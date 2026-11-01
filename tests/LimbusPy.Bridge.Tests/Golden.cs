using System.Collections.Generic;

namespace LimbusPy.Bridge.Tests
{
    /// <summary>
    /// Mirrors python/tests/golden_table.py. The two tables must stay in lockstep: if either
    /// implementation changes its canonical encoding, tools/conformance.py reports the drift.
    /// </summary>
    internal static class Golden
    {
        public static readonly (string Label, object Value, bool Canonical)[] Cases =
        {
            ("nil", null, true),
            ("false", false, true),
            ("true", true, true),
            ("int_0", 0L, true),
            ("int_1", 1L, true),
            ("int_127", 127L, true),
            ("int_128", 128L, true),
            ("int_255", 255L, true),
            ("int_256", 256L, true),
            ("int_65535", 65535L, true),
            ("int_65536", 65536L, true),
            ("int_u32_max", 4294967295L, true),
            ("int_u32_over", 4294967296L, true),
            ("int_u64_max", ulong.MaxValue, true),
            ("int_m1", -1L, true),
            ("int_m32", -32L, true),
            ("int_m33", -33L, true),
            ("int_m128", -128L, true),
            ("int_m129", -129L, true),
            ("int_m32768", -32768L, true),
            ("int_m32769", -32769L, true),
            ("int_m2p31", -2147483648L, true),
            ("int_m2p31_minus1", -2147483649L, true),
            ("int_i64_min", long.MinValue, true),
            ("f64_1_5", 1.5d, true),
            ("f64_0", 0.0d, true),
            ("str_empty", string.Empty, true),
            ("str_a", "a", true),
            ("str_zh", "边狱公司", true),
            ("str_31", new string('x', 31), true),
            ("str_32", new string('x', 32), true),
            ("str_256", new string('x', 256), true),
            ("bin_empty", new byte[0], true),
            ("bin_2", new byte[] { 1, 2 }, true),
            ("arr_empty", new object[0], true),
            ("arr_1_2", new object[] { 1L, 2L }, true),
            ("map_empty", new Dictionary<string, object>(), true),
            ("map_a1", One("a", 1L), true),
            ("nested", Nested(), false),
            ("event_batch", EventBatch(), false),
        };

        private static Dictionary<string, object> One(string key, object value)
        {
            var map = new Dictionary<string, object>();
            map[key] = value;
            return map;
        }

        private static object Nested()
        {
            var inner = new Dictionary<string, object> { ["b"] = null };
            var map = new Dictionary<string, object>
            {
                ["a"] = new object[] { 1L, inner },
                ["c"] = new byte[] { 0 },
            };
            return map;
        }

        private static object EventBatch()
        {
            var unitA = new Dictionary<string, object> { ["t"] = "unit", ["h"] = 11L };
            var unitB = new Dictionary<string, object> { ["t"] = "unit", ["h"] = 12L };
            var eventA = new Dictionary<string, object>
            {
                ["timing"] = "OnSucceedAttack",
                ["gen"] = 7L,
                ["ctx"] = unitA,
            };
            var eventB = new Dictionary<string, object>
            {
                ["timing"] = "OnSucceedAttack",
                ["gen"] = 7L,
                ["ctx"] = unitB,
            };
            return new Dictionary<string, object>
            {
                ["seq"] = 42L,
                ["frame"] = 1180L,
                ["events"] = new object[] { eventA, eventB },
            };
        }
    }
}
