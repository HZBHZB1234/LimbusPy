"""End-to-end tests of the LPCP client against a Python mock kernel.

These cover the parts that would otherwise need the game: handshake, request/response
correlation, dispatch ordering, reentrancy, timeouts and error propagation.
"""

from __future__ import annotations

import threading
import time

import pytest

from conftest import make_transport_pair
from limbuspy._mock import MockHandles, MockKernel
from limbuspy.runtime import protocol
from limbuspy.runtime.client import Bridge
from limbuspy.runtime.errors import (
    BridgeError,
    NotFoundError,
    PeerClosedError,
    ScriptError,
    TimeoutError_,
)
from limbuspy.runtime.protocol import MsgType


class Harness:
    def __init__(self, handlers=None, kernel_hello=None):
        client_t, kernel_t = make_transport_pair()
        self.kernel = MockKernel(kernel_t, handlers=handlers)
        self.bridge = Bridge(client_t, name="test-client").start()
        self.ready = None
        self._error = None
        self._thread = threading.Thread(target=self._run_kernel, args=(kernel_hello,), daemon=True)

    def _run_kernel(self, kernel_hello):
        try:
            if kernel_hello is None:
                self.ready = self.kernel.start(timeout=3.0)
            else:
                self.kernel.bridge.notify(MsgType.HELLO, kernel_hello)
                self.ready = self.kernel.bridge.wait_for(MsgType.READY, timeout=3.0).decode()
        except Exception as exc:  # noqa: BLE001
            self._error = exc

    def __enter__(self):
        self._thread.start()
        return self

    def handshake(self, **kwargs):
        hello = self.bridge.handshake(**kwargs)
        self._thread.join(5)
        if self._error is not None:
            raise self._error
        return hello

    def __exit__(self, *exc_info):
        try:
            self.bridge.close()
        finally:
            self.kernel.close()
        self._thread.join(5)


@pytest.fixture()
def echo_kernel():
    handlers = {
        "echo": lambda *args: args[0] if len(args) == 1 else list(args),
        "add": lambda a, b: a + b,
        "concat": lambda a, b: "%s%s" % (a, b),
        "nested": lambda: {"ok": [1, {"deep": None}], "bin": b"\x00\x01", "f": 2.5},
        "boom": _raise_value_error,
        "slow": lambda ms: (time.sleep(float(ms) / 1000.0), "late")[1],
    }
    with Harness(handlers=handlers) as harness:
        harness.handshake(mods=["demo"], stubs_build="deadbeef")
        yield harness


def _raise_value_error():
    raise ValueError("kaboom")


# ---------------------------------------------------------------------------------------
# handshake
# ---------------------------------------------------------------------------------------


def test_handshake_exchanges_versions_and_caps(echo_kernel):
    ready = echo_kernel.ready
    assert ready["proto"] == protocol.PROTOCOL_VERSION
    assert ready["mods"] == ["demo"]
    assert ready["stubs_build"] == "deadbeef"
    assert ready["py"].count(".") == 2

    hello = echo_kernel.bridge.hello
    assert hello["proto"] == protocol.PROTOCOL_VERSION
    assert hello["kernel"] == "mock-0.1.0"
    assert protocol.Caps.HOT_RELOAD in hello["caps"]


def test_protocol_mismatch_is_fatal():
    hello = {
        "proto": "2.0",
        "kernel": "future",
        "game_build": "x",
        "game_version": "y",
        "caps": [],
    }
    with Harness(kernel_hello=hello) as harness:
        with pytest.raises(BridgeError) as info:
            harness.handshake()
        assert info.value.code == protocol.ErrorCode.PROTO
        assert harness.bridge.closed is True


# ---------------------------------------------------------------------------------------
# calls
# ---------------------------------------------------------------------------------------


def test_call_roundtrip(echo_kernel):
    bridge = echo_kernel.bridge
    assert bridge.call("k", "echo", 42) == 42
    assert bridge.call("k", "add", 2, 3) == 5
    assert bridge.call("k", "concat", "边狱", "公司") == "边狱公司"
    assert bridge.call("k", "nested") == {
        "ok": [1, {"deep": None}],
        "bin": b"\x00\x01",
        "f": 2.5,
    }


def test_call_unknown_method_is_notfound(echo_kernel):
    with pytest.raises(NotFoundError) as info:
        echo_kernel.bridge.call("k", "nope")
    assert info.value.code == protocol.ErrorCode.NOT_FOUND


def test_call_wrong_arity_is_script_error(echo_kernel):
    with pytest.raises(ScriptError):
        echo_kernel.bridge.call("k", "add", 1)


def test_call_bad_target_still_dispatches_by_method(echo_kernel):
    # The mock resolves "target::method" first, then falls back to method.
    assert echo_kernel.bridge.call("", "echo", "x") == "x"


def test_timeout_does_not_poison_later_calls(echo_kernel):
    with pytest.raises(TimeoutError_):
        echo_kernel.bridge.call("k", "slow", 120, timeout=0.02)
    # The late reply must be dropped as an orphan, not mis-delivered.
    time.sleep(0.2)
    assert echo_kernel.bridge.call("k", "echo", "still alive") == "still alive"


def test_concurrent_calls_correlate(echo_kernel):
    bridge = echo_kernel.bridge
    results = {}
    errors = []

    def worker(index):
        try:
            results[index] = bridge.call("k", "add", index, index)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert not errors
    assert results == {i: i * 2 for i in range(40)}


def test_ping(echo_kernel):
    assert echo_kernel.bridge is not None
    assert echo_kernel.kernel.ping() is True


# ---------------------------------------------------------------------------------------
# kernel -> python
# ---------------------------------------------------------------------------------------


def test_event_batch_actions(echo_kernel):
    seen = []

    def on_batch(frame):
        payload = frame.decode()
        seen.append(payload)
        actions = [{"event": i, "effects": [{"op": "log", "msg": "e%d" % i}]} for i, _ in enumerate(payload["events"])]
        echo_kernel.bridge.notify(MsgType.ACT, {"seq": payload["seq"], "actions": actions})

    echo_kernel.bridge.on(MsgType.EVENTBATCH, on_batch)

    actions = echo_kernel.kernel.emit_event_batch(
        [{"timing": "OnSucceedAttack", "gen": 1}, {"timing": "OnSucceedAttack", "gen": 1}]
    )

    assert len(seen) == 1
    assert [a["event"] for a in actions] == [0, 1]
    assert actions[0]["effects"][0]["op"] == "log"


def test_sync_call_returns_value(echo_kernel):
    def on_sync(payload):
        return {"echoed": payload["fn"], "ctx": payload["ctx"]}

    echo_kernel.bridge.on_request(MsgType.SYNCALL, on_sync)
    value = echo_kernel.kernel.sync_call("mymod.on_hit", {"unit": 1})
    assert value == {"echoed": "mymod.on_hit", "ctx": {"unit": 1}}


def test_sync_call_handler_exception_is_reported(echo_kernel):
    def on_sync(payload):
        raise ValueError("handler exploded")

    echo_kernel.bridge.on_request(MsgType.SYNCALL, on_sync)
    with pytest.raises(ScriptError) as info:
        echo_kernel.kernel.sync_call("mymod.bad")
    assert "handler exploded" in str(info.value)


def test_sync_call_can_reenter_the_kernel(echo_kernel):
    """The handler thread may call back into the kernel while the kernel is blocked."""

    def on_sync(payload):
        return echo_kernel.bridge.call("k", "add", 20, 22)

    echo_kernel.bridge.on_request(MsgType.SYNCALL, on_sync)
    assert echo_kernel.kernel.sync_call("mymod.reenter") == 42


def test_sync_call_without_handler_is_unsupported(echo_kernel):
    with pytest.raises(BridgeError) as info:
        echo_kernel.kernel.sync_call("nobody.home")
    assert info.value.code == protocol.ErrorCode.UNSUPPORTED


def test_unknown_inbound_request_is_answered(echo_kernel):
    # PATCH_ADD is a request message; the mock kernel has no handler for it, so the call must
    # come back as a structured "unsupported" error instead of hanging.
    with pytest.raises(BridgeError) as info:
        echo_kernel.bridge.request(MsgType.PATCH_ADD, {"spec": {}}, timeout=2.0)
    assert info.value.code == protocol.ErrorCode.UNSUPPORTED


# ---------------------------------------------------------------------------------------
# handles
# ---------------------------------------------------------------------------------------


def test_handle_refs_roundtrip():
    handles = MockHandles()
    obj = object()
    handle = handles.alloc("unit", obj)
    kind, target = handles.resolve(handle)
    assert kind == "unit"
    assert target is obj
    assert handles.release(handle) is True
    with pytest.raises(BridgeError) as info:
        handles.resolve(handle)
    assert info.value.code == protocol.ErrorCode.STALE_HANDLE


def test_ref_helper_matches_wire_shape():
    assert protocol.ref("buff", 5) == {"t": "buff", "h": 5}
    assert protocol.try_read_ref(protocol.ref("buff", 5)) == ("buff", 5)


# ---------------------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------------------


def test_kernel_close_closes_client():
    with Harness(handlers={"echo": lambda x: x}) as harness:
        harness.handshake()
        bridge = harness.bridge
        harness.kernel.close()
        assert bridge.wait_closed(3.0) is True
        assert bridge.closed is True
        with pytest.raises((PeerClosedError, TimeoutError_)):
            bridge.call("k", "echo", 1, timeout=0.5)
