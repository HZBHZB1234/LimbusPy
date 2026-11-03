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
        ops: Any = None,
        game_build: str = "mock",
        game_version: str = "1.84.0-mock",
        battle: Optional[dict] = None,
    ) -> None:
        self.bridge = Bridge(transport, name="mock-kernel").start()
        self.handles = MockHandles()
        self._handlers: Dict[str, Callable[..., Any]] = dict(handlers or {})
        self._caps = list(caps or [
            protocol.Caps.REFLECT_MANAGED,
            protocol.Caps.HANDLE_ARENA,
            protocol.Caps.HOT_RELOAD,
            protocol.Caps.BATCH_V1,
        ])
        if ops is not None:
            self._caps.append(protocol.Caps.OPS_V1)
        #: A 层 op 的参考实现（MockOps 或 {name: callable(args)}）
        self.ops = ops
        self._game_build = game_build
        self._game_version = game_version
        #: What SNAPSHOT returns (see limbuspy.game.battle).
        self.battle: dict = dict(battle or {"round": 1, "wave": 1, "units": []})
        self.ready: Optional[dict] = None
        self._closed = threading.Event()
        self.bridge.on_request(MsgType.CALL, self._handle_call)
        self.bridge.on_request(MsgType.DOCTOR, self._handle_doctor)
        self.bridge.on_request(MsgType.PATCH_ADD, self._handle_patch_add)
        self.bridge.on_request(MsgType.PATCH_DEL, self._handle_patch_del)
        self.bridge.on_request(MsgType.SNAPSHOT, self._handle_snapshot)
        self.bridge.on_request(MsgType.BATCH, self._handle_batch)
        if ops is not None:
            self.bridge.on_request(MsgType.OPCALL, self._handle_opcall)
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
            "ops": self.ops_manifest(),
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

    def _handle_snapshot(self, payload: dict) -> dict:
        return dict(self.battle)

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

    # -- A 层 op ------------------------------------------------------------------------

    def ops_manifest(self) -> dict:
        """与 ``api/_contract_manifest.json`` 对齐的清单（没有就用 mock 实现的方法名）。"""
        try:
            import json, os

            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "api", "_contract_manifest.json")
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and data.get("ops"):
                return {"version": data.get("version", "1.0"), "ops": data["ops"]}
        except (OSError, ValueError):
            pass
        ops = []
        for name in dir(self.ops or object()):
            if name.startswith("op_"):
                ops.append({"name": name[3:].replace("_", ".")})
        return {"version": "1.0", "ops": ops}

    def _handle_opcall(self, payload: dict) -> Any:
        name = payload.get("op") or ""
        args = payload.get("args") or {}
        if not isinstance(args, dict):
            raise BridgeError(protocol.ErrorCode.MARSHAL, "op args must be an object")
        if self.ops is None:
            raise BridgeError(protocol.ErrorCode.UNSUPPORTED, "mock kernel has no ops")
        dispatch = getattr(self.ops, "dispatch", None)
        try:
            if dispatch is not None:
                return dispatch(name, args)
            handler = self.ops.get(name) if hasattr(self.ops, "get") else None
            if handler is None:
                raise KeyError(name)
            return handler(args)
        except KeyError as exc:
            raise BridgeError(protocol.ErrorCode.UNSUPPORTED,
                              "no op %r (mock)" % (name,)) from exc
        except BridgeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BridgeError(protocol.ErrorCode.SCRIPT, "%s: %s" % (type(exc).__name__, exc)) from exc

    def _handle_batch(self, payload: dict) -> list:
        """``ops: [[target, method, args], ...]`` → 逐项结果（与内核 batch.v1 同形）。"""
        results = []
        for entry in payload.get("ops") or []:
            try:
                target, method, args = entry[0], entry[1], list(entry[2] or [])
                handler = self._handlers.get("%s::%s" % (target, method)) or self._handlers.get(method)
                if handler is None:
                    raise BridgeError(protocol.ErrorCode.NOT_FOUND, "no mock handler for %r" % (method,))
                results.append({"ok": True, "value": handler(*args)})
            except BridgeError as exc:
                results.append({"ok": False, "err": protocol.err(exc.code, exc.message)})
            except Exception as exc:  # noqa: BLE001
                results.append({"ok": False, "err": protocol.err(
                    protocol.ErrorCode.SCRIPT, "%s: %s" % (type(exc).__name__, exc))})
        return results

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
            "ops": self.ops_manifest(),
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
