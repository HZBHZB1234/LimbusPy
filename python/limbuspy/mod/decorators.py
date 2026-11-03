"""Decorators a mod author uses.

Declarative on purpose: the loader reads these attributes off the classes/objects it imports, so
registration never depends on import side effects and a reload can rebuild the whole table from
source without stale entries.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

__all__ = [
    "hook",
    "expose",
    "consequence",
    "acquirer",
    "lua_function",
    "config",
    "HookSpec",
    "ExposeSpec",
    "ConfigSpec",
    "ConfigField",
]


class HookSpec:
    """Metadata attached to a ``@hook`` function."""

    __slots__ = ("timing", "filters", "priority", "sync", "fallback", "when", "allow_rpc")

    def __init__(self, timing: str, filters: dict, priority: int, sync: bool, fallback: Any,
                 when: Any = None, allow_rpc: bool = False) -> None:
        self.timing = timing
        self.filters = filters
        self.priority = priority
        self.sync = sync
        self.fallback = fallback
        #: 声明式谓词（``limbuspy.registration.Pred``）。注册期编译成闭包，运行期零开销。
        self.when = when
        #: 是否允许 ``when`` 里出现需要跨进程往返的谓词（默认拒绝）。
        self.allow_rpc = allow_rpc


class ExposeSpec:
    """Metadata attached to ``@expose`` / ``@consequence`` / ``@acquirer`` / ``@lua_function``."""

    __slots__ = ("name", "table", "fallback")

    def __init__(self, name: str, table: Optional[str], fallback: Any) -> None:
        self.name = name
        self.table = table
        self.fallback = fallback


def hook(timing: str, heads: Optional[bool] = None, tails: Optional[bool] = None,
         crit: Optional[bool] = None, keyword: Optional[str] = None, priority: int = 0,
         sync: bool = False, fallback: Any = None):
    """Declares an event hook.

    ``timing`` uses Modular's timing names (``"OnSucceedAttack"``, ``"WhenUse"``, ...), so existing
    knowledge transfers. ``heads``/``tails``/``crit``/``keyword`` mirror Modular's
    ``TIMING:OnSucceedAttack(Head)`` filters.

    ``sync=True`` marks a hook that must return a value to the kernel *immediately* (damage
    modifiers). That lane is deliberately small: it blocks the game's main thread for the round
    trip, so prefer side effects.
    """
    filters = {}
    if heads is not None:
        filters["head"] = bool(heads)
        if heads:
            filters["tail"] = False
    if tails is not None:
        filters["tail"] = bool(tails)
        if tails:
            filters["head"] = False
    if crit is not None:
        filters["crit"] = bool(crit)
    if keyword is not None:
        filters["keyword"] = keyword

    def decorate(fn: Callable) -> Callable:
        setattr(fn, "__limbuspy_hook__", HookSpec(timing, filters, priority, sync, fallback))
        return fn

    return decorate


def expose(name: Optional[str] = None, fallback: Any = None):
    """Makes a function callable from the kernel by name (``SYNCALL``), e.g. Modular's ``py()``."""

    def decorate(fn: Callable) -> Callable:
        setattr(fn, "__limbuspy_expose__", ExposeSpec(name or fn.__name__, None, fallback))
        return fn

    return decorate


def consequence(name: Optional[str] = None):
    """Registers the function into Modular's ``consequenceDict`` (fills the ``py()`` bridge)."""

    def decorate(fn: Callable) -> Callable:
        setattr(fn, "__limbuspy_expose__", ExposeSpec(name or fn.__name__, "consequence", None))
        return fn

    return decorate


def acquirer(name: Optional[str] = None):
    """Registers the function into Modular's ``acquirerDict``."""

    def decorate(fn: Callable) -> Callable:
        setattr(fn, "__limbuspy_expose__", ExposeSpec(name or fn.__name__, "acquirer", None))
        return fn

    return decorate


def lua_function(name: Optional[str] = None):
    """Registers the function into Modular's ``luaFunctionDict`` (callable from Lua scripts)."""

    def decorate(fn: Callable) -> Callable:
        setattr(fn, "__limbuspy_expose__", ExposeSpec(name or fn.__name__, "lua", None))
        return fn

    return decorate


# --------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------


class ConfigField:
    __slots__ = ("name", "default", "description", "choices")

    def __init__(self, name: str, default: Any, description: str = "", choices=None) -> None:
        self.name = name
        self.default = default
        self.description = description
        self.choices = choices


class ConfigSpec:
    """Typed config with defaults, validation and JSON persistence (no TOML writer needed)."""

    def __init__(self, **fields: Any) -> None:
        self._fields = {}
        for name, value in fields.items():
            if isinstance(value, ConfigField):
                self._fields[name] = value
            else:
                self._fields[name] = ConfigField(name, value)
        self._values = {name: field.default for name, field in self._fields.items()}

    # -- access ------------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        fields = self.__dict__.get("_fields", {})
        if name in fields:
            return self.__dict__["_values"][name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_fields", "_values"):
            object.__setattr__(self, name, value)
            return
        fields = self.__dict__.get("_fields", {})
        if name in fields:
            self.__dict__["_values"][name] = self._validate(name, value)
            return
        object.__setattr__(self, name, value)

    def _validate(self, name: str, value: Any) -> Any:
        field = self._fields[name]
        if field.choices is not None and value not in field.choices:
            raise ValueError("%s must be one of %r, got %r" % (name, field.choices, value))
        if isinstance(field.default, bool) and not isinstance(value, bool):
            return bool(value)
        if isinstance(field.default, int) and not isinstance(field.default, bool):
            return int(value)
        if isinstance(field.default, float):
            return float(value)
        if isinstance(field.default, str):
            return str(value)
        return value

    # -- persistence -------------------------------------------------------------------

    def as_dict(self) -> dict:
        return dict(self._values)

    def load(self, values: Optional[dict]) -> None:
        if not values:
            return
        for name, value in values.items():
            if name in self._fields:
                try:
                    self._values[name] = self._validate(name, value)
                except (TypeError, ValueError):
                    pass  # keep the default; the loader logs the mismatch

    def schema(self) -> dict:
        return {
            name: {"default": field.default, "description": field.description, "choices": field.choices}
            for name, field in self._fields.items()
        }


def config(**fields: Any) -> ConfigSpec:
    """Shorthand used inside a Mod class body: ``cfg = config(sinking_gain=2)``."""
    return ConfigSpec(**fields)
