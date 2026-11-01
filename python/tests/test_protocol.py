"""Protocol constant / helper tests."""

from __future__ import annotations

import pytest

from limbuspy.runtime import protocol
from limbuspy.runtime.errors import error_from_payload


def test_message_type_values_are_frozen():
    # docs/01-protocol.md §3 - these values are a wire contract, do not reorder.
    assert protocol.MsgType.HELLO == 0x01
    assert protocol.MsgType.READY == 0x02
    assert protocol.MsgType.CALL == 0x03
    assert protocol.MsgType.CALLRESULT == 0x04
    assert protocol.MsgType.EVENTBATCH == 0x05
    assert protocol.MsgType.ACT == 0x06
    assert protocol.MsgType.SYNCALL == 0x07
    assert protocol.MsgType.SYNCRESULT == 0x08
    assert protocol.MsgType.PATCH_ADD == 0x09
    assert protocol.MsgType.PATCH_DEL == 0x0A
    assert protocol.MsgType.REGISTRY_ADD == 0x0B
    assert protocol.MsgType.REGISTRY_DEL == 0x0C
    assert protocol.MsgType.RELOAD == 0x0D
    assert protocol.MsgType.STATE == 0x0E
    assert protocol.MsgType.LOG == 0x0F
    assert protocol.MsgType.STATS == 0x10
    assert protocol.MsgType.PING == 0x11
    assert protocol.MsgType.PONG == 0x12
    assert protocol.MsgType.ERROR == 0x13
    assert protocol.MsgType.DOCTOR == 0x14
    assert protocol.MsgType.BYE == 0x15


@pytest.mark.parametrize(
    "given,expected",
    [
        ("1.0", True),
        ("1.9", True),
        ("1", True),
        ("2.0", False),
        ("0.9", False),
        ("", False),
        (None, False),
        ("x.y", False),
    ],
)
def test_is_compatible(given, expected):
    assert protocol.is_compatible(given) is expected


def test_ref_helpers():
    assert protocol.ref("unit", 7) == {"t": "unit", "h": 7}
    assert protocol.null_ref() == {"t": "null"}
    with pytest.raises(ValueError):
        protocol.ref("nonsense", 1)


def test_try_read_ref():
    assert protocol.try_read_ref({"t": "unit", "h": 3}) == ("unit", 3)
    assert protocol.try_read_ref(9) == ("", 9)
    assert protocol.try_read_ref(None) is None
    assert protocol.try_read_ref({"t": "null"}) is None
    assert protocol.try_read_ref({"t": "unit", "h": 0}) is None
    assert protocol.try_read_ref(True) is None
    with pytest.raises(ValueError):
        protocol.try_read_ref({"t": "spaceship", "h": 1})


def test_accessors_are_tolerant():
    payload = {"n": 3, "s": "x", "b": True, "a": [1, 2], "m": {"k": "v"}, "fs": "7"}
    assert protocol.get_int(payload, "n") == 3
    assert protocol.get_int(payload, "missing", 42) == 42
    assert protocol.get_int(payload, "fs") == 7
    assert protocol.get_str(payload, "s") == "x"
    assert protocol.get_str(payload, "n") is None
    assert protocol.get_bool(payload, "b") is True
    assert protocol.get_array(payload, "a") == [1, 2]
    assert protocol.get_array(payload, "n") == []
    assert protocol.get_map(payload, "m") == {"k": "v"}
    assert protocol.get_map(payload, "n") is None
    assert protocol.get_str_array({"a": ["x", 2]}, "a") == ["x", ""]


def test_error_payload_shapes():
    assert protocol.err("timeout", "boom") == {"code": "timeout", "msg": "boom"}
    assert protocol.err("timeout", "boom", {"a": 1})["detail"] == {"a": 1}


@pytest.mark.parametrize(
    "code,expected_type",
    [
        ("notfound", "NotFoundError"),
        ("ambiguous", "AmbiguousError"),
        ("marshal", "MarshalError"),
        ("wrong_thread", "WrongThreadError"),
        ("stale_handle", "StaleHandleError"),
        ("script", "ScriptError"),
        ("unsupported", "UnsupportedError"),
        ("something-else", "BridgeError"),
    ],
)
def test_error_from_payload_maps_codes(code, expected_type):
    exc = error_from_payload({"code": code, "msg": "m"})
    assert type(exc).__name__ == expected_type
    assert exc.code == code
    assert "m" in str(exc)


def test_error_from_payload_handles_garbage():
    exc = error_from_payload(None)
    assert exc.code == "internal"
    exc = error_from_payload({"code": 5, "msg": 6, "detail": "nope"})
    assert exc.code == "5"
    assert exc.message == "6"
    assert exc.detail == {}


def test_caps_constants_are_namespaced():
    for name in dir(protocol.Caps):
        if name.startswith("_"):
            continue
        value = getattr(protocol.Caps, name)
        assert isinstance(value, str) and value
        assert "." in value, "capability %r should be namespaced" % value
