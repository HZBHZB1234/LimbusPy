"""The bridge-backed session behind every façade object.

One :class:`Session` per connection. It owns the value wrapping rules (a wire ref becomes a ``Unit``,
a ``Skill``, ...), the optional index validation, and a per-callback cache so a hook that reads
``unit.hp`` five times does one round trip, not five.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

from ..runtime import protocol
from ..runtime.client import Bridge
from ..runtime.errors import BridgeError

__all__ = ["Session", "wrap", "wrap_many"]

_KIND_TO_FACTORY = {}


def register_wrapper(kind: str, factory) -> None:
    _KIND_TO_FACTORY[kind] = factory


def wrap(session: "Session", value: Any) -> Any:
    """Converts a wire value into a façade object when it is a typed reference."""
    if isinstance(value, dict):
        kind = value.get("t")
        if isinstance(kind, str) and "h" in value:
            factory = _KIND_TO_FACTORY.get(kind)
            if factory is not None:
                return factory(session, value)
        return {key: wrap(session, item) for key, item in value.items()}
    if isinstance(value, list):
        return [wrap(session, item) for item in value]
    return value


def wrap_many(session: "Session", values: Iterable[Any]) -> List[Any]:
    return [wrap(session, value) for value in (values or [])]


class Session:
    """Wraps a :class:`~limbuspy.runtime.client.Bridge` with game-aware helpers."""

    def __init__(self, bridge: Bridge, index=None, strict: Optional[bool] = None) -> None:
        self.bridge = bridge
        self.index = index
        #: When true, unknown member names raise instead of being sent to the kernel.
        self.strict = bool(os.environ.get("LIMBUSPY_STRICT")) if strict is None else bool(strict)
        self._cache: Dict[tuple, Any] = {}
        self.warnings: List[str] = []

    # -- values ------------------------------------------------------------------------

    def get(self, ref: Any, member: str, timeout: float = 5.0) -> Any:
        key = ("get", _key(ref), member)
        if key in self._cache:
            return self._cache[key]
        value = self.bridge.request(
            protocol.MsgType.CALL,
            {"target": ref if isinstance(ref, dict) else "", "method": "reflect.get", "args": [ref, member]},
            timeout=timeout,
        ).get("value")
        wrapped = wrap(self, value)
        self._cache[key] = wrapped
        return wrapped

    def set(self, ref: Any, member: str, value: Any, timeout: float = 5.0) -> Any:
        self._cache.pop(("get", _key(ref), member), None)
        result = self.bridge.request(
            protocol.MsgType.CALL,
            {"target": "", "method": "reflect.set", "args": [ref, member, value]},
            timeout=timeout,
        ).get("value")
        return wrap(self, result)

    def call(self, ref: Any, member: str, *args: Any, timeout: float = 5.0) -> Any:
        key = ("call", _key(ref), member, len(args))
        if not args and key in self._cache:
            return self._cache[key]
        result = self.bridge.call("", "reflect.invoke", ref, member, list(args), timeout=timeout)
        wrapped = wrap(self, result)
        if not args:
            self._cache[key] = wrapped
        return wrapped

    def static(self, type_and_method: str, *args: Any, timeout: float = 5.0) -> Any:
        return wrap(self, self.bridge.call("", type_and_method, *args, timeout=timeout))

    def global_ref(self, name: str) -> Dict[str, Any]:
        """A ``global:Name`` handle that the kernel resolves to a singleton."""
        return self.bridge.call("", "reflect.get", "global:" + name, "__type__")

    def snapshot(self, timeout: float = 5.0) -> dict:
        """One round trip describing the whole battle (see protocol §6.4)."""
        result = self.bridge.request(protocol.MsgType.SNAPSHOT, {}, timeout=timeout)
        return dict(result.get("value") or {})

    def coerce(self, value: Any, cls) -> Any:
        """Wraps ``value`` with ``cls`` when it is (a list of) typed references.

        The kernel's wire ``kind`` is only a hint: a type it does not classify arrives as ``obj``
        and gets wrapped as a generic ``Ref``. The caller knows the expected class from its member
        table, so references are re-labelled here - the façade therefore never depends on the
        kernel's best-effort classification.
        """
        if value is None:
            return None
        if isinstance(value, list):
            return [self.coerce(item, cls) for item in value]
        if isinstance(value, cls):
            return value
        # Already wrapped as something else (usually Ref): re-label from its target spec.
        target = getattr(value, "target", None)
        if target is not None:
            return cls(self, target)
        if isinstance(value, dict) and "h" in value:
            return cls(self, value)
        return value

    # -- validation --------------------------------------------------------------------

    def check_member(self, type_name: str, member: str) -> None:
        """Dev-time guard: an unknown member name is a typo, not a runtime surprise."""
        index = self.index
        if index is None or not index.has_type(type_name):
            return
        if index.has_member(type_name, member):
            return
        message = "%s has no member %r in this build" % (type_name, member)
        if self.strict:
            raise BridgeError(protocol.ErrorCode.NOT_FOUND, message)
        if message not in self.warnings:
            self.warnings.append(message)

    # -- cache -------------------------------------------------------------------------

    def refresh(self) -> None:
        self._cache.clear()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._cache.clear()


def _key(ref: Any) -> Any:
    if isinstance(ref, dict):
        return (ref.get("t"), ref.get("h"))
    return ref
