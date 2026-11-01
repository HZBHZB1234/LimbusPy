"""Single source of truth for the LPCP canonical-encoding golden table.

Both the Python tests / conformance runner and the C# harness use this table; the harness
embeds an identical copy, so a mismatch means one of the two implementations drifted.

``canonical=True`` cases are compared byte-for-byte across languages (all keys of a map are
single anyway, so ordering cannot differ). Multi-key structures are compared after decoding,
because map ordering is not part of the contract.
"""

from __future__ import annotations

CASES = [
    # label, value, canonical (byte-comparable across languages)
    ("nil", None, True),
    ("false", False, True),
    ("true", True, True),
    ("int_0", 0, True),
    ("int_1", 1, True),
    ("int_127", 127, True),
    ("int_128", 128, True),
    ("int_255", 255, True),
    ("int_256", 256, True),
    ("int_65535", 65535, True),
    ("int_65536", 65536, True),
    ("int_u32_max", 4294967295, True),
    ("int_u32_over", 4294967296, True),
    ("int_u64_max", 18446744073709551615, True),
    ("int_m1", -1, True),
    ("int_m32", -32, True),
    ("int_m33", -33, True),
    ("int_m128", -128, True),
    ("int_m129", -129, True),
    ("int_m32768", -32768, True),
    ("int_m32769", -32769, True),
    ("int_m2p31", -2147483648, True),
    ("int_m2p31_minus1", -2147483649, True),
    ("int_i64_min", -9223372036854775808, True),
    ("f64_1_5", 1.5, True),
    ("f64_0", 0.0, True),
    ("str_empty", "", True),
    ("str_a", "a", True),
    ("str_zh", "边狱公司", True),
    ("str_31", "x" * 31, True),
    ("str_32", "x" * 32, True),
    ("str_256", "x" * 256, True),
    ("bin_empty", b"", True),
    ("bin_2", b"\x01\x02", True),
    ("arr_empty", [], True),
    ("arr_1_2", [1, 2], True),
    ("map_empty", {}, True),
    ("map_a1", {"a": 1}, True),
    ("nested", {"a": [1, {"b": None}], "c": b"\x00"}, False),
    ("event_batch", {
        "seq": 42,
        "frame": 1180,
        "events": [
            {"timing": "OnSucceedAttack", "gen": 7, "ctx": {"t": "unit", "h": 11}},
            {"timing": "OnSucceedAttack", "gen": 7, "ctx": {"t": "unit", "h": 12}},
        ],
    }, False),
]


def canonical_cases():
    return [(label, value) for label, value, canonical in CASES if canonical]


def all_cases():
    return [(label, value) for label, value, _ in CASES]
