"""Canonical-encoding tests for the LPCP msgpack subset.

The golden byte strings here are the *contract*: the C# writer must produce identical bytes,
which ``tools/conformance.py`` verifies across processes.
"""

from __future__ import annotations

import pytest

from limbuspy.runtime import codec

GOLDEN = [
    (None, "c0"),
    (False, "c2"),
    (True, "c3"),
    (0, "00"),
    (1, "01"),
    (127, "7f"),
    (128, "cc80"),
    (255, "ccff"),
    (256, "cd0100"),
    (65535, "cdffff"),
    (65536, "ce00010000"),
    (4294967295, "ceffffffff"),
    (4294967296, "cf0000000100000000"),
    (18446744073709551615, "cfffffffffffffffff"),
    (-1, "ff"),
    (-32, "e0"),
    (-33, "d0df"),
    (-128, "d080"),
    (-129, "d1ff7f"),
    (-32768, "d18000"),
    (-32769, "d2ffff7fff"),
    (-2147483648, "d280000000"),
    (-2147483649, "d3ffffffff7fffffff"),
    (-9223372036854775808, "d38000000000000000"),
    (1.5, "cb3ff8000000000000"),
    (0.0, "cb0000000000000000"),
    ("", "a0"),
    ("a", "a161"),
    ("中", "a3e4b8ad"),
    (b"", "c400"),
    (b"\x01\x02", "c4020102"),
    ([], "90"),
    ([1, 2], "920102"),
    ({}, "80"),
    ({"a": 1}, "81a16101"),
]


@pytest.mark.parametrize("value,expected_hex", GOLDEN)
def test_canonical_encoding(value, expected_hex):
    assert codec.pack(value).hex() == expected_hex


@pytest.mark.parametrize("value,expected_hex", GOLDEN)
def test_roundtrip(value, expected_hex):
    decoded = codec.unpack(bytes.fromhex(expected_hex))
    if isinstance(value, bool):
        assert decoded is value
    elif isinstance(value, float):
        assert decoded == value
    else:
        assert decoded == value
        assert type(decoded) is type(value) or (
            isinstance(value, (bytes, bytearray)) and isinstance(decoded, bytes)
        )


def test_str_boundaries():
    assert codec.pack("x" * 31)[:1] == b"\xbf"
    assert codec.pack("x" * 32)[:1] == b"\xd9"
    assert codec.pack("x" * 32)[:2] == b"\xd9\x20"
    assert codec.pack("x" * 256)[:1] == b"\xda"
    assert codec.pack("x" * 256)[:3] == b"\xda\x01\x00"


def test_array_and_map_boundaries():
    assert codec.pack([0] * 15)[:1] == b"\x9f"
    assert codec.pack([0] * 16)[:1] == b"\xdc"
    assert codec.pack({("k%d" % i): 0 for i in range(15)})[:1] == b"\x8f"
    assert codec.pack({("k%d" % i): 0 for i in range(16)})[:1] == b"\xde"


def test_nested_structure_roundtrip():
    value = {
        "proto": "1.0",
        "caps": ["reflect.managed", "handle.arena"],
        "ok": True,
        "value": {"t": "unit", "h": 305419896},
        "nested": [1, [2, [3, {"deep": None}]]],
        "raw": b"\x00\xff",
        "f": 1.25,
        "big": 2**63,
        "neg": -(2**63),
    }
    assert codec.unpack(codec.pack(value)) == value


def test_non_string_keys_rejected():
    with pytest.raises(codec.CodecError):
        codec.pack({1: "x"})


def test_int_out_of_range_rejected():
    with pytest.raises(codec.CodecError):
        codec.pack(2**64)
    with pytest.raises(codec.CodecError):
        codec.pack(-(2**63) - 1)


def test_unsupported_python_type_rejected():
    with pytest.raises(codec.CodecError):
        codec.pack(object())


def test_oversized_string_rejected():
    with pytest.raises(codec.CodecError):
        codec.pack("x" * 70000)


def test_decoder_rejects_ext_and_32bit_headers():
    for bad in (b"\xd4", b"\xc7", b"\xdb", b"\xc6", b"\xdd", b"\xdf"):
        with pytest.raises(codec.CodecError):
            codec.unpack(bad)


def test_decoder_rejects_truncated():
    with pytest.raises(codec.CodecError):
        codec.unpack(b"\xcd\x01")  # uint16 with only one byte
    with pytest.raises(codec.CodecError):
        codec.unpack(b"\x92\x01")  # array of 2 with one element
    with pytest.raises(codec.CodecError):
        codec.unpack(b"")


def test_decoder_rejects_trailing_bytes():
    with pytest.raises(codec.CodecError):
        codec.unpack(b"\x01\x02")


def test_decoder_accepts_float32():
    # 0x3FC00000 == 1.5f
    assert codec.unpack(bytes.fromhex("ca3fc00000")) == 1.5


def test_unicode_roundtrip_including_emoji():
    for text in ("边狱公司", "Limbus", "🎲", "a\u0000b"):
        assert codec.unpack(codec.pack(text)) == text


def test_unpack_from_offset():
    data = b"\x00\x01\x02"
    value, offset = codec.unpack_from(data, 1)
    assert (value, offset) == (1, 2)
