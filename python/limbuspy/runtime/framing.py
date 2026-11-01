"""LPCP framing: [u32 LE length][u8 type][payload]  (docs/01-protocol.md §2)."""

from __future__ import annotations

import struct
from typing import Optional

from . import codec

__all__ = [
    "MAX_PAYLOAD_BYTES",
    "HEADER_BYTES",
    "Frame",
    "FrameError",
    "encode_frame",
    "read_frame",
]

MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
_MAX_FRAME_LENGTH = 1 + MAX_PAYLOAD_BYTES
HEADER_BYTES = 5
_U32_LE = struct.Struct("<I")


class FrameError(Exception):
    """Malformed frame or broken framing state (fatal for the connection)."""


class Frame:
    """One LPCP frame."""

    __slots__ = ("type", "payload")

    def __init__(self, type_: int, payload: bytes = b"") -> None:  # noqa: A002 - keep wire name
        self.type = int(type_)
        self.payload = payload or b""

    # -- payload helpers ---------------------------------------------------------------

    def decode(self) -> dict:
        """Decodes the payload as a string-keyed map (missing payload -> empty map)."""
        if not self.payload:
            return {}
        value = codec.unpack(self.payload)
        if not isinstance(value, dict):
            raise FrameError(
                "LPCP payload must be a map, got %s" % type(value).__name__
            )
        return value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Frame(type=0x%02X, payload=%dB)" % (self.type, len(self.payload))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Frame)
            and other.type == self.type
            and other.payload == self.payload
        )


def encode_frame(type_: int, payload: bytes = b"") -> bytes:
    """Serialises one frame. `payload` must already be msgpack-encoded."""
    payload = payload or b""
    length = 1 + len(payload)
    if length > _MAX_FRAME_LENGTH:
        raise FrameError("frame too large: %d bytes" % length)
    if not 0 <= int(type_) <= 0xFF:
        raise FrameError("message type out of range: %r" % (type_,))
    return _U32_LE.pack(length) + bytes((int(type_) & 0xFF,)) + payload


def encode_message(type_: int, message: Optional[dict] = None) -> bytes:
    """Convenience: msgpack-encode a map payload and frame it."""
    payload = codec.pack(message) if message else b""
    return encode_frame(type_, payload)


def read_frame(read_exact) -> Optional[Frame]:
    """
    Reads one frame using a caller-supplied `read_exact(n) -> bytes` callable.

    Returns None on clean EOF at a frame boundary (no bytes read at all), raises
    FrameError on partial reads / invalid lengths.
    """
    header = read_exact(4, allow_empty=True)
    if header is None or len(header) == 0:
        return None
    if len(header) != 4:
        raise FrameError("truncated frame header (%d/4 bytes)" % len(header))

    (length,) = _U32_LE.unpack(header)
    if length == 0:
        raise FrameError("frame length must be >= 1")
    if length > _MAX_FRAME_LENGTH:
        raise FrameError("frame too large: %d bytes" % length)

    body = read_exact(length)
    if body is None or len(body) != length:
        raise FrameError(
            "truncated frame body (%d/%d bytes)" % (len(body or b""), length)
        )

    return Frame(body[0], body[1:])
