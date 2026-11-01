using System;
using System.Collections.Generic;

namespace LimbusPy.Bridge.Core.Protocol
{
    /// <summary>Error codes frozen by docs/01-protocol.md §7.</summary>
    public static class ErrorCode
    {
        public const string Proto = "proto";
        public const string Busy = "busy";
        public const string NotFound = "notfound";
        public const string Ambiguous = "ambiguous";
        public const string Marshal = "marshal";
        public const string WrongThread = "wrong_thread";
        public const string Timeout = "timeout";
        public const string StaleHandle = "stale_handle";
        public const string Script = "script";
        public const string Unsupported = "unsupported";
        public const string OverBudget = "over_budget";
        public const string Internal = "internal";
    }

    /// <summary>Capability names frozen by docs/01-protocol.md §5.1.</summary>
    public static class Caps
    {
        public const string ReflectManaged = "reflect.managed";
        public const string ReflectNative = "reflect.native";
        public const string ReflectField = "reflect.field";
        public const string HandleArena = "handle.arena";
        public const string HandlePinned = "handle.pinned";
        public const string SnapshotV1 = "snapshot.v1";
        public const string PatchTable = "patch.table";
        public const string ModularRegistry = "modular.registry";
        public const string ModularRun = "modular.run";
        public const string HotReload = "hot.reload";
        public const string BudgetFrame = "budget.frame";
    }

    public static class ProtocolInfo
    {
        /// <summary>Protocol version spoken by this build. Python refuses anything whose major differs.</summary>
        public const string Version = "1.0";
        public const int Major = 1;

        public static bool IsCompatible(string peerVersion)
        {
            if (string.IsNullOrWhiteSpace(peerVersion)) return false;
            var dot = peerVersion.IndexOf('.');
            var majorText = dot < 0 ? peerVersion : peerVersion.Substring(0, dot);
            return int.TryParse(majorText, out var major) && major == Major;
        }
    }

    /// <summary>Handle kinds. Encoded in the high 4 bits of a handle; the low 28 bits are the slot.</summary>
    public enum HandleKind : byte
    {
        None = 0,
        Unit = 1,
        Skill = 2,
        Coin = 3,
        Buff = 4,
        Action = 5,
        Stage = 6,
        Model = 7,
        Ability = 8,
        Sound = 9,
        Asset = 10,
        Sa = 11,
        Obj = 12,
    }

    public static class HandleKindNames
    {
        public static string ToWire(HandleKind kind)
        {
            switch (kind)
            {
                case HandleKind.Unit: return "unit";
                case HandleKind.Skill: return "skill";
                case HandleKind.Coin: return "coin";
                case HandleKind.Buff: return "buff";
                case HandleKind.Action: return "action";
                case HandleKind.Stage: return "stage";
                case HandleKind.Model: return "model";
                case HandleKind.Ability: return "ability";
                case HandleKind.Sound: return "sound";
                case HandleKind.Asset: return "asset";
                case HandleKind.Sa: return "sa";
                case HandleKind.Obj: return "obj";
                default: return "none";
            }
        }

        public static bool TryParse(string wire, out HandleKind kind)
        {
            switch (wire)
            {
                case "unit": kind = HandleKind.Unit; return true;
                case "skill": kind = HandleKind.Skill; return true;
                case "coin": kind = HandleKind.Coin; return true;
                case "buff": kind = HandleKind.Buff; return true;
                case "action": kind = HandleKind.Action; return true;
                case "stage": kind = HandleKind.Stage; return true;
                case "model": kind = HandleKind.Model; return true;
                case "ability": kind = HandleKind.Ability; return true;
                case "sound": kind = HandleKind.Sound; return true;
                case "asset": kind = HandleKind.Asset; return true;
                case "sa": kind = HandleKind.Sa; return true;
                case "obj": kind = HandleKind.Obj; return true;
                default: kind = HandleKind.None; return false;
            }
        }
    }

    /// <summary>
    /// Helpers that build payload maps and typed refs. Keeping every key name in one file makes
    /// the wire contract auditable against docs/01-protocol.md.
    /// </summary>
    public static class Lpcp
    {
        public static Dictionary<string, object> Map() => new Dictionary<string, object>(StringComparer.Ordinal);

        /// <summary>Builds a map from alternating key/value arguments: Map("a", 1, "b", 2).</summary>
        public static Dictionary<string, object> Map(params object[] keyValuePairs)
        {
            var map = new Dictionary<string, object>(StringComparer.Ordinal);
            if (keyValuePairs == null) return map;
            if ((keyValuePairs.Length & 1) != 0)
                throw new ArgumentException("Map() requires an even number of arguments (key/value pairs)");

            for (var i = 0; i < keyValuePairs.Length; i += 2)
            {
                var key = keyValuePairs[i] as string;
                if (key == null) throw new ArgumentException("Map() keys must be strings, got " + (keyValuePairs[i]?.GetType().Name ?? "null"));
                map[key] = keyValuePairs[i + 1];
            }

            return map;
        }

        public static Dictionary<string, object> Ref(HandleKind kind, uint handle)
            => Map("t", HandleKindNames.ToWire(kind), "h", (long)handle);

        public static Dictionary<string, object> NullRef() => Map("t", "null");

        public static Dictionary<string, object> Error(string code, string message, Dictionary<string, object> detail = null)
        {
            var err = Map("code", code, "msg", message ?? string.Empty);
            if (detail != null) err["detail"] = detail;
            return err;
        }

        public static Dictionary<string, object> Hello(string kernelVersion, string gameBuild, string gameVersion, IEnumerable<string> caps)
            => Map("proto", ProtocolInfo.Version,
                   "kernel", kernelVersion,
                   "game_build", gameBuild ?? string.Empty,
                   "game_version", gameVersion ?? string.Empty,
                   "caps", ToArray(caps));

        public static Dictionary<string, object> OkResult(long id, object value)
            => Map("id", id, "ok", true, "value", value);

        public static Dictionary<string, object> ErrorResult(long id, Dictionary<string, object> err)
            => Map("id", id, "ok", false, "err", err);

        public static Dictionary<string, object> Call(long id, string target, string method, object[] args)
            => Map("id", id, "target", target ?? string.Empty, "method", method ?? string.Empty, "args", args ?? Array.Empty<object>());

        public static Dictionary<string, object> Log(string level, string mod, string message)
            => Map("level", level ?? "info", "mod", mod ?? string.Empty, "msg", message ?? string.Empty);

        public static Dictionary<string, object> Bye(string reason) => Map("reason", reason ?? string.Empty);

        public static object[] ToArray(IEnumerable<string> values)
        {
            if (values == null) return Array.Empty<object>();
            var list = new List<object>();
            foreach (var value in values) list.Add(value);
            return list.ToArray();
        }

        // ---- payload access helpers (never throw on missing keys) ----

        public static long GetLong(Dictionary<string, object> map, string key, long fallback = 0)
        {
            if (map == null || !map.TryGetValue(key, out var value) || value == null) return fallback;
            switch (value)
            {
                case long l: return l;
                case ulong u: return u <= long.MaxValue ? (long)u : fallback;
                case int i: return i;
                case uint ui: return ui;
                case short s: return s;
                case ushort us: return us;
                case byte b: return b;
                case sbyte sb: return sb;
                case double d: return (long)d;
                case float f: return (long)f;
                case bool bo: return bo ? 1 : 0;
                case string st: return long.TryParse(st, out var parsed) ? parsed : fallback;
                default: return fallback;
            }
        }

        public static int GetInt(Dictionary<string, object> map, string key, int fallback = 0)
        {
            var value = GetLong(map, key, fallback);
            if (value > int.MaxValue) return int.MaxValue;
            if (value < int.MinValue) return int.MinValue;
            return (int)value;
        }

        public static bool GetBool(Dictionary<string, object> map, string key, bool fallback = false)
            => map != null && map.TryGetValue(key, out var value) && value is bool b ? b : fallback;

        public static string GetString(Dictionary<string, object> map, string key, string fallback = null)
            => map != null && map.TryGetValue(key, out var value) && value is string s ? s : fallback;

        public static object[] GetArray(Dictionary<string, object> map, string key)
            => map != null && map.TryGetValue(key, out var value) && value is object[] array ? array : Array.Empty<object>();

        public static Dictionary<string, object> GetMap(Dictionary<string, object> map, string key)
            => map != null && map.TryGetValue(key, out var value) ? value as Dictionary<string, object> : null;

        public static string[] GetStringArray(Dictionary<string, object> map, string key)
        {
            var array = GetArray(map, key);
            var result = new string[array.Length];
            for (var i = 0; i < array.Length; i++) result[i] = array[i] as string ?? string.Empty;
            return result;
        }

        /// <summary>
        /// Accepts either {"t":kind,"h":n} or a bare integer and resolves it to (kind, handle).
        /// Returns false for null refs / unsupported shapes.
        ///
        /// NOTE: for call *arguments* prefer <see cref="TryReadRefMap"/> plus type-directed
        /// interpretation - in an argument position a bare integer is usually a number, not a
        /// handle. This helper is for places whose only meaning is a reference (targets,
        /// kernel-to-python values).
        /// </summary>
        public static bool TryReadRef(object value, out HandleKind kind, out uint handle, out bool isExplicitNull)
        {
            kind = HandleKind.None;
            handle = 0;
            isExplicitNull = false;

            if (value == null)
            {
                isExplicitNull = true;
                return false;
            }

            switch (value)
            {
                case long l:
                    handle = unchecked((uint)l);
                    return handle != 0;
                case ulong u:
                    handle = unchecked((uint)u);
                    return handle != 0;
                case int i:
                    handle = unchecked((uint)i);
                    return handle != 0;
                case Dictionary<string, object> map:
                    return TryReadRefMap(map, out kind, out handle, out isExplicitNull);
                default:
                    return false;
            }
        }

        /// <summary>
        /// Strictly map-shaped reference: <c>{"t":kind,"h":handle}</c> or <c>{"t":"null"}</c>.
        /// Integer values are never interpreted, so callers can decide per expected type.
        /// </summary>
        public static bool TryReadRefMap(object value, out HandleKind kind, out uint handle, out bool isExplicitNull)
        {
            kind = HandleKind.None;
            handle = 0;
            isExplicitNull = false;

            if (!(value is Dictionary<string, object> map)) return false;
            if (!map.ContainsKey("h") && !map.ContainsKey("t")) return false;

            var tag = GetString(map, "t");
            if (tag == null || tag == "null")
            {
                isExplicitNull = true;
                return false;
            }

            handle = unchecked((uint)GetLong(map, "h"));
            HandleKindNames.TryParse(tag, out kind);
            return handle != 0;
        }
    }
}
