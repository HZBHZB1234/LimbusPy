"""Public mod-authoring API.

    from limbuspy.mod import Mod, hook, expose, config

Everything here is stable surface: if it changes, mods break, so it gets a deprecation cycle.
"""

from __future__ import annotations

from .base import Mod
from .context import Context
from .decorators import (
    ConfigField,
    ConfigSpec,
    HookSpec,
    acquirer,
    config,
    consequence,
    expose,
    hook,
    lua_function,
)
from .loader import LoadedMod, ManifestError, ModManifest, discover, resolve_order
from .registry import HookEntry, Registry
from .runtime import ModRuntime, ReloadResult
from .watch import ModWatcher

__all__ = [
    "Mod",
    "Context",
    "ModRuntime",
    "ReloadResult",
    "Registry",
    "HookEntry",
    "ConfigSpec",
    "ConfigField",
    "HookSpec",
    "hook",
    "expose",
    "consequence",
    "acquirer",
    "lua_function",
    "config",
    "ModManifest",
    "ManifestError",
    "LoadedMod",
    "ModWatcher",
    "discover",
    "resolve_order",
]
