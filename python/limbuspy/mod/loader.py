"""Discovery, import, dependency ordering and registration of mods.

Conventions (docs/02-mod-authoring.md):

    mods/<name>/__init__.py     required - the package entry point
    mods/<name>/mod.toml        optional - manifest (name/version/requires/...)
    mods/<name>/**              anything else the mod wants

Nothing here touches the game: the loader only imports Python and fills the registry, which is
what makes it testable against the mock kernel.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .base import Mod
from .decorators import ConfigSpec, ExposeSpec, HookSpec
from .registry import ExposedEntry, HookEntry, Registry

__all__ = ["ModManifest", "ManifestError", "discover", "resolve_order", "LoadedMod", "load_mod",
           "register_instance", "unload_module"]


class ManifestError(Exception):
    """Dependency or manifest problem that must be reported, not guessed at."""


@dataclass
class ModManifest:
    name: str
    directory: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    requires: List[str] = field(default_factory=list)
    entry: Optional[str] = None

    @property
    def module_name(self) -> str:
        return "limbuspy_mods." + self.name.replace("-", "_").replace(".", "_")

    def __repr__(self) -> str:  # pragma: no cover
        return "<ModManifest %s %s>" % (self.name, self.version)


@dataclass
class LoadedMod:
    manifest: ModManifest
    instance: Mod
    module: Any
    hooks: List[HookEntry] = field(default_factory=list)
    exposed: List[ExposedEntry] = field(default_factory=list)
    contents: List[Any] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------------------


def _read_manifest(directory: str, fallback_name: str) -> Dict[str, Any]:
    toml_path = os.path.join(directory, "mod.toml")
    json_path = os.path.join(directory, "mod.json")

    data: Dict[str, Any] = {}
    if os.path.isfile(toml_path):
        try:
            import tomllib  # Python 3.11+
        except ModuleNotFoundError:  # pragma: no cover - 3.10 and older
            tomllib = None
        if tomllib is not None:
            with open(toml_path, "rb") as handle:
                data = tomllib.load(handle)
        else:
            data = {"_warning": "mod.toml ignored: tomllib unavailable (use mod.json)"}
    elif os.path.isfile(json_path):
        with open(json_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)

    if not isinstance(data, dict):
        raise ManifestError("%s: manifest must be a table/object" % directory)

    data.setdefault("name", fallback_name)
    return data


def discover(mods_dir: str) -> List[ModManifest]:
    """Finds every mod package under ``mods_dir`` (skips ``_``/``.`` prefixed entries)."""
    manifests: List[ModManifest] = []
    if not os.path.isdir(mods_dir):
        return manifests

    for entry in sorted(os.listdir(mods_dir)):
        if entry.startswith("_") or entry.startswith("."):
            continue
        directory = os.path.join(mods_dir, entry)
        if not os.path.isdir(directory):
            continue
        if not os.path.isfile(os.path.join(directory, "__init__.py")):
            continue

        try:
            data = _read_manifest(directory, entry)
        except (ManifestError, ValueError, OSError) as exc:
            raise ManifestError("%s: %s" % (entry, exc))

        requires = data.get("requires") or []
        if isinstance(requires, str):
            requires = [requires]

        manifests.append(ModManifest(
            name=str(data.get("name") or entry),
            directory=directory,
            version=str(data.get("version") or "0.0.0"),
            description=str(data.get("description") or ""),
            author=str(data.get("author") or ""),
            requires=[str(item) for item in requires],
            entry=data.get("entry"),
        ))

    names = [manifest.name for manifest in manifests]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise ManifestError("duplicate mod names: %s" % ", ".join(sorted(duplicates)))

    return manifests


def _parse_requirement(text: str) -> Tuple[str, Optional[str]]:
    for separator in (">=", "==", ">"):
        if separator in text:
            name, version = text.split(separator, 1)
            return name.strip(), version.strip()
    return text.strip(), None


def resolve_order(manifests: List[ModManifest]) -> List[ModManifest]:
    """Topological order (dependencies first). Raises on missing deps and cycles."""
    by_name = {manifest.name: manifest for manifest in manifests}
    order: List[ModManifest] = []
    visiting: List[str] = []
    done: set = set()

    def visit(name: str, chain: List[str]) -> None:
        if name in done:
            return
        if name in chain:
            raise ManifestError("dependency cycle: %s" % " -> ".join(chain + [name]))
        manifest = by_name.get(name)
        if manifest is None:
            raise ManifestError("missing dependency %r (required by %s)" % (name, chain[-1] if chain else "?"))

        chain = chain + [name]
        for requirement in manifest.requires:
            dependency, _version = _parse_requirement(requirement)
            visit(dependency, chain)

        done.add(name)
        order.append(manifest)

    for manifest in manifests:
        visit(manifest.name, [])
    return order


# --------------------------------------------------------------------------------------
# importing
# --------------------------------------------------------------------------------------


def _ensure_package_parent() -> None:
    if "limbuspy_mods" not in sys.modules:
        package = importlib.util.module_from_spec(
            importlib.util.spec_from_loader("limbuspy_mods", loader=None, is_package=True)
        )
        package.__path__ = []  # type: ignore[attr-defined]
        sys.modules["limbuspy_mods"] = package


def _purge_bytecode(directory: str) -> None:
    """Deletes `__pycache__` under a mod so the next import recompiles the source.

    CPython validates a `.pyc` against (source mtime in whole seconds, source size). A hot
    reload that changes behaviour without changing size (`"v1"` -> `"v2"`) inside the same
    second would silently reuse stale bytecode - the classic reason a "hot reload" works in the
    editor and not in-game. Removing the cache removes the ambiguity.
    """
    for root, directories, _files in os.walk(directory):
        for name in list(directories):
            if name != "__pycache__":
                continue
            try:
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
            except OSError:
                pass
            directories.remove(name)


def import_mod(manifest: ModManifest):
    """Imports (or re-imports) the mod package, bypassing any cached bytecode."""
    importlib.invalidate_caches()
    _purge_bytecode(manifest.directory)
    _ensure_package_parent()

    init_path = manifest.entry or os.path.join(manifest.directory, "__init__.py")
    name = manifest.module_name

    spec = importlib.util.spec_from_file_location(
        name,
        init_path,
        submodule_search_locations=[manifest.directory],
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot build an import spec for %s" % init_path)

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def unload_module(manifest: ModManifest) -> None:
    prefix = manifest.module_name
    for key in [key for key in sys.modules if key == prefix or key.startswith(prefix + ".")]:
        sys.modules.pop(key, None)


def instantiate_mod(module, manifest: ModManifest) -> Mod:
    candidates = []
    for attribute in dir(module):
        value = getattr(module, attribute)
        if not isinstance(value, type):
            continue
        if not issubclass(value, Mod) or value is Mod:
            continue
        if value.__module__ != module.__name__:
            continue
        candidates.append(value)

    if not candidates:
        # ``Mod`` 子类从「入场券」降级为「可选道具」：无状态的模组只要在模块顶层用
        # ``@lp.on_hit`` 之类的门面注册就够了，不需要写类。这里造一个裸实例来承载
        # 清单信息（配置、状态目录、卸载钩子仍然有地方挂）。
        instance = Mod()
        instance.name = manifest.name
        instance.version = manifest.version or instance.version
        instance.description = instance.description or manifest.description
        instance.author = instance.author or manifest.author
        instance.requires = list(instance.requires or manifest.requires or [])
        return instance

    # Prefer the most derived class when several are exported.
    candidates.sort(key=lambda cls: len(cls.__mro__), reverse=True)
    instance = candidates[0]()
    if not instance.name:
        instance.name = manifest.name
    if not instance.version or instance.version == "0.0.0":
        instance.version = manifest.version
    if not instance.description:
        instance.description = manifest.description
    if not instance.author:
        instance.author = manifest.author
    if manifest.requires and not instance.requires:
        instance.requires = list(manifest.requires)
    return instance


# --------------------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------------------

#: _compile_when 的"拒绝"标记（与"没有谓词"的 None 区分开）。
_REJECTED = object()


def _compile_when(spec, instance, mod_name: str):
    """把 ``@on(..., when=...)`` 的谓词编译成闭包；失败时**只丢这个钩子**。

    为什么要在这里编译而不是每次回调都解释：谓词求值在游戏主线程的补丁路径上，
    每帧可能几十次。注册期编译一次，运行期就是一次普通函数调用。

    拒绝需要跨进程往返的谓词（除非显式 ``allow_rpc=True``）—— 那是这个框架
    唯一的性能退化防线。
    """
    when = getattr(spec, "when", None)
    if when is None:
        return None
    try:
        from ..registration import compile_predicate

        return compile_predicate(when, allow_rpc=bool(getattr(spec, "allow_rpc", False)))
    except Exception as exc:  # noqa: BLE001 - 一个坏谓词不该拖垮整个 mod
        problems = getattr(instance, "registration_errors", None)
        if problems is not None:
            problems.append("%s: %s" % (mod_name, exc))
        return _REJECTED


def collect_contents(module, mod_name: str):
    """收集模块里声明的 ``Content`` 子类，回填 ``owner`` 与位序号。

    位序号（``bit``）是"零往返 buff 判断"的核心：注册期分配一次，之后
    C# 侧按同一份顺序算位图，Python 侧只做按位与。

    **只有 Buff 占位**：位图装的是"某个 Buff 关键字在不在"，被动/技能不参与。
    这样 ``bit`` 恒等于 ``watch`` 里的下标（见 :func:`..registration.plan_for`）——
    如果连被动一起编号，``watch`` 里就会出现空位，谓词判断会整体错一位。

    Returns:
        ``([content_cls...], [problem...])``
    """
    from ..registration import Content

    contents = []
    problems = []
    seen = set()
    buffs = 0
    try:
        candidates = list(vars(module).items())
    except TypeError:  # pragma: no cover - 非模块对象
        return [], []

    for name, value in candidates:
        if not isinstance(value, type) or not issubclass(value, Content) or value is Content:
            continue
        if value.__module__ != getattr(module, "__name__", None):
            continue
        if name in seen:
            continue
        seen.add(name)

        found = value.validate()
        if found:
            problems.extend(found)
            continue

        value.owner = mod_name
        if str(getattr(value, "kind", "")) == "buff":
            value.bit = buffs
            buffs += 1
        else:
            value.bit = -1
        contents.append(value)

    return contents, problems


def _iter_decorated(cls):
    """Yields (attribute_name, function, decorator_payload) across the MRO, base first.

    Derived overrides win because we walk from the base to the derived class and keep the last
    definition of each name.
    """
    collected: Dict[str, Tuple[Any, Any]] = {}
    for klass in reversed(cls.__mro__):
        for attribute, value in vars(klass).items():
            if not callable(value):
                continue
            payload = getattr(value, "__limbuspy_hook__", None) or getattr(value, "__limbuspy_expose__", None)
            if payload is None:
                continue
            collected[attribute] = (value, payload)
    for attribute in sorted(collected):
        function, payload = collected[attribute]
        yield attribute, function, payload


def _iter_module_decorated(module):
    """Yields (name, function, decorator_payload) for module-level decorated callables.

    没有这一层，``@lp.on_hit`` 就只能写在类体里 —— 而"十行的 hello world"恰恰是
    门面最该支持的形状（见 ``python/examples/hello_passive``）。类对象会被跳过，
    所以 ``Mod`` 子类的成员不会被重复注册。
    """
    try:
        items = list(vars(module).items())
    except TypeError:  # pragma: no cover - 非模块对象
        return
    for name, value in items:
        if not callable(value) or isinstance(value, type):
            continue
        payload = getattr(value, "__limbuspy_hook__", None) or getattr(value, "__limbuspy_expose__", None)
        if payload is None:
            continue
        yield name, value, payload


def register_instance(instance: Mod, mod_name: str, registry: Registry, gen: int,
                      module=None) -> LoadedMod:
    """Fills the registry with everything a mod instance publishes."""
    manifest = ModManifest(name=mod_name, directory="")
    loaded = LoadedMod(manifest=manifest, instance=instance, module=None)

    for attribute, function, payload in _iter_decorated(type(instance)):
        bound = getattr(instance, attribute)

        if isinstance(payload, HookSpec):
            composed = _compile_when(payload, instance, mod_name)
            if composed is _REJECTED:
                continue
            entry = HookEntry(
                mod=mod_name,
                name=attribute,
                timing=payload.timing,
                fn=bound,
                gen=gen,
                priority=payload.priority,
                filters=payload.filters,
                sync=payload.sync,
                fallback=payload.fallback,
                when=composed,
                pred=payload.when,
            )
            registry.add_hook(entry)
            loaded.hooks.append(entry)
            if payload.sync:
                # Synchronous hooks must also be reachable by name for the kernel's SYNCALL lane.
                exposed = ExposedEntry(mod=mod_name, name="%s.%s" % (mod_name, attribute),
                                       fn=bound, gen=gen, fallback=payload.fallback)
                registry.expose(exposed)
                loaded.exposed.append(exposed)

        elif isinstance(payload, ExposeSpec):
            entry = ExposedEntry(
                mod=mod_name,
                name=payload.name,
                fn=bound,
                gen=gen,
                fallback=payload.fallback,
            )
            registry.expose(entry)
            loaded.exposed.append(entry)
            if payload.table:
                registry.add_table_entry(payload.table, entry)

    instance.name = instance.name or mod_name

    # 内容类（Passive / Buff ...）上也可以挂 ``@on_*`` —— 它们是「被动」的载体。
    # 这些处理器不属于 Mod 子类，所以必须单独扫一遍，否则 Passive 里的回调会静默丢失。
    for content_cls in getattr(instance, "contents", []) or []:
        try:
            content = content_cls()
        except Exception as exc:  # noqa: BLE001 - 内容实例化失败只影响它自己
            instance.registration_errors.append(
                "%s: 无法实例化 %s: %s" % (mod_name, content_cls.__name__, exc))
            continue

        content_uid = content_cls.uid() or content_cls.__name__
        for attribute, function, payload in _iter_decorated(content_cls):
            if not isinstance(payload, HookSpec):
                continue
            composed = _compile_when(payload, instance, mod_name)
            if composed is _REJECTED:
                continue
            name = "%s.%s" % (content_uid, attribute)
            entry = HookEntry(
                mod=mod_name,
                name=name,
                timing=payload.timing,
                fn=getattr(content, attribute),
                gen=gen,
                priority=payload.priority,
                filters=payload.filters,
                sync=payload.sync,
                fallback=payload.fallback,
                when=composed,
                pred=payload.when,
            )
            registry.add_hook(entry)
            loaded.hooks.append(entry)
            if payload.sync:
                exposed = ExposedEntry(mod=mod_name, name="%s.%s" % (mod_name, name),
                                       fn=getattr(content, attribute), gen=gen,
                                       fallback=payload.fallback)
                registry.expose(exposed)
                loaded.exposed.append(exposed)

    # 模块顶层声明的 ``@lp.on_hit`` / ``@hook`` / ``@expose``。
    # 它们的 fn 已经是"只吃 ctx"的形态（``@on`` 的 wrapper 自己能分辨），不需要绑定。
    if module is not None:
        for name, function, payload in _iter_module_decorated(module):
            if isinstance(payload, HookSpec):
                composed = _compile_when(payload, instance, mod_name)
                if composed is _REJECTED:
                    continue
                entry = HookEntry(
                    mod=mod_name,
                    name="%s.%s" % (mod_name, name),
                    timing=payload.timing,
                    fn=function,
                    gen=gen,
                    priority=payload.priority,
                    filters=payload.filters,
                    sync=payload.sync,
                    fallback=payload.fallback,
                    when=composed,
                    pred=payload.when,
                )
                registry.add_hook(entry)
                loaded.hooks.append(entry)
                if payload.sync:
                    exposed = ExposedEntry(mod=mod_name, name="%s.%s" % (mod_name, name),
                                           fn=function, gen=gen, fallback=payload.fallback)
                    registry.expose(exposed)
                    loaded.exposed.append(exposed)
            elif isinstance(payload, ExposeSpec):
                entry = ExposedEntry(mod=mod_name, name=payload.name, fn=function, gen=gen,
                                     fallback=payload.fallback)
                registry.expose(entry)
                loaded.exposed.append(entry)
                if payload.table:
                    registry.add_table_entry(payload.table, entry)

    return loaded


def load_mod(manifest: ModManifest, registry: Registry, bridge=None, logger=None) -> LoadedMod:
    """Imports + instantiates + registers one mod at the registry's current generation."""
    module = import_mod(manifest)
    try:
        instance = instantiate_mod(module, manifest)
    except Exception:
        unload_module(manifest)
        raise

    instance.bridge = bridge
    instance.logger = logger

    # 内容注册（Buff / Passive / ...）：收集 → 回填 owner/bit → 交给 runtime 发布计划。
    # 放在 register_instance 之前，因为钩子谓词要用到内容分配好的位序号。
    contents, problems = collect_contents(module, manifest.name)
    instance.contents = contents
    instance.registration_errors = list(getattr(instance, "registration_errors", []) or []) + problems

    gen = registry.generation(manifest.name)
    loaded = register_instance(instance, manifest.name, registry, gen, module=module)
    loaded.contents = contents
    loaded.module = module
    loaded.manifest = manifest
    return loaded


def load_config(instance: Mod, state_dir: str) -> dict:
    """Reads persisted config/state for a mod. Returns ``{"config": {...}, "state": {...}}``."""
    path = os.path.join(state_dir, instance.name + ".json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(instance: Mod, state_dir: str, extra_state: Optional[dict] = None) -> None:
    path = os.path.join(state_dir, instance.name + ".json")
    payload: Dict[str, Any] = {}
    if isinstance(instance.cfg, ConfigSpec):
        payload["config"] = instance.cfg.as_dict()
    if extra_state:
        payload["state"] = extra_state
    try:
        os.makedirs(state_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass  # state persistence is best effort


@dataclass
class PreparedMods:
    """Result of importing every mod once, before any registration."""

    manifests: List[ModManifest] = field(default_factory=list)
    imported: List[tuple] = field(default_factory=list)      # (manifest, module, instance)
    errors: Dict[str, str] = field(default_factory=dict)


def prepare(mods_dir: str) -> PreparedMods:
    """Discovers and imports mods so code-declared requirements join dependency ordering.

    Importing mutates ``sys.modules``; callers that do not intend to register the mods (for
    example the doctor) should call :func:`unload_module` for every prepared manifest afterwards.
    """
    result = PreparedMods()
    result.manifests = discover(mods_dir)

    for manifest in result.manifests:
        try:
            module = import_mod(manifest)
            instance = instantiate_mod(module, manifest)
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            result.errors[manifest.name] = describe_failure(exc)
            unload_module(manifest)
            continue

        if instance.requires:
            merged = list(manifest.requires)
            for requirement in instance.requires:
                if requirement not in merged:
                    merged.append(requirement)
            manifest.requires = merged

        result.imported.append((manifest, module, instance))
    return result


def describe_failure(exc: BaseException) -> str:
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()

