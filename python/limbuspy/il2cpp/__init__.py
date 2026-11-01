"""Access to the generated IL2CPP index (docs/03-architecture.md §7).

The index is produced by ``tools/gen_limbuspy_stubs.py`` from ``dump.cs`` and lives in
``limbuspy/_gen/<build>/``. It is *generated*, gitignored and optional: everything that needs it
degrades to "unknown" when it is missing, because the runtime resolves everything through
reflection anyway.

Why an index at all:
  * IDE hints / autocomplete (``types.pyi``, ``enums.py``);
  * capability probes: "does this build still have BattleUnitModel.Hp?" (``caps.json``);
  * version-drift reports for ``limbuspy doctor``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

__all__ = [
    "GEN_ROOT",
    "available_builds",
    "current_build",
    "TypeIndex",
    "default_index",
    "enums_module",
    "reset_cache",
]

GEN_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_gen"
)

_BUILD_CACHE: Dict[str, "TypeIndex"] = {}
_ENUM_CACHE: Dict[str, Any] = {}


def available_builds(root: Optional[str] = None) -> List[str]:
    """Build ids that have a generated index, newest first."""
    root = root or GEN_ROOT
    if not os.path.isdir(root):
        return []
    builds = []
    for entry in os.listdir(root):
        directory = os.path.join(root, entry)
        if not os.path.isdir(directory):
            continue
        if not os.path.isfile(os.path.join(directory, "methods.json")):
            continue
        builds.append((os.path.getmtime(directory), entry))
    builds.sort(reverse=True)
    return [entry for _stamp, entry in builds]


def current_build(root: Optional[str] = None) -> Optional[str]:
    """The build the kernel expects: ``LIMBUSPY_BUILD`` env, else the newest generated index."""
    explicit = os.environ.get("LIMBUSPY_BUILD")
    if explicit:
        return explicit
    builds = available_builds(root)
    return builds[0] if builds else None


class TypeIndex:
    """Read-only view of one build's ``methods.json`` (+ ``caps.json`` / ``manifest.json``)."""

    def __init__(self, build: str, root: Optional[str] = None) -> None:
        self.build = build
        self.root = root or GEN_ROOT
        self.directory = os.path.join(self.root, build)
        self._types: Optional[Dict[str, dict]] = None
        self._manifest: Optional[dict] = None
        self._caps: Optional[dict] = None

    # -- loading -----------------------------------------------------------------------

    @property
    def exists(self) -> bool:
        return os.path.isfile(os.path.join(self.directory, "methods.json"))

    def _load(self) -> Dict[str, dict]:
        if self._types is None:
            path = os.path.join(self.directory, "methods.json")
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            types = payload.get("types") if isinstance(payload, dict) else None
            self._types = types if isinstance(types, dict) else {}
        return self._types

    @property
    def types(self) -> Dict[str, dict]:
        return self._load()

    @property
    def manifest(self) -> dict:
        if self._manifest is None:
            path = os.path.join(self.directory, "manifest.json")
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    self._manifest = json.load(handle)
            except (OSError, ValueError):
                self._manifest = {}
        return self._manifest

    @property
    def caps(self) -> dict:
        if self._caps is None:
            path = os.path.join(self.directory, "caps.json")
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    self._caps = json.load(handle)
            except (OSError, ValueError):
                self._caps = {}
        return self._caps

    # -- queries -----------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.types)

    def has_type(self, name: str) -> bool:
        return name in self.types

    def type_names(self, pattern: Optional[str] = None, limit: int = 100) -> List[str]:
        if not pattern:
            return sorted(self.types)[:limit]
        try:
            matcher = re.compile(pattern)
        except re.error:
            matcher = re.compile(re.escape(pattern))
        found = [name for name in self.types if matcher.search(name)]
        return found[:limit]

    def describe(self, type_name: str) -> Optional[dict]:
        record = self.types.get(type_name)
        if record is None:
            return None
        described = {
            "type": type_name,
            "kind": record.get("kind"),
            "namespace": record.get("namespace"),
            "image": record.get("image"),
            "bases": record.get("bases") or [],
            "properties": [prop.get("name") for prop in record.get("properties") or []],
            "fields": [field.get("name") for field in record.get("fields") or []],
            "methods": [method.get("name") for method in record.get("methods") or []],
        }
        return described

    def has_member(self, type_name: str, member: str) -> bool:
        record = self.types.get(type_name)
        if record is None:
            return False
        for group in ("properties", "fields", "methods"):
            for entry in record.get(group) or []:
                if entry.get("name") == member:
                    return True
        return False

    def member(self, type_name: str, member: str) -> Optional[dict]:
        """Returns ``{"kind": ..., **signature}`` for a member, or None."""
        record = self.types.get(type_name)
        if record is None:
            return None
        for group, kind in (("properties", "property"), ("fields", "field"), ("methods", "method")):
            for entry in record.get(group) or []:
                if entry.get("name") == member:
                    described = {"kind": kind, "type": type_name}
                    described.update(entry)
                    return described
        return None

    def find_members(self, pattern: str, limit: int = 50) -> List[dict]:
        """Searches member names across every type (REPL / discovery helper)."""
        try:
            matcher = re.compile(pattern, re.IGNORECASE)
        except re.error:
            matcher = re.compile(re.escape(pattern), re.IGNORECASE)
        found: List[dict] = []
        for type_name, record in self.types.items():
            for group, kind in (("properties", "property"), ("fields", "field"), ("methods", "method")):
                for entry in record.get(group) or []:
                    if matcher.search(str(entry.get("name") or "")):
                        found.append({"type": type_name, "kind": kind,
                                      "name": entry.get("name"),
                                      "signature": entry.get("ret") or entry.get("type")})
                        if len(found) >= limit:
                            return found
        return found


def default_index(build: Optional[str] = None) -> Optional[TypeIndex]:
    """Returns the index for ``build`` (or the current build), or None when nothing is generated."""
    build = build or current_build()
    if not build:
        return None
    if build in _BUILD_CACHE:
        return _BUILD_CACHE[build]
    index = TypeIndex(build)
    if not index.exists:
        return None
    _BUILD_CACHE[build] = index
    return index


def enums_module(build: Optional[str] = None):
    """Imports the generated ``enums.py`` for a build (raises ImportError when missing)."""
    import importlib.util

    build = build or current_build()
    if not build:
        raise ImportError("no generated build available; run tools/gen_limbuspy_stubs.py")
    if build in _ENUM_CACHE:
        return _ENUM_CACHE[build]

    path = os.path.join(GEN_ROOT, build, "enums.py")
    if not os.path.isfile(path):
        raise ImportError("no enums for build %r (%s)" % (build, path))

    name = "limbuspy._gen_enums_" + re.sub(r"\W", "_", build)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    _ENUM_CACHE[build] = module
    return module


def reset_cache() -> None:
    """Drops cached indexes/enums (used by doctor after a regeneration)."""
    _BUILD_CACHE.clear()
    _ENUM_CACHE.clear()
