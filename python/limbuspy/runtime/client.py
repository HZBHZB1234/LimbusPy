"""LPCP client: the Python side of the bridge.

Threading model (important, and mirrored by docs/01-protocol.md §6):

* **reader thread** - owns the transport, reads frames, correlates results with pending
  requests, and enqueues everything else. It must never block on the kernel, otherwise a
  reply could not be received.
* **handler thread** - executes inbound work in arrival order (event batches, reloads,
  kernel-initiated requests such as ``PATCH_ADD``/``SYNCALL``). Kernel-initiated sync calls
  are drained from a high-priority queue so a busy event batch cannot starve them.
* **caller threads** - anything that calls :meth:`Bridge.call` (a script loop, a user thread)
  just parks on a future; it is woken by the reader thread.

This split is what makes "hook -> call back into the kernel" legal without deadlocking: the
handler thread blocks on the future while the reader thread keeps receiving.
"""

from __future__ import annotations

import collections
import platform
import queue
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import framing, protocol
from .codec import CodecError
from .errors import (
    BridgeError,
    LimbusPyError,
    PeerClosedError,
    TimeoutError_,
    TransportError,
    error_from_payload,
)
from .framing import Frame, FrameError
from .protocol import MsgType
from .transport import Transport

__all__ = ["Bridge", "BridgeCounters"]


DEFAULT_TIMEOUT = 5.0
SYNC_TIMEOUT = 10.0


class BridgeCounters:
    """Cheap traffic counters surfaced through ``STATS`` and ``limbuspy doctor``."""

    __slots__ = ("frames_sent", "frames_received", "bytes_sent", "bytes_received", "calls",
                 "timeouts", "errors", "reconnects")

    def __init__(self) -> None:
        self.frames_sent = 0
        self.frames_received = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        self.calls = 0
        self.timeouts = 0
        self.errors = 0
        self.reconnects = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "frames_sent": self.frames_sent,
            "frames_received": self.frames_received,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "calls": self.calls,
            "timeouts": self.timeouts,
            "errors": self.errors,
            "reconnects": self.reconnects,
        }


class _Inbox:
    """Priority inbox: kernel-initiated sync calls jump ahead of batched event work."""

    __slots__ = ("_cond", "_high", "_normal", "_closed")

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._high: collections.deque = collections.deque()
        self._normal: collections.deque = collections.deque()
        self._closed = False

    def put(self, item: Tuple[int, Frame], high: bool = False) -> None:
        with self._cond:
            (self._high if high else self._normal).append(item)
            self._cond.notify()

    def get(self) -> Optional[Tuple[int, Frame]]:
        with self._cond:
            while not self._high and not self._normal and not self._closed:
                self._cond.wait(0.25)
            if self._closed and not self._high and not self._normal:
                return None
            return self._high.popleft() if self._high else self._normal.popleft()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


class Bridge:
    """One connection to the LimbusPy kernel."""

    def __init__(self, transport: Transport, name: str = "limbuspy") -> None:
        self._transport = transport
        self.name = name
        self.counters = BridgeCounters()

        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: Dict[int, Future] = {}
        self._next_id = 0

        self._wait_lock = threading.Lock()
        self._waiters: Dict[int, Future] = {}
        self._early: Dict[int, Frame] = {}

        self._request_handlers: Dict[int, Callable[[dict], Any]] = {}
        self._listeners: Dict[int, List[Callable[[Frame], None]]] = {}

        self._inbox = _Inbox()
        self._reader: Optional[threading.Thread] = None
        self._handler: Optional[threading.Thread] = None
        self._closed = threading.Event()
        self._close_reason: Optional[str] = None
        self._fatal_error: Optional[BaseException] = None

        self.hello: Optional[dict] = None
        self.ready_sent = False
        self.py_caps: List[str] = []

    # -- lifecycle ---------------------------------------------------------------------

    @classmethod
    def connect_pipe(cls, pipe_prefix: str, timeout: float = DEFAULT_TIMEOUT, **kwargs: Any) -> "Bridge":
        """Connects to a kernel endpoint (``limbuspy.<pid>``) over its two one-way pipes.

        The returned bridge is already running (reader + handler threads started); use
        :meth:`from_transport` if you need to wire things up before starting.
        """
        from .transport import connect_pipe_pair

        return cls(connect_pipe_pair(pipe_prefix, timeout=timeout), **kwargs).start()

    @classmethod
    def connect_tcp(cls, host: str = "127.0.0.1", port: int = 0, timeout: float = DEFAULT_TIMEOUT,
                    **kwargs: Any) -> "Bridge":
        from .transport import connect_tcp

        return cls(connect_tcp(host, port, timeout=timeout), **kwargs).start()

    @classmethod
    def from_transport(cls, transport: Transport, **kwargs: Any) -> "Bridge":
        return cls(transport, **kwargs)

    def start(self) -> "Bridge":
        if self._reader is not None:
            return self
        self._handler = threading.Thread(target=self._handler_loop, name="limbuspy-handler", daemon=True)
        self._handler.start()
        self._reader = threading.Thread(target=self._reader_loop, name="limbuspy-reader", daemon=True)
        self._reader.start()
        return self

    def close(self, reason: str = "closed by client") -> None:
        if self._closed.is_set():
            return
        self._close_reason = reason
        try:
            self._send(MsgType.BYE, {"reason": reason})
        except LimbusPyError:
            pass
        self._shutdown()

    def _shutdown(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._inbox.close()
        try:
            self._transport.close()
        except Exception:  # noqa: BLE001
            pass
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(PeerClosedError("bridge closed: %s" % (self._close_reason or "eof")))
        with self._wait_lock:
            for future in self._waiters.values():
                if not future.done():
                    future.set_exception(PeerClosedError("bridge closed: %s" % (self._close_reason or "eof")))
            self._waiters.clear()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # -- capability negotiation ---------------------------------------------------------

    @property
    def peer_caps(self) -> List[str]:
        """Capabilities declared by the kernel in HELLO."""
        return [cap for cap in (self.hello or {}).get("caps") or [] if isinstance(cap, str)]

    def has_cap(self, name: str) -> bool:
        return name in self.peer_caps

    def shared_caps(self) -> List[str]:
        """Capabilities supported by both sides (intersection)."""
        remote = set(self.peer_caps)
        return [cap for cap in self.py_caps if cap in remote]

    def wait_closed(self, timeout: Optional[float] = None) -> bool:
        return self._closed.wait(timeout)

    def __enter__(self) -> "Bridge":
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- sending -----------------------------------------------------------------------

    def _next_request_id(self) -> int:
        with self._write_lock:
            self._next_id += 1
            return self._next_id

    def _send_frame(self, type_: int, payload: bytes) -> None:
        if self._closed.is_set():
            raise PeerClosedError(
                "bridge is closed (%s)" % (self._close_reason or "unknown reason")
            )
        data = framing.encode_frame(type_, payload)
        with self._write_lock:
            self._transport.write(data)
            self.counters.frames_sent += 1
            self.counters.bytes_sent += len(data)

    def _send(self, type_: int, message: Optional[dict] = None) -> None:
        self._send_frame(type_, framing.codec.pack(message) if message else b"")

    def notify(self, type_: int, message: Optional[dict] = None) -> None:
        """Fire-and-forget message (no reply expected)."""
        self._send(type_, message)

    def request(self, type_: int, payload: Optional[dict] = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
        """
        Sends a request frame and waits for the matching CALLRESULT/SYNCRESULT payload.

        Returns the raw result map; raises :class:`BridgeError` when ``ok`` is false and
        :class:`TimeoutError_` when no reply arrives in time.
        """
        request_id = self._next_request_id()
        message = dict(payload or {})
        message["id"] = request_id

        future: Future = Future()
        with self._pending_lock:
            self._pending[request_id] = future
        try:
            self._send(type_, message)
            try:
                result = future.result(timeout=timeout)
            except TimeoutError:
                self.counters.timeouts += 1
                raise TimeoutError_(
                    "%s id=%d timed out after %.3fs" % (MsgType(type_).name, request_id, timeout)
                ) from None
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

        if not result.get("ok", False):
            self.counters.errors += 1
            raise error_from_payload(result.get("err"))
        return result

    def call(self, target: str, method: str, *args: Any, timeout: float = DEFAULT_TIMEOUT) -> Any:
        """Invokes ``method`` on ``target`` inside the game (reflection path)."""
        self.counters.calls += 1
        result = self.request(
            MsgType.CALL,
            {"target": target or "", "method": method or "", "args": list(args)},
            timeout=timeout,
        )
        return result.get("value")

    # -- harmony patches ---------------------------------------------------------------

    def patch(self, owner: str, method: str, fn: str, kind: str = "postfix",
              sig: Optional[List[str]] = None, fallback: Any = None,
              timeout: float = DEFAULT_TIMEOUT) -> dict:
        """Asks the kernel to patch ``owner.method`` and dispatch to ``fn`` on every call.

        ``sig`` is the parameter type list used to disambiguate overloads (``["int"]``); pass it
        whenever the name is overloaded - the kernel refuses to guess.

        Returns the binding (``id``, ``target``, ``kind``, ``fn``). Keep the ``id``: that is how
        you remove the patch again.
        """
        payload: Dict[str, Any] = {
            "owner": owner,
            "method": method,
            "kind": kind,
            "fn": fn,
            "sig": list(sig or []),
        }
        if fallback is not None:
            payload["fallback"] = fallback
        result = self.request(MsgType.PATCH_ADD, payload, timeout=timeout)
        return dict(result.get("value") or {})

    def unpatch(self, patch_id: str, timeout: float = DEFAULT_TIMEOUT) -> bool:
        result = self.request(MsgType.PATCH_DEL, {"patch": patch_id}, timeout=timeout)
        return bool(result.get("value"))

    def patches(self, timeout: float = DEFAULT_TIMEOUT) -> List[dict]:
        """Current bindings, read from the kernel's DOCTOR report."""
        result = self.request(MsgType.DOCTOR, {}, timeout=timeout)
        doctor = result.get("value") or {}
        stats = doctor.get("patches") or {}
        bindings = stats.get("bindings") or []
        return [binding for binding in bindings if isinstance(binding, dict)]

    # -- receiving ---------------------------------------------------------------------

    def on_request(self, type_: int, handler: Callable[[dict], Any]) -> None:
        """Registers the handler for a kernel-initiated request (its return value is replied)."""
        self._request_handlers[int(type_)] = handler

    def on(self, type_: int, handler: Callable[[Frame], None]) -> None:
        """Registers a notification listener (executed on the handler thread)."""
        self._listeners.setdefault(int(type_), []).append(handler)

    def wait_for(self, type_: int, timeout: float) -> Frame:
        """Waits for the next frame of ``type_`` (buffered frames are honoured)."""
        with self._wait_lock:
            buffered = self._early.pop(int(type_), None)
            if buffered is not None:
                return buffered
            future: Future = Future()
            self._waiters[int(type_)] = future
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            raise TimeoutError_("waiting for %s timed out after %.3fs" % (MsgType(type_).name, timeout)) from None
        finally:
            with self._wait_lock:
                self._waiters.pop(int(type_), None)

    # -- handshake ---------------------------------------------------------------------

    def handshake(self, mods: Optional[List[str]] = None, stubs_build: str = "",
                  timeout: float = 3.0) -> dict:
        """
        Performs the HELLO/READY handshake and returns the kernel's HELLO payload.

        Raises :class:`BridgeError` with code ``proto`` when the peer speaks an incompatible
        major version; the transport is closed in that case.
        """
        frame = self.wait_for(MsgType.HELLO, timeout=timeout)
        hello = frame.decode()
        peer_version = hello.get("proto")
        if not protocol.is_compatible(peer_version):
            self._send(MsgType.ERROR, protocol.err(
                protocol.ErrorCode.PROTO,
                "incompatible protocol: kernel speaks %r, limbuspy speaks %r"
                % (peer_version, protocol.PROTOCOL_VERSION),
                {"peer": peer_version, "self": protocol.PROTOCOL_VERSION, "fatal": True},
            ))
            self._close_reason = "protocol mismatch"
            self._shutdown()
            raise BridgeError(
                protocol.ErrorCode.PROTO,
                "incompatible protocol: kernel speaks %r, limbuspy speaks %r"
                % (peer_version, protocol.PROTOCOL_VERSION),
                {"peer": peer_version, "self": protocol.PROTOCOL_VERSION},
            )

        self.hello = hello
        self._send(MsgType.READY, {
            "proto": protocol.PROTOCOL_VERSION,
            "py": "%d.%d.%d" % sys.version_info[:3],
            "impl": platform.python_implementation(),
            "mods": list(mods or []),
            "stubs_build": stubs_build,
            "caps": list(self.py_caps),
        })
        self.ready_sent = True
        return hello

    # -- reader / handler loops --------------------------------------------------------

    def _reader_loop(self) -> None:
        try:
            while not self._closed.is_set():
                frame = framing.read_frame(self._transport.read_exact)
                if frame is None:
                    self._close_reason = "peer closed the connection"
                    break
                self.counters.frames_received += 1
                self.counters.bytes_received += len(frame.payload) + framing.HEADER_BYTES
                self._route(frame)
        except FrameError as exc:
            self._fatal_error = exc
            self._close_reason = "framing error: %s" % exc
        except TransportError as exc:
            self._fatal_error = exc
            self._close_reason = "transport error: %s" % exc
        except Exception as exc:  # noqa: BLE001 - never kill the process from the reader
            self._fatal_error = exc
            self._close_reason = "reader error: %s" % exc
        finally:
            self._shutdown()

    def _route(self, frame: Frame) -> None:
        type_ = frame.type

        if type_ in protocol.RESULT_MESSAGES:
            self._resolve_result(frame)
            return
        if type_ == MsgType.PING:
            self._send(MsgType.PONG)
            return
        if type_ in protocol.REQUEST_MESSAGES or type_ == MsgType.SYNCALL:
            self._inbox.put((type_, frame), high=(type_ == MsgType.SYNCALL))
            return
        if type_ == MsgType.ERROR:
            payload = _safe_decode(frame)
            if payload and payload.get("fatal"):
                self._fatal_error = BridgeError.from_payload(payload)
                self._close_reason = "kernel reported fatal error: %s" % payload.get("msg")
        self._deliver(type_, frame)

    def _resolve_result(self, frame: Frame) -> None:
        payload = _safe_decode(frame)
        if not payload:
            return
        request_id = payload.get("id")
        if not isinstance(request_id, int):
            return
        with self._pending_lock:
            future = self._pending.pop(request_id, None)
        if future is None:
            return  # orphan (late reply after timeout) - ignore
        future.set_result(payload)

    def _deliver(self, type_: int, frame: Frame) -> None:
        with self._wait_lock:
            future = self._waiters.get(int(type_))
            if future is None:
                self._early[int(type_)] = frame
                if len(self._early) > 64:  # bound the buffer
                    self._early.pop(next(iter(self._early)))
        if future is not None and not future.done():
            future.set_result(frame)
            return
        for listener in list(self._listeners.get(int(type_), ())):
            self._inbox.put((listener, frame))

    def _handler_loop(self) -> None:
        while True:
            item = self._inbox.get()
            if item is None:
                return
            try:
                if isinstance(item[0], int):
                    self._execute_inbound(item[0], item[1])
                else:
                    item[0](item[1])
            except Exception:  # noqa: BLE001 - isolate user/script failures
                traceback.print_exc()

    def _execute_inbound(self, type_: int, frame: Frame) -> None:
        payload = _safe_decode(frame)
        request_id = payload.get("id")
        handler = self._request_handlers.get(int(type_))
        reply_type = MsgType.SYNCRESULT if type_ == MsgType.SYNCALL else MsgType.CALLRESULT

        if handler is None:
            self._reply(reply_type, request_id, error=protocol.err(
                protocol.ErrorCode.UNSUPPORTED, "no handler registered for %s" % MsgType(type_).name))
            return

        try:
            value = handler(payload)
        except BridgeError as exc:
            self._reply(reply_type, request_id, error=protocol.err(exc.code, exc.message, exc.detail))
            return
        except Exception as exc:  # noqa: BLE001
            self.counters.errors += 1
            self._reply(reply_type, request_id, error=protocol.err(
                protocol.ErrorCode.SCRIPT, "%s: %s" % (type(exc).__name__, exc),
                {"traceback": traceback.format_exc(limit=8)}))
            return

        self._reply(reply_type, request_id, value=value)

    def _reply(self, reply_type: int, request_id: Optional[int], value: Any = None,
               error: Optional[dict] = None) -> None:
        if not isinstance(request_id, int):
            return
        message = {"id": request_id}
        if error is not None:
            message["ok"] = False
            message["err"] = error
        else:
            message["ok"] = True
            message["value"] = value
        try:
            self._send(reply_type, message)
        except LimbusPyError:
            pass

    # -- diagnostics -------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "transport": self._transport.name,
            "closed": self.closed,
            "close_reason": self._close_reason,
            "counters": self.counters.as_dict(),
            "hello": self.hello,
        }


def _safe_decode(frame: Frame) -> dict:
    try:
        return frame.decode()
    except (CodecError, FrameError):
        return {}
