"""Asset access: read, replace and rebuild Unity bundles with UnityPy.

This is the backbone of "regular" modding (立绘/贴图/CG/BGM/文本替换). It never touches the bridge:
the game loads bundles from disk, so the work is purely Python - which is also why it keeps working
when the kernel is busy or the game is not running.

UnityPy is an *optional* dependency: importing this module is free, and using it without UnityPy
raises a message telling you exactly what to install.

    from limbuspy.game import assets

    bundle = assets.open("...static_s1_0_assets_all_xxx.bundle")
    bundle.objects                                   # {type: count}
    json_assets = bundle.find(type="TextAsset")       # selector search
    raw = bundle.export(json_assets[0])               # bytes

    patch = assets.Patch()
    patch.replace("TextAsset", "SkillData", new_bytes)
    patch.apply(bundle)
    bundle.save("mods/my_mod/patch.bundle")           # a bundle the game can load
"""

from __future__ import annotations

import io
import os
import re
from typing import Any, Dict, Iterable, List, Optional

__all__ = ["Asset", "Bundle", "Patch", "open", "available", "unity_version"]

#: Limbus builds on Unity 6; bundles carry this in their header, but a freshly stripped bundle may
#: not, so UnityPy needs the fallback (see AGENTS.md).
FALLBACK_UNITY_VERSION = "6000.3.12f1"


def unity_version() -> str:
    return FALLBACK_UNITY_VERSION


def _unitypy():
    """Imports UnityPy with the fallback version configured, or raises an actionable error."""
    try:
        import UnityPy  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise ModuleNotFoundError(
            "UnityPy is required for asset work: pip install UnityPy "
            "(only asset extraction/replacement needs it; scripts and the kernel do not)"
        ) from exc
    try:
        UnityPy.config.FALLBACK_UNITY_VERSION = FALLBACK_UNITY_VERSION
    except Exception:  # noqa: BLE001 - older/newer UnityPy may not expose the attribute
        pass
    return UnityPy


def available() -> bool:
    """True when UnityPy can be imported (asset work possible)."""
    try:
        _unitypy()
        return True
    except ModuleNotFoundError:
        return False


class Asset:
    """One object inside a bundle."""

    __slots__ = ("bundle", "object")

    def __init__(self, bundle: "Bundle", obj: Any) -> None:
        self.bundle = bundle
        self.object = obj

    @property
    def type(self) -> str:
        return str(getattr(self.object.type, "name", self.object.type))

    @property
    def path_id(self) -> int:
        return int(getattr(self.object, "path_id", 0))

    def read(self) -> Any:
        """The UnityPy typed object (``TextAsset``, ``Texture2D``, ``AudioClip``, ...)."""
        return self.object.read()

    @property
    def name(self) -> str:
        try:
            data = self.read()
        except Exception:  # noqa: BLE001 - some objects refuse to deserialize
            return "path_%d" % self.path_id
        for attribute in ("m_Name", "name"):
            value = getattr(data, attribute, None)
            if isinstance(value, str) and value:
                return value
        return "path_%d" % self.path_id

    def export(self) -> bytes:
        """Raw bytes of the asset (TextAsset script payload or sprite/texture PNG)."""
        data = self.read()
        payload = getattr(data, "m_Script", None)
        if payload is None:
            payload = getattr(data, "script", None)
        if payload is not None:
            return payload.encode("utf-8", "surrogateescape") if isinstance(payload, str) else bytes(payload)

        raw = getattr(data, "image", None)  # PIL image for Texture2D/Sprite
        if raw is not None:
            buffer = io.BytesIO()
            raw.save(buffer, format="PNG")
            return buffer.getvalue()

        for attribute in ("m_AudioData", "audio_data", "m_Text"):
            value = getattr(data, attribute, None)
            if isinstance(value, (bytes, bytearray)):
                return bytes(value)
        raise ValueError("asset %s (%s) has no exportable payload" % (self.name, self.type))

    def __repr__(self) -> str:  # pragma: no cover
        return "<Asset %s %s>" % (self.type, self.name)


class Bundle:
    """An opened bundle; mutations stay in memory until :meth:`save`."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._unitypy = _unitypy()
        self.environment = self._unitypy.load(path)
        self._assets: List[Asset] = [Asset(self, obj) for obj in self.environment.objects]

    # -- inspection --------------------------------------------------------------------

    @property
    def assets(self) -> List[Asset]:
        return list(self._assets)

    @property
    def objects(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for asset in self._assets:
            counts[asset.type] = counts.get(asset.type, 0) + 1
        return counts

    def types(self) -> List[str]:
        return sorted(self.objects)

    def find(self, type: Optional[str] = None, name: Optional[str] = None,
             pattern: Optional[str] = None) -> List[Asset]:
        """Selects assets by type, exact name, or a regex over the name."""
        matcher = re.compile(pattern) if pattern else None
        found = []
        for asset in self._assets:
            if type is not None and asset.type.lower() != type.lower():
                continue
            if name is None and matcher is None:
                found.append(asset)
                continue
            asset_name = asset.name
            if name is not None and asset_name != name:
                continue
            if matcher is not None and not matcher.search(asset_name):
                continue
            found.append(asset)
        return found

    def get(self, type: str, name: str) -> Optional[Asset]:
        matches = self.find(type=type, name=name)
        return matches[0] if matches else None

    def export(self, asset: Asset) -> bytes:
        return asset.export()

    # -- modification ------------------------------------------------------------------

    def replace_text(self, asset: Asset, data: bytes) -> None:
        """Replaces a TextAsset payload (the common case: data tables, localisation)."""
        if not isinstance(data, (bytes, bytearray)):
            data = str(data).encode("utf-8")
        target = asset.read()
        if hasattr(target, "m_Script"):
            target.m_Script = bytes(data).decode("utf-8", "surrogateescape")
        elif hasattr(target, "script"):
            target.script = bytes(data).decode("utf-8", "surrogateescape")
        else:
            raise TypeError("%s (%s) is not a text asset" % (asset.name, asset.type))
        if hasattr(target, "save"):
            target.save()

    def replace_image(self, asset: Asset, image_path: str) -> None:
        """Replaces a Texture2D/Sprite image from a PNG/JPG file (needs Pillow for the source)."""
        try:
            from PIL import Image  # noqa: PLC0415
        except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
            raise ModuleNotFoundError("Pillow is required to load replacement images: pip install Pillow") from exc

        target = asset.read()
        if not hasattr(target, "image"):
            raise TypeError("%s (%s) has no image" % (asset.name, asset.type))
        target.image = Image.open(image_path).convert("RGBA")
        if hasattr(target, "save"):
            target.save()

    def save(self, path: Optional[str] = None) -> str:
        """Writes the (modified) bundle. ``path`` defaults to overwriting the source."""
        target = path or self.path
        data = self._serialize()
        os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
        with io.open(target, "wb") as handle:
            handle.write(data)
        return target

    def _serialize(self) -> bytes:
        environment = self.environment
        for attribute in ("file", "files"):
            holder = getattr(environment, attribute, None)
            if holder is None:
                continue
            saver = getattr(holder, "save", None)
            if callable(saver):
                payload = saver()
                if isinstance(payload, (bytes, bytearray)):
                    return bytes(payload)
                if isinstance(payload, dict):
                    # Multi-file bundles: concatenate is wrong; take the main file.
                    for value in payload.values():
                        if isinstance(value, (bytes, bytearray)):
                            return bytes(value)
        raise RuntimeError("this UnityPy build cannot serialise the bundle")

    def __len__(self) -> int:
        return len(self._assets)

    def __repr__(self) -> str:  # pragma: no cover
        return "<Bundle %s: %d asset(s)>" % (os.path.basename(self.path), len(self._assets))


class Patch:
    """A list of asset replacements, applied to a bundle in one go."""

    def __init__(self) -> None:
        self.entries: List[dict] = []

    def replace(self, type: str, name: str, data: bytes) -> "Patch":
        self.entries.append({"op": "text", "type": type, "name": name, "data": data})
        return self

    def replace_image(self, type: str, name: str, image_path: str) -> "Patch":
        self.entries.append({"op": "image", "type": type, "name": name, "path": image_path})
        return self

    def apply(self, bundle: Bundle) -> Dict[str, int]:
        """Applies every entry; returns ``{"applied": n, "missing": m}``."""
        applied = 0
        missing = 0
        for entry in self.entries:
            asset = bundle.get(entry["type"], entry["name"])
            if asset is None:
                missing += 1
                continue
            if entry["op"] == "text":
                bundle.replace_text(asset, entry["data"])
            else:
                bundle.replace_image(asset, entry["path"])
            applied += 1
        return {"applied": applied, "missing": missing}

    def __len__(self) -> int:
        return len(self.entries)


def open(path: str) -> Bundle:
    """Opens a bundle for reading/patching."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return Bundle(path)


def catalog_lookup(catalog: str, pattern: str, limit: int = 20) -> List[str]:
    """Resolves bundle file names from an Addressables catalog (delegates to tools/parse_catalog.py)."""
    tools = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "tools"))
    if not os.path.isdir(tools):
        raise RuntimeError("tools/ not found next to the package: %s" % tools)
    import sys

    if tools not in sys.path:
        sys.path.insert(0, tools)
    import parse_catalog  # noqa: PLC0415

    matcher = re.compile(pattern)
    found: List[str] = []
    catalog_data = parse_catalog.load_catalog(catalog)  # type: ignore[attr-defined]
    for key, bundle in (catalog_data or {}).items():
        if matcher.search(str(key)):
            found.append(str(bundle))
    return sorted(set(found))[:limit]
