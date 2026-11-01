"""Framing tests: [u32 LE length][u8 type][payload]."""

from __future__ import annotations

import pytest

from limbuspy.runtime import framing
from limbuspy.runtime.framing import Frame, FrameError


def read_from(buffer: bytes):
    """Builds a read_exact callable over a byte buffer that never returns partial chunks."""
    state = {"offset": 0}

    def read_exact(count: int, allow_empty: bool = False) -> bytes:
        start = state["offset"]
        if start >= len(buffer):
            return b"" if allow_empty else b""
        chunk = buffer[start : start + count]
        state["offset"] = start + len(chunk)
        return chunk

    return read_exact, state


def test_encode_layout():
    data = framing.encode_frame(0x11)
    assert data == b"\x01\x00\x00\x00\x11"

    data = framing.encode_frame(0x04, b"\xc0")
    assert data == b"\x02\x00\x00\x00\x04\xc0"


def test_roundtrip_single():
    payload = b"\x81\xa1a\x01"
    data = framing.encode_frame(0x03, payload)
    read_exact, _ = read_from(data)
    frame = framing.read_frame(read_exact)
    assert frame == Frame(0x03, payload)


def test_roundtrip_many_frames_sticky():
    stream = b"".join(framing.encode_message(0x11) for _ in range(5))
    stream += framing.encode_message(0x0F, {"msg": "hi"})
    read_exact, state = read_from(stream)

    seen = []
    while True:
        frame = framing.read_frame(read_exact)
        if frame is None:
            break
        seen.append(frame)

    assert [f.type for f in seen] == [0x11, 0x11, 0x11, 0x11, 0x11, 0x0F]
    assert seen[-1].decode() == {"msg": "hi"}
    assert state["offset"] == len(stream)


def test_eof_at_frame_boundary_returns_none():
    read_exact, _ = read_from(b"")
    assert framing.read_frame(read_exact) is None


def test_truncated_header_raises():
    read_exact, _ = read_from(b"\x01\x00")
    with pytest.raises(FrameError):
        framing.read_frame(read_exact)


def test_truncated_body_raises():
    data = framing.encode_frame(0x11)
    read_exact, _ = read_from(data[:-1])
    with pytest.raises(FrameError):
        framing.read_frame(read_exact)


def test_zero_length_rejected():
    read_exact, _ = read_from(b"\x00\x00\x00\x00")
    with pytest.raises(FrameError):
        framing.read_frame(read_exact)


def test_oversized_frame_rejected():
    header = (framing.MAX_PAYLOAD_BYTES + 2).to_bytes(4, "little")
    read_exact, _ = read_from(header)
    with pytest.raises(FrameError):
        framing.read_frame(read_exact)


def test_encode_rejects_oversized_payload():
    with pytest.raises(FrameError):
        framing.encode_frame(0x01, b"\x00" * (framing.MAX_PAYLOAD_BYTES + 1))


def test_encode_rejects_bad_type():
    with pytest.raises(FrameError):
        framing.encode_frame(0x100)
    with pytest.raises(FrameError):
        framing.encode_frame(-1)


def test_payload_must_be_map():
    frame = Frame(0x0F, b"\x01")  # a bare int
    with pytest.raises(FrameError):
        frame.decode()


def test_empty_payload_decodes_to_empty_map():
    assert Frame(0x11, b"").decode() == {}


def test_length_prefix_is_little_endian():
    data = framing.encode_frame(0x01, b"\x00" * 300)
    assert data[0:4] == (301).to_bytes(4, "little")
