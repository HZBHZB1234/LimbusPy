"""LPCP value codec: the MessagePack subset frozen by docs/01-protocol.md §4.

This module is deliberately dependency-free (no `msgpack` package required) so that the
"system python" build of LimbusPy works on a bare CPython install. If `msgpack` is
importable we still do NOT swap it in: byte-for-byte identity with the C# side is a
conformance requirement, and our canonicalisation (smallest integer form, float64 only)
must stay under our control.
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Tuple

__all__ = ["CodecError", "pack", "unpack", "unpack_from", "MAX_STR_BYTES", "MAX_BIN_BYTES"]

# The C# writer refuses anything larger than a 16-bit length header.
MAX_STR_BYTES = 0xFFFF
MAX_BIN_BYTES = 0xFFFF
_U8_MAX = 0xFF
_U16_MAX = 0xFFFF
_U32_MAX = 0xFFFFFFFF
_U64_MAX = 0xFFFFFFFFFFFFFFFF
_I8_MIN, _I8_MAX = -0x80, 0x7F
_I16_MIN, _I16_MAX = -0x8000, 0x7FFF
_I32_MIN, _I32_MAX = -0x80000000, 0x7FFFFFFF
_I64_MIN, _I64_MAX = -0x8000000000000000, 0x7FFFFFFFFFFFFFFF


class CodecError(ValueError):
    """Raised for values outside the LPCP subset or malformed payloads."""


# --------------------------------------------------------------------------------------
# packing
# --------------------------------------------------------------------------------------


def pack(value: Any) -> bytes:
    out = bytearray()
    _pack(value, out)
    return bytes(out)


def _pack(value: Any, out: bytearray) -> None:
    # bool must be tested before int: bool is a subclass of int in Python.
    if value is None:
        out.append(0xC0)
        return
    if value is True:
        out.append(0xC3)
        return
    if value is False:
        out.append(0xC2)
        return
    if isinstance(value, int):
        _pack_int(value, out)
        return
    if isinstance(value, float):
        out.append(0xCB)
        out += struct.pack(">d", value)
        return
    if isinstance(value, str):
        _pack_str(value, out)
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        _pack_bin(bytes(value), out)
        return
    if isinstance(value, (list, tuple)):
        _pack_array_header(len(value), out)
        for item in value:
            _pack(item, out)
        return
    if isinstance(value, dict):
        _pack_map_header(len(value), out)
        for key, item in value.items():
            if not isinstance(key, str):
                raise CodecError(
                    "LPCP map keys must be str, got %r (%s)" % (key, type(key).__name__)
                )
            _pack_str(key, out)
            _pack(item, out)
        return

    raise CodecError(
        "value of type %s is not encodable over LPCP: %r" % (type(value).__name__, value)
    )


def _pack_int(value: int, out: bytearray) -> None:
    if value >= 0:
        if value > _U64_MAX:
            raise CodecError("integer out of LPCP range: %d" % value)
        if value <= 0x7F:
            out.append(value)
        elif value <= _U8_MAX:
            out.append(0xCC)
            out.append(value)
        elif value <= _U16_MAX:
            out.append(0xCD)
            out += struct.pack(">H", value)
        elif value <= _U32_MAX:
            out.append(0xCE)
            out += struct.pack(">I", value)
        else:
            out.append(0xCF)
            out += struct.pack(">Q", value)
        return

    if value < _I64_MIN:
        raise CodecError("integer out of LPCP range: %d" % value)
    if value >= -32:
        out.append(value & 0xFF)
    elif value >= _I8_MIN:
        out.append(0xD0)
        out.append(value & 0xFF)
    elif value >= _I16_MIN:
        out.append(0xD1)
        out += struct.pack(">h", value)
    elif value >= _I32_MIN:
        out.append(0xD2)
        out += struct.pack(">i", value)
    else:
        out.append(0xD3)
        out += struct.pack(">q", value)


def _pack_str(value: str, out: bytearray) -> None:
    raw = value.encode("utf-8")
    length = len(raw)
    if length > MAX_STR_BYTES:
        raise CodecError("string too large for LPCP (%d bytes, max %d)" % (length, MAX_STR_BYTES))
    if length <= 31:
        out.append(0xA0 | length)
    elif length <= _U8_MAX:
        out.append(0xD9)
        out.append(length)
    else:
        out.append(0xDA)
        out += struct.pack(">H", length)
    out += raw


def _pack_bin(value: bytes, out: bytearray) -> None:
    length = len(value)
    if length > MAX_BIN_BYTES:
        raise CodecError("binary too large for LPCP (%d bytes, max %d)" % (length, MAX_BIN_BYTES))
    if length <= _U8_MAX:
        out.append(0xC4)
        out.append(length)
    else:
        out.append(0xC5)
        out += struct.pack(">H", length)
    out += value


def _pack_array_header(count: int, out: bytearray) -> None:
    if count <= 15:
        out.append(0x90 | count)
    elif count <= _U16_MAX:
        out.append(0xDC)
        out += struct.pack(">H", count)
    else:
        raise CodecError("array too large for LPCP (%d items, max %d)" % (count, _U16_MAX))


def _pack_map_header(count: int, out: bytearray) -> None:
    if count <= 15:
        out.append(0x80 | count)
    elif count <= _U16_MAX:
        out.append(0xDE)
        out += struct.pack(">H", count)
    else:
        raise CodecError("map too large for LPCP (%d entries, max %d)" % (count, _U16_MAX))


# --------------------------------------------------------------------------------------
# unpacking
# --------------------------------------------------------------------------------------


def unpack(data: bytes) -> Any:
    value, offset = unpack_from(data, 0)
    if offset != len(data):
        raise CodecError("%d trailing bytes after value" % (len(data) - offset))
    return value


def unpack_from(data, offset: int = 0) -> Tuple[Any, int]:
    """Decodes one value. `data` may be bytes/bytearray/memoryview."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise CodecError("unpack_from expects bytes-like, got %s" % type(data).__name__)

    total = len(data)
    if offset >= total:
        raise CodecError("truncated msgpack payload")

    marker = data[offset] & 0xFF
    offset += 1

    if marker <= 0x7F:  # positive fixint
        return marker, offset
    if marker >= 0xE0:  # negative fixint
        return marker - 0x100, offset
    if 0x80 <= marker <= 0x8F:  # fixmap
        return _unpack_map(data, offset, marker & 0x0F, total)
    if 0x90 <= marker <= 0x9F:  # fixarray
        return _unpack_array(data, offset, marker & 0x0F, total)
    if 0xA0 <= marker <= 0xBF:  # fixstr
        return _unpack_str(data, offset, marker & 0x1F, total)

    if marker == 0xC0:
        return None, offset
    if marker == 0xC2:
        return False, offset
    if marker == 0xC3:
        return True, offset
    if marker == 0xCC:
        return _read_uint(data, offset, 1, total)
    if marker == 0xCD:
        return _read_uint(data, offset, 2, total)
    if marker == 0xCE:
        return _read_uint(data, offset, 4, total)
    if marker == 0xCF:
        return _read_uint(data, offset, 8, total)
    if marker == 0xD0:
        return _read_int(data, offset, 1, total)
    if marker == 0xD1:
        return _read_int(data, offset, 2, total)
    if marker == 0xD2:
        return _read_int(data, offset, 4, total)
    if marker == 0xD3:
        return _read_int(data, offset, 8, total)
    if marker == 0xCA:
        value, offset = _read_float(data, offset, 4, total)
        return value, offset
    if marker == 0xCB:
        return _read_float(data, offset, 8, total)
    if marker == 0xC4:
        length, offset = _read_length(data, offset, 1, total)
        return _unpack_bin(data, offset, length, total)
    if marker == 0xC5:
        length, offset = _read_length(data, offset, 2, total)
        return _unpack_bin(data, offset, length, total)
    if marker == 0xD9:
        length, offset = _read_length(data, offset, 1, total)
        return _unpack_str(data, offset, length, total)
    if marker == 0xDA:
        length, offset = _read_length(data, offset, 2, total)
        return _unpack_str(data, offset, length, total)
    if marker == 0xDC:
        length, offset = _read_length(data, offset, 2, total)
        return _unpack_array(data, offset, length, total)
    if marker == 0xDE:
        length, offset = _read_length(data, offset, 2, total)
        return _unpack_map(data, offset, length, total)

    if marker in (0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xC7, 0xC8, 0xC9):
        raise CodecError("msgpack ext types are not supported by LPCP")
    if marker in (0xDB, 0xC6, 0xDD, 0xDF):
        raise CodecError("msgpack 32-bit length headers are not supported by LPCP")

    raise CodecError("unknown msgpack marker 0x%02X" % marker)


def _need(data, offset: int, count: int, total: int) -> None:
    if offset + count > total:
        raise CodecError("truncated msgpack payload (%d bytes needed)" % count)


def _read_length(data, offset: int, width: int, total: int) -> Tuple[int, int]:
    _need(data, offset, width, total)
    return int.from_bytes(data[offset : offset + width], "big"), offset + width


def _read_uint(data, offset: int, width: int, total: int) -> Tuple[int, int]:
    value, offset = _read_length(data, offset, width, total)
    return value, offset


def _read_int(data, offset: int, width: int, total: int) -> Tuple[int, int]:
    value, offset = _read_length(data, offset, width, total)
    sign_bit = 1 << (width * 8 - 1)
    if value & sign_bit:
        value -= 1 << (width * 8)
    return value, offset


def _read_float(data, offset: int, width: int, total: int) -> Tuple[float, int]:
    _need(data, offset, width, total)
    fmt = ">f" if width == 4 else ">d"
    return struct.unpack_from(fmt, data, offset)[0], offset + width


def _unpack_str(data, offset: int, length: int, total: int) -> Tuple[str, int]:
    _need(data, offset, length, total)
    return bytes(data[offset : offset + length]).decode("utf-8", errors="strict"), offset + length


def _unpack_bin(data, offset: int, length: int, total: int) -> Tuple[bytes, int]:
    _need(data, offset, length, total)
    return bytes(data[offset : offset + length]), offset + length


def _unpack_array(data, offset: int, count: int, total: int) -> Tuple[List[Any], int]:
    items: List[Any] = []
    for _ in range(count):
        item, offset = unpack_from(data, offset)
        items.append(item)
    return items, offset


def _unpack_map(data, offset: int, count: int, total: int) -> Tuple[Dict[str, Any], int]:
    result: Dict[str, Any] = {}
    for _ in range(count):
        key, offset = unpack_from(data, offset)
        value, offset = unpack_from(data, offset)
        if not isinstance(key, str):
            # Mirrors the C# reader (seldom used: LPCP payload maps are string-keyed).
            key = _stringify(key)
        result[key] = value
    return result, offset


def _stringify(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "True"
    if value is False:
        return "False"
    return str(value)
