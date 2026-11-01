"""Per-callback context handed to hooks and exposed functions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..runtime import protocol
from ..runtime.client import Bridge

__all__ = ["Context"]


class Context:
    """What a mod sees when it is called.

    ``call``/``get``/``set`` go straight to the kernel's reflection layer; handles travel as the
    ``{"t","h"}`` refs the kernel produced, so a script never invents one.
    """

    __slots__ = ("bridge", "mod", "timing", "gen", "event", "target", "snapshot", "effects",
                 "state", "session", "modular", "_logger")

    def __init__(self, bridge: Bridge, mod: str, timing: str = "", gen: int = 1,
                 event: Optional[dict] = None, state: Optional[dict] = None,
                 logger=None) -> None:
        self.bridge = bridge
        self.mod = mod
        self.timing = timing
        self.gen = gen
        self.event = dict(event or {})
        self.target = _extract_target(self.event)
        self.snapshot = self.event.get("snapshot")
        self.effects: List[dict] = []
        self.state = state if state is not None else {}
        #: Game façade bound to this callback. A fresh session per callback means the value cache
        #: never leaks stale numbers into the next event.
        self.session = _make_session(bridge)
        #: One-way compatibility layer (``ctx.modular.run("TIMING:WhenUse/...")``).
        self.modular = _make_modular(self.session)
        self._logger = logger

    # -- reflection ---------------------------------------------------------------------

    def call(self, target: Any, method: str, *args: Any, timeout: float = 5.0) -> Any:
        return self.bridge.call(target, method, *args, timeout=timeout)

    def invoke(self, target: Any, method: str, args: Optional[list] = None, timeout: float = 5.0) -> Any:
        return self.bridge.call(target, method, *(args or []), timeout=timeout)

    def get(self, target: Any, member: str, timeout: float = 5.0) -> Any:
        return self._member_call("reflect.get", target, member, timeout=timeout)

    def set(self, target: Any, member: str, value: Any, timeout: float = 5.0) -> Any:
        return self._member_call("reflect.set", target, member, value, timeout=timeout)

    def _member_call(self, method: str, target: Any, member: str, *extra: Any, timeout: float = 5.0) -> Any:
        args = [target, member] + list(extra)
        return self.bridge.request(
            protocol.MsgType.CALL,
            {"target": "", "method": method, "args": args},
            timeout=timeout,
        ).get("value")

    def static(self, type_and_method: str, *args: Any, timeout: float = 5.0) -> Any:
        """``ctx.static("Ns.Type::Method", ...)`` - a static call without a receiver."""
        return self.bridge.call("", type_and_method, *args, timeout=timeout)

    # -- deferred effects ---------------------------------------------------------------

    def effect(self, op: str, **kwargs: Any) -> None:
        """Queues a declarative effect for the kernel to apply at the next safe point.

        The kernel applies these in a later milestone; until then use ``call``/``set`` for anything
        that must happen now. Keeping the API here means mods written today keep working when the
        fast path lands.
        """
        payload = {"op": op}
        payload.update(kwargs)
        self.effects.append(payload)

    # -- logging / convenience ----------------------------------------------------------

    def log(self, message: str) -> None:
        if self._logger is not None:
            self._logger("%s/%s: %s" % (self.mod, self.timing or "call", message))

    @property
    def is_enemy_turn(self) -> bool:
        return bool(self.event.get("enemy_turn"))


def _make_session(bridge):
    try:
        from ..game import Session

        return Session(bridge)
    except Exception:  # noqa: BLE001 - the mod layer must not depend on the façade
        return None


def _make_modular(session):
    if session is None:
        return None
    try:
        from ..modular import Modular

        return Modular(session)
    except Exception:  # noqa: BLE001
        return None


def _extract_target(event: dict) -> Any:
    """The kernel puts the primary subject in ``ctx``; older payloads may use ``target``."""
    if not isinstance(event, dict):
        return None
    for key in ("ctx", "target", "unit"):
        value = event.get(key)
        if value is not None:
            return value
    return None
