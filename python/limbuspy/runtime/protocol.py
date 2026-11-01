"""LPCP constants and payload helpers (docs/01-protocol.md §3, §5.1, §7)."""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "MsgType",
    "ErrorCode",
    "Caps",
    "PROTOCOL_VERSION",
    "PROTOCOL_MAJOR",
    "is_compatible",
    "ref",
    "null_ref",
    "err",
    "get_int",
    "get_long",
    "get_bool",
    "get_str",
    "get_array",
    "get_map",
    "get_str_array",
    "try_read_ref",
    "HANDLE_KINDS",
]


class MsgType(IntEnum):
    HELLO = 0x01
    READY = 0x02
    CALL = 0x03
    CALLRESULT = 0x04
    EVENTBATCH = 0x05
    ACT = 0x06
    SYNCALL = 0x07
    SYNCRESULT = 0x08
    PATCH_ADD = 0x09
    PATCH_DEL = 0x0A
    REGISTRY_ADD = 0x0B
    REGISTRY_DEL = 0x0C
    RELOAD = 0x0D
    STATE = 0x0E
    LOG = 0x0F
    STATS = 0x10
    PING = 0x11
    PONG = 0x12
    ERROR = 0x13
    DOCTOR = 0x14
    BYE = 0x15
    SNAPSHOT = 0x16


#: Messages that carry an ``id`` and expect a CALLRESULT back (docs §3).
REQUEST_MESSAGES = frozenset(
    {
        MsgType.CALL,
        MsgType.PATCH_ADD,
        MsgType.PATCH_DEL,
        MsgType.REGISTRY_ADD,
        MsgType.REGISTRY_DEL,
        MsgType.STATE,
        MsgType.DOCTOR,
        MsgType.SNAPSHOT,
    }
)

RESULT_MESSAGES = frozenset({MsgType.CALLRESULT, MsgType.SYNCRESULT})


class ErrorCode:
    PROTO = "proto"
    BUSY = "busy"
    NOT_FOUND = "notfound"
    AMBIGUOUS = "ambiguous"
    MARSHAL = "marshal"
    WRONG_THREAD = "wrong_thread"
    TIMEOUT = "timeout"
    STALE_HANDLE = "stale_handle"
    SCRIPT = "script"
    UNSUPPORTED = "unsupported"
    OVER_BUDGET = "over_budget"
    INTERNAL = "internal"


class Caps:
    REFLECT_MANAGED = "reflect.managed"
    REFLECT_NATIVE = "reflect.native"
    REFLECT_FIELD = "reflect.field"
    HANDLE_ARENA = "handle.arena"
    HANDLE_PINNED = "handle.pinned"
    SNAPSHOT_V1 = "snapshot.v1"
    PATCH_TABLE = "patch.table"
    MODULAR_REGISTRY = "modular.registry"
    MODULAR_RUN = "modular.run"
    HOT_RELOAD = "hot.reload"
    BUDGET_FRAME = "budget.frame"


PROTOCOL_VERSION = "1.1"
PROTOCOL_MAJOR = 1

#: Handle kinds as they appear on the wire (docs §4.1).
HANDLE_KINDS = frozenset(
    {
        "unit",
        "skill",
        "coin",
        "buff",
        "action",
        "stage",
        "model",
        "ability",
        "sound",
        "asset",
        "sa",
        "obj",
    }
)


def is_compatible(peer_version: Optional[str]) -> bool:
    """True when the peer speaks the same protocol major version."""
    if not peer_version:
        return False
    text = str(peer_version).strip()
    if not text:
        return False
    major = text.split(".", 1)[0]
    try:
        return int(major) == PROTOCOL_MAJOR
    except ValueError:
        return False


# --------------------------------------------------------------------------------------
# payload helpers
# --------------------------------------------------------------------------------------


def ref(kind: str, handle: int) -> Dict[str, Any]:
    """Typed reference to a kernel-side object (docs §4.1)."""
    if kind not in HANDLE_KINDS:
        raise ValueError("unknown handle kind %r" % (kind,))
    return {"t": kind, "h": int(handle)}


def null_ref() -> Dict[str, Any]:
    return {"t": "null"}


def err(code: str, message: str, detail: Optional[dict] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"code": code, "msg": message or ""}
    if detail is not None:
        payload["detail"] = detail
    return payload


def get_long(message: Optional[dict], key: str, fallback: int = 0) -> int:
    if not message:
        return fallback
    value = message.get(key, fallback)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return fallback
    return fallback


def get_int(message: Optional[dict], key: str, fallback: int = 0) -> int:
    return get_long(message, key, fallback)


def get_bool(message: Optional[dict], key: str, fallback: bool = False) -> bool:
    if not message:
        return fallback
    value = message.get(key, fallback)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return fallback


def get_str(message: Optional[dict], key: str, fallback: Optional[str] = None) -> Optional[str]:
    if not message:
        return fallback
    value = message.get(key, fallback)
    return value if isinstance(value, str) else fallback


def get_array(message: Optional[dict], key: str) -> List[Any]:
    if not message:
        return []
    value = message.get(key)
    return value if isinstance(value, list) else []


def get_map(message: Optional[dict], key: str) -> Optional[dict]:
    if not message:
        return None
    value = message.get(key)
    return value if isinstance(value, dict) else None


def get_str_array(message: Optional[dict], key: str) -> List[str]:
    return [item if isinstance(item, str) else "" for item in get_array(message, key)]


def try_read_ref(value: Any) -> Optional[Tuple[str, int]]:
    """
    Accepts ``{"t": kind, "h": n}`` or a bare integer and returns ``(kind, handle)``.

    Returns ``None`` for explicit nulls and unsupported shapes; ``kind`` is ``""`` when the
    value was a bare integer and the kernel has to infer it from its handle table.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return ("", value) if value else None
    if isinstance(value, dict):
        kind = value.get("t")
        if kind is None or kind == "null":
            return None
        if not isinstance(kind, str):
            return None
        handle = get_long(value, "h")
        if not handle:
            return None
        if kind not in HANDLE_KINDS:
            raise ValueError("unknown handle kind %r" % (kind,))
        return (kind, handle)
    return None
