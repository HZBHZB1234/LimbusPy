"""Bridge error types shared by the runtime, the client and user scripts."""

from __future__ import annotations

from typing import Any, Dict, Optional


class LimbusPyError(Exception):
    """Base class for every LimbusPy failure."""


class BridgeError(LimbusPyError):
    """A structured error returned by the kernel (docs/01-protocol.md §7)."""

    def __init__(self, code: str, message: str = "", detail: Optional[dict] = None) -> None:
        self.code = code or "internal"
        self.message = message or ""
        self.detail = detail or {}
        super().__init__("%s: %s" % (self.code, self.message) if self.message else self.code)

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, Any]]) -> "BridgeError":
        payload = payload or {}
        code = payload.get("code") or "internal"
        message = payload.get("msg") or ""
        detail = payload.get("detail")
        if not isinstance(code, str):
            code = str(code)
        if not isinstance(message, str):
            message = str(message)
        if not isinstance(detail, dict):
            detail = {}
        return cls(code, message, detail)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "BridgeError(%r, %r)" % (self.code, self.message)


class NotFoundError(BridgeError):
    pass


class AmbiguousError(BridgeError):
    pass


class MarshalError(BridgeError):
    pass


class WrongThreadError(BridgeError):
    pass


class StaleHandleError(BridgeError):
    pass


class ScriptError(BridgeError):
    pass


class UnsupportedError(BridgeError):
    pass


class TimeoutError_(LimbusPyError):
    """A request did not receive a reply within the deadline."""


#: Maps wire error codes onto the richer Python exception classes.
_BY_CODE = {
    "notfound": NotFoundError,
    "ambiguous": AmbiguousError,
    "marshal": MarshalError,
    "wrong_thread": WrongThreadError,
    "stale_handle": StaleHandleError,
    "script": ScriptError,
    "unsupported": UnsupportedError,
}


def error_from_payload(payload: Optional[Dict[str, Any]]) -> BridgeError:
    """Builds the most specific BridgeError subclass for a wire error payload."""
    base = BridgeError.from_payload(payload)
    klass = _BY_CODE.get(base.code, BridgeError)
    return klass(base.code, base.message, base.detail)


class PeerClosedError(LimbusPyError):
    """The other side of the bridge went away."""


class TransportError(LimbusPyError):
    """Transport-level failure (pipe closed, connection refused, ...)."""
