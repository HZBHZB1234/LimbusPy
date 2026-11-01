"""Transports for the LPCP bridge.

A transport is a byte stream with ``read_exact`` / ``write``. Framing lives in
:mod:`limbuspy.runtime.framing`; the client never sees raw bytes.

Implemented transports:

* :class:`PipeTransport`  - Windows named pipe (primary; matches the C# kernel)
* :class:`TcpTransport`   - loopback TCP (fallback / debugging)
* :class:`FileTransport`  - anything file-like (used by the mock kernel and tests)
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Optional

from .errors import TransportError

__all__ = [
    "Transport",
    "FileTransport",
    "PipeTransport",
    "SplitTransport",
    "TcpTransport",
    "pipe_name_for_process",
    "to_py_pipe",
    "from_py_pipe",
    "connect_pipe",
    "connect_pipe_pair",
    "connect_tcp",
]


class Transport:
    """Byte-stream contract used by the frame layer."""

    name: str = "<transport>"

    def read_exact(self, count: int, allow_empty: bool = False) -> bytes:
        """Reads exactly ``count`` bytes. With ``allow_empty`` returns b'' on EOF."""
        raise NotImplementedError

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    @property
    def closed(self) -> bool:
        raise NotImplementedError


class FileTransport(Transport):
    """Transport over a file-like object (pipes opened via ``open()``, socket makefiles)."""

    def __init__(self, fileobj, name: str = "<file>", on_close=None) -> None:
        self._file = fileobj
        self._on_close = on_close
        self.name = name
        self._closed = False

    def read_exact(self, count: int, allow_empty: bool = False) -> bytes:
        if count <= 0:
            return b""
        chunks = []
        remaining = count
        while remaining > 0:
            try:
                chunk = self._file.read(remaining)
            except (OSError, ValueError) as exc:
                raise TransportError("%s read failed: %s" % (self.name, exc)) from exc
            if not chunk:
                if remaining == count and allow_empty:
                    return b""
                raise TransportError(
                    "%s closed mid-frame (%d/%d bytes read)" % (self.name, count - remaining, count)
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def write(self, data: bytes) -> None:
        if self._closed:
            raise TransportError("%s is closed" % self.name)
        view = memoryview(data)
        sent = 0
        while sent < len(view):
            try:
                written = self._file.write(view[sent:])
            except (OSError, ValueError) as exc:
                raise TransportError("%s write failed: %s" % (self.name, exc)) from exc
            if written is None:  # pragma: no cover - some file-likes return None
                written = len(view) - sent
            if written <= 0:
                raise TransportError("%s write returned %d" % (self.name, written))
            sent += written
        try:
            flush = getattr(self._file, "flush", None)
            if flush is not None:
                flush()
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._file.close()
        except Exception:  # noqa: BLE001 - best effort
            pass
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def closed(self) -> bool:
        return self._closed


class PipeTransport(FileTransport):
    """Windows named pipe client, single duplex handle.

    NOTE: a single duplex handle cannot be used for a pending blocking read *and* a write from
    different threads on Windows (the write fails with ERROR_INVALID_PARAMETER). Use
    :class:`SplitTransport` via :func:`connect_pipe_pair`; this class is only for one-directional
    diagnostics.
    """

    def __init__(self, fileobj, pipe_name: str) -> None:
        super().__init__(fileobj, "pipe:" + pipe_name)
        self.pipe_name = pipe_name


class SplitTransport(Transport):
    """Duplex transport over two one-way streams (one named pipe per direction).

    This is the production pipe transport: the kernel exposes ``<prefix>.toPy`` (it writes, we
    read) and ``<prefix>.fromPy`` (we write, it reads), so the reader thread never shares a
    handle with the writer thread.
    """

    def __init__(self, rx_file, tx_file, prefix: str, on_close=None) -> None:
        self._rx = rx_file
        self._tx = tx_file
        self._on_close = on_close
        self.pipe_prefix = prefix
        self.name = "pipe:" + prefix
        self._closed = False

    def read_exact(self, count: int, allow_empty: bool = False) -> bytes:
        if count <= 0:
            return b""
        chunks = []
        remaining = count
        while remaining > 0:
            try:
                chunk = self._rx.read(remaining)
            except (OSError, ValueError) as exc:
                raise TransportError("%s read failed: %s" % (self.name, exc)) from exc
            if not chunk:
                if remaining == count and allow_empty:
                    return b""
                raise TransportError(
                    "%s closed mid-frame (%d/%d bytes read)" % (self.name, count - remaining, count)
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def write(self, data: bytes) -> None:
        if self._closed:
            raise TransportError("%s is closed" % self.name)
        sent = 0
        view = memoryview(data)
        while sent < len(view):
            try:
                written = self._tx.write(view[sent:])
            except (OSError, ValueError) as exc:
                raise TransportError("%s write failed: %s" % (self.name, exc)) from exc
            if written is None:  # pragma: no cover
                written = len(view) - sent
            if written <= 0:
                raise TransportError("%s write returned %d" % (self.name, written))
            sent += written
        try:
            self._tx.flush()
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Close the write side first: the kernel then sees EOF and closes its own ends, which
        # makes our pending blocking read return. Closing the read handle while a ReadFile is
        # pending on it can deadlock, so the read side is closed out-of-band by a daemon thread.
        try:
            self._tx.close()
        except Exception:  # noqa: BLE001
            pass
        threading.Thread(target=self._close_read_side, name="limbuspy-rx-close", daemon=True).start()

    def _close_read_side(self) -> None:
        # Give the reader thread a moment to observe EOF on its own.
        time.sleep(0.2)
        try:
            self._rx.close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def closed(self) -> bool:
        return self._closed


class TcpTransport(Transport):
    """Loopback TCP transport."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._closed = False
        host, port = sock.getpeername()[:2]
        self.name = "tcp:%s:%s" % (host, port)

    def read_exact(self, count: int, allow_empty: bool = False) -> bytes:
        if count <= 0:
            return b""
        chunks = []
        remaining = count
        while remaining > 0:
            try:
                chunk = self._sock.recv(remaining)
            except OSError as exc:
                raise TransportError("%s recv failed: %s" % (self.name, exc)) from exc
            if not chunk:
                if remaining == count and allow_empty:
                    return b""
                raise TransportError(
                    "%s closed mid-frame (%d/%d bytes read)" % (self.name, count - remaining, count)
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def write(self, data: bytes) -> None:
        if self._closed:
            raise TransportError("%s is closed" % self.name)
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise TransportError("%s send failed: %s" % (self.name, exc)) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def closed(self) -> bool:
        return self._closed


# --------------------------------------------------------------------------------------
# connecting
# --------------------------------------------------------------------------------------


def pipe_name_for_process(process_id: int) -> str:
    """``limbuspy.<pid>`` - mirrors ``NamedPipeTransport.NameForProcess`` on the C# side."""
    return "limbuspy.%d" % int(process_id)


def to_py_pipe(prefix: str) -> str:
    """Pipe the kernel writes to (we read)."""
    return prefix + ".toPy"


def from_py_pipe(prefix: str) -> str:
    """Pipe we write to (the kernel reads)."""
    return prefix + ".fromPy"


def _pipe_path(pipe_name: str) -> str:
    return r"\\.\pipe\%s" % pipe_name


def _open_with_retry(pipe_name: str, mode: str, timeout: float):
    path = _pipe_path(pipe_name)
    deadline = time.monotonic() + max(0.0, timeout)
    last_error = None
    while True:
        try:
            # buffering=0 -> raw FileIO: every read() maps to one ReadFile call
            return open(path, mode, buffering=0)
        except OSError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                raise TransportError(
                    "could not connect to pipe %r within %.1fs: %s" % (path, timeout, last_error)
                ) from last_error
            time.sleep(0.02)


def connect_pipe_pair(prefix: str, timeout: float = 5.0) -> SplitTransport:
    """Connects both one-way pipes of a kernel endpoint and returns a duplex transport."""
    if os.name != "nt":  # pragma: no cover - the kernel only exists on Windows
        raise TransportError("named pipes are only supported on Windows")

    rx = _open_with_retry(to_py_pipe(prefix), "rb", timeout)
    try:
        tx = _open_with_retry(from_py_pipe(prefix), "wb", timeout)
    except Exception:
        rx.close()
        raise
    return SplitTransport(rx, tx, prefix)


def connect_pipe(pipe_name: str, timeout: float = 5.0) -> PipeTransport:
    """Single duplex handle. Only for one-directional diagnostics - see PipeTransport."""
    fileobj = _open_with_retry(pipe_name, "r+b", timeout)
    return PipeTransport(fileobj, pipe_name)


def connect_tcp(host: str = "127.0.0.1", port: int = 0, timeout: float = 5.0) -> TcpTransport:
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
    except OSError as exc:
        raise TransportError("could not connect to %s:%s: %s" % (host, port, exc)) from exc
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return TcpTransport(sock)
