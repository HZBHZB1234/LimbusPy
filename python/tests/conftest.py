"""Shared fixtures for the LimbusPy runtime tests.

Everything here runs with no game, no C# build and no network: transports are backed by
``socket.socketpair()`` which behaves like a duplex byte stream on Windows too.
"""

from __future__ import annotations

import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from limbuspy.runtime.transport import FileTransport  # noqa: E402


def make_transport_pair():
    """Returns two FileTransports connected to each other."""
    left, right = socket.socketpair()
    for sock in (left, right):
        if sock.family == socket.AF_INET:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    left_file = left.makefile("rwb", buffering=0)
    right_file = right.makefile("rwb", buffering=0)

    # socket.makefile() bumps _io_refs, so the real fd only closes once both the file and the
    # socket object are closed. We deliberately keep the socket objects alive and expose them
    # through on_shutdown: closing a socket.makefile() does NOT wake a reader blocked in
    # read() on POSIX (the fd is still referenced by the in-flight syscall), which would leave
    # Bridge's reader thread parked forever. shutdown(SHUT_RDWR) both unblocks our own reader
    # and sends FIN so the peer sees EOF.
    def _shutdown(sock):
        def run():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

        return run

    return (
        FileTransport(left_file, "pair-a", on_shutdown=_shutdown(left)),
        FileTransport(right_file, "pair-b", on_shutdown=_shutdown(right)),
    )


@pytest.fixture()
def transport_pair():
    left, right = make_transport_pair()
    try:
        yield left, right
    finally:
        left.close()
        right.close()
