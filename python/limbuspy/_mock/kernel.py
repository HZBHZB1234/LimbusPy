"""A Python implementation of the *kernel* side of LPCP.

Purpose: let the Python runtime be tested end-to-end with no game and no C# build. The mock
speaks the same wire protocol through the same :class:`~limbuspy.runtime.client.Bridge`
machinery with the handshake inverted, so a passing mock test means the client's framing,
correlation, dispatch and handshake logic are sound.

It is also the reference used by ``limbuspy.devkit`` to run mod logic against a synthetic
battle state (recorded/replayed event batches).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..runtime import protocol
from ..runtime.client import Bridge
from ..runtime.errors import BridgeError
from ..runtime.protocol import MsgType
from ..runtime.transport import Transport

__all__ = ["MockKernel", "MockHandles"]

KERNEL_VERSION = "mock-0.1.0"


class MockHandles:
    """Tiny handle registry so tests can exercise ``{"t","h"}`` refs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0x1000
        self._objects: Dict[int, Tuple[str, Any]] = {}

    def alloc(self, kind: str, obj: Any) -> int:
        with self._lock:
            handle = self._next
            self._next += 1
            self._objects[handle] = (kind, obj)
            return handle

    def resolve(self, handle: int) -> Tuple[str, Any]:
        with self._lock:
            entry = self._objects.get(int(handle))
        if entry is None:
            raise BridgeError(protocol.ErrorCode.STALE_HANDLE, "handle 0x%x is not live" % int(handle))
        return entry

    def release(self, handle: int) -> bool:
        with self._lock:
            return self._objects.pop(int(handle), None) is not None

    def ref(self, kind: str, obj: Any) -> Dict[str, Any]:
        return protocol.ref(kind, self.alloc(kind, obj))


class MockKernel:
    """Plays the C# kernel's role on the wire."""

    def __init__(
        self,
        transport: Transport,
        handlers: Optional[Dict[str, Callable[..., Any]]] = None,
        caps: Optional[List[str]] = None,
        game_build: str = "mock",
        game_version: str = "1.84.0-mock",
    ) -> None:
        self.bridge = Bridge(transport, name="mock-kernel").start()
        self.handles = MockHandles()
        self._handlers: Dict[str, Callable[..., Any]] = dict(handlers or {})
        self._caps = list(caps or [
            protocol.Caps.REFLECT_MANAGED,
            protocol.Caps.HANDLE_ARENA,
            protocol.Caps.HOT_RELOAD,
        ])
        self._game_build = game_build
        self._game_version = game_version
        self.ready: Optional[dict] = None
        self._closed = threading.Event()
        self.bridge.on_request(MsgType.CALL, self._handle_call)
        self.bridge.on_request(MsgType.DOCTOR, self._handle_doctor)
        self.bridge.on_request(MsgType.PATCH_ADD, self._handle_patch_add)
        self.bridge.on_request(MsgType.PATCH_DEL, self._handle_patch_del)
        self.bridge.on(MsgType.BYE, self._on_bye)
        self.bridge.on(MsgType.LOG, self._on_log)
        self.logs: List[dict] = []
        #: id -> spec, mirroring the real kernel's patch table.
        self.patches: Dict[str, dict] = {}

    # -- lifecycle ---------------------------------------------------------------------

    def start(self, timeout: float = 3.0) -> dict:
        """Sends HELLO and waits for READY. Returns the READY payload."""
        self.bridge.notify(MsgType.HELLO, {
            "proto": protocol.PROTOCOL_VERSION,
            "kernel": KERNEL_VERSION,
            "game_build": self._game_build,
            "game_version": self._game_version,
            "caps": self._caps,
        })
        frame = self.bridge.wait_for(MsgType.READY, timeout=timeout)
        self.ready = frame.decode()
        return self.ready

    def close(self) -> None:
        self._closed.set()
        self.bridge.close("mock kernel done")

    def wait_closed(self, timeout: Optional[float] = None) -> bool:
        return self.bridge.wait_closed(timeout)

    @property
    def closed(self) -> bool:
        return self.bridge.closed

    # -- handler registration ----------------------------------------------------------

    def register(self, method: str, fn: Callable[..., Any]) -> None:
        self._handlers[method] = fn

    def register_many(self, mapping: Dict[str, Callable[..., Any]]) -> None:
        self._handlers.update(mapping)

    # -- kernel -> python --------------------------------------------------------------

    def emit_event_batch(self, events: List[dict], timeout: float = 2.0) -> List[dict]:
        self.bridge.notify(MsgType.EVENTBATCH, {
            "seq": int(time.time() * 1000) & 0x7FFFFFFF,
            "frame": 0,
            "events": events,
        })
        frame = self.bridge.wait_for(MsgType.ACT, timeout=timeout)
        payload = frame.decode()
        return list(payload.get("actions") or [])

    def sync_call(self, fn: str, ctx: Optional[dict] = None, deadline_ms: int = 20,
                  timeout: float = 2.0) -> Any:
        result = self.bridge.request(
            MsgType.SYNCALL,
            {"fn": fn, "ctx": ctx or {}, "deadline_ms": int(deadline_ms)},
            timeout=timeout,
        )
        return result.get("value")

    def ping(self, timeout: float = 2.0) -> bool:
        self.bridge.notify(MsgType.PING)
        self.bridge.wait_for(MsgType.PONG, timeout=timeout)
        return True

    # -- internals ---------------------------------------------------------------------

    def _handle_call(self, payload: dict) -> Any:
        method = payload.get("method") or ""
        target = payload.get("target") or ""
        args = payload.get("args") or []
        handler = self._handlers.get("%s::%s" % (target, method)) or self._handlers.get(method)
        if handler is None:
            raise BridgeError(protocol.ErrorCode.NOT_FOUND, "no mock handler for %r (target=%r)" % (method, target))
        if not isinstance(args, list):
            raise BridgeError(protocol.ErrorCode.MARSHAL, "args must be a list")
        return handler(*args)

    def _handle_patch_add(self, payload: dict) -> dict:
        spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else payload
        owner = spec.get("owner") or ""
        method = spec.get("method") or ""
        fn = spec.get("fn") or ""
        if not owner or not method or not fn:
            raise BridgeError(protocol.ErrorCode.MARSHAL, "patch needs owner, method and fn")
        patch_id = "%s.%s#%d" % (owner, method, len(self.patches) + 1)
        self.patches[patch_id] = {
            "id": patch_id,
            "owner": owner,
            "method": method,
            "kind": spec.get("kind") or "postfix",
            "fn": fn,
            "sig": spec.get("sig") or [],
        }
        return self.patches[patch_id]

    def _handle_patch_del(self, payload: dict) -> bool:
        patch_id = payload.get("patch") or payload.get("id")
        if patch_id not in self.patches:
            raise BridgeError(protocol.ErrorCode.NOT_FOUND, "no patch with id %r" % (patch_id,))
        del self.patches[patch_id]
        return True

    def _handle_doctor(self, payload: dict) -> dict:
        """Canned kernel self-report, shaped like KernelHost.BuildDoctor()."""
        return {
            "proto": protocol.PROTOCOL_VERSION,
            "kernel": KERNEL_VERSION,
            "game": self._game_version + "/mock-unity",
            "stubs_build": self._game_build,
            "connected": not self.bridge.closed,
            "generation": 1,
            "pid": 0,
            "pipe": "mock",
            "caps": list(self._caps),
            "thread": 1,
            "types": 0,
            "globals": [],
            "handles": {"live": 0, "pinned": 0, "allocated": 0, "released": 0, "stale": 0, "dead": 0},
            "patches": {"patches": len(self.patches), "bindings": list(self.patches.values())},
            "dispatcher": {"posted": 0, "executed": 0, "faulted": 0, "pending": 0,
                           "nested_pumps": 0, "nested_refused": 0, "owner_thread": 1},
            "counters": {},
            "trace": [],
        }

    def _on_bye(self, frame) -> None:
        self._closed.set()

    def _on_log(self, frame) -> None:
        try:
            self.logs.append(frame.decode())
        except Exception:  # noqa: BLE001
            pass
