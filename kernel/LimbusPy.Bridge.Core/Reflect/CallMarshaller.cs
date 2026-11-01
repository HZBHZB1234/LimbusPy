using System;
using System.Collections;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Bridge.Core.Reflect
{
    /// <summary>Handle storage abstraction so the reflection layer stays Unity-free.</summary>
    public interface IRefStore
    {
        bool TryResolve(uint handle, out object target);

        /// <summary>Stores an object and returns its handle (0 = not storable).</summary>
        uint Store(object target);

        /// <summary>Wire kind ("unit", "skill", "obj", ...) used to build typed refs.</summary>
        string KindOf(object target);
    }

    /// <summary>Type lookup abstraction (the plugin resolves against interop assemblies).</summary>
    public interface ITypeIndex
    {
        bool TryGetType(string name, out Type type);

        IEnumerable<Type> AllTypes { get; }

        string Build { get; }
    }

    /// <summary>Named singletons ("global:BattleObjectManager").</summary>
    public interface IGlobalRegistry
    {
        bool TryGet(string name, out object value);

        IEnumerable<string> Names { get; }
    }

    /// <summary>
    /// Converts between the LPCP value model
    /// (null / bool / long / double / string / byte[] / object[] / Dictionary&lt;string,object&gt; / typed refs)
    /// and managed values.
    ///
    /// Rules (docs/01-protocol.md §4.1):
    ///  * refs in, objects out; objects that cannot be encoded come back as refs;
    ///  * enums travel as their name, <c>Nullable&lt;T&gt;</c> is unwrapped, numbers widen/narrow;
    ///  * anything that would silently lose information raises a <c>marshal</c> fault.
    /// </summary>
    public sealed class CallMarshaller
    {
        private static readonly Dictionary<Type, PropertyInfo> NullableUnderlying = new Dictionary<Type, PropertyInfo>();

        private readonly IRefStore _store;

        public CallMarshaller(IRefStore store)
        {
            _store = store ?? throw new ArgumentNullException(nameof(store));
        }

        public IRefStore Store => _store;

        // -- wire -> managed ----------------------------------------------------------------

        public object ToManaged(object wire, Type expected, string context)
        {
            var nullable = Nullable.GetUnderlyingType(expected);
            if (nullable != null) expected = nullable;

            if (wire == null) return DefaultFor(expected);

            // 1. Explicit {"t","h"} refs are always honoured.
            if (Lpcp.TryReadRefMap(wire, out var kind, out var refHandle, out var explicitNull))
            {
                var target = Resolve(refHandle, context);
                if (target == null) return DefaultFor(expected);
                if (expected != typeof(object) && !expected.IsInstanceOfType(target))
                {
                    throw LpcpFault.Marshal(
                        context + ": handle kind '" + (kind == HandleKind.None ? "?" : HandleKindNames.ToWire(kind)) +
                        "' points at " + target.GetType().FullName + ", expected " + expected.FullName,
                        Lpcp.Map("expected", expected.FullName, "actual", target.GetType().FullName));
                }
                return target;
            }

            if (explicitNull) return DefaultFor(expected);

            // 2. Bare integers in a reference-typed position are handles; in a numeric position
            //    they are numbers. Explicit ref maps (above) are how the Python side always sends
            //    object references, so this only matters for hand-written raw calls.
            if (IsHandlePosition(expected) && IsBareInteger(wire))
            {
                return Resolve(unchecked((uint)ToInt64(wire)), context);
            }

            if (expected == typeof(object))
            {
                return NormalizeWireScalar(wire);
            }

            if (expected == typeof(string))
            {
                if (wire is string s) return s;
                throw MarshalError(context, wire, expected);
            }

            if (expected == typeof(bool))
            {
                if (wire is bool b) return b;
                if (wire is long lb) return lb != 0;
                throw MarshalError(context, wire, expected);
            }

            if (expected == typeof(byte[]))
            {
                if (wire is byte[] bytes) return bytes;
                throw MarshalError(context, wire, expected);
            }

            if (expected.IsEnum)
            {
                if (wire is string name)
                {
                    try
                    {
                        return Enum.Parse(expected, name, ignoreCase: true);
                    }
                    catch (Exception ex)
                    {
                        throw LpcpFault.Marshal(context + ": '" + name + "' is not a member of " + expected.FullName + " (" + ex.Message + ")");
                    }
                }

                if (wire is long || wire is double || wire is ulong)
                {
                    var numeric = ToInt64(wire);
                    return Enum.ToObject(expected, numeric);
                }

                throw MarshalError(context, wire, expected);
            }

            if (expected == typeof(Type))
            {
                if (wire is string typeName) return ResolveTypeOrThrow(typeName, context);
                throw MarshalError(context, wire, expected);
            }

            if (expected.IsArray)
            {
                var element = expected.GetElementType();
                if (wire is byte[] buffer && element == typeof(byte)) return buffer;
                if (!(wire is object[] items)) throw MarshalError(context, wire, expected);
                var array = Array.CreateInstance(element, items.Length);
                for (var i = 0; i < items.Length; i++)
                {
                    array.SetValue(ToManaged(items[i], element, context + "[" + i + "]"), i);
                }
                return array;
            }

            if (expected.IsGenericType && expected.GetGenericTypeDefinition() == typeof(List<>))
            {
                if (!(wire is object[] items)) throw MarshalError(context, wire, expected);
                var element = expected.GetGenericArguments()[0];
                var list = (IList)Activator.CreateInstance(expected);
                for (var i = 0; i < items.Length; i++)
                {
                    list.Add(ToManaged(items[i], element, context + "[" + i + "]"));
                }
                return list;
            }

            if (expected == typeof(long))
            {
                if (wire is long l) return l;
                if (wire is ulong ul) return unchecked((long)ul);
                if (wire is double d) return (long)d;
                if (wire is bool bo) return bo ? 1L : 0L;
                if (wire is string st && long.TryParse(st, out var parsed)) return parsed;
                throw MarshalError(context, wire, expected);
            }

            if (expected == typeof(int)) return (int)CheckedNumber(ToInt64Strict(wire, context), int.MinValue, int.MaxValue, context);
            if (expected == typeof(short)) return (short)CheckedNumber(ToInt64Strict(wire, context), short.MinValue, short.MaxValue, context);
            if (expected == typeof(sbyte)) return (sbyte)CheckedNumber(ToInt64Strict(wire, context), sbyte.MinValue, sbyte.MaxValue, context);
            if (expected == typeof(uint)) return (uint)CheckedUnsigned(ToInt64Strict(wire, context), uint.MaxValue, context);
            if (expected == typeof(ushort)) return (ushort)CheckedUnsigned(ToInt64Strict(wire, context), ushort.MaxValue, context);
            if (expected == typeof(byte)) return (byte)CheckedUnsigned(ToInt64Strict(wire, context), byte.MaxValue, context);
            if (expected == typeof(ulong))
            {
                var value = ToInt64Strict(wire, context);
                if (value < 0) throw LpcpFault.Marshal(context + ": negative value " + value + " for UInt64");
                return (ulong)value;
            }
            if (expected == typeof(double)) return ToDoubleStrict(wire, context);
            if (expected == typeof(float)) return (float)ToDoubleStrict(wire, context);

            // Fallback: exact type match or a ref-less object we cannot interpret.
            if (wire != null && expected.IsInstanceOfType(wire)) return wire;

            throw MarshalError(context, wire, expected);
        }

        private object DefaultFor(Type expected)
        {
            return expected != null && expected.IsValueType ? Activator.CreateInstance(expected) : null;
        }

        /// <summary>True when a bare integer in this position denotes a handle rather than a number.</summary>
        public static bool IsHandlePosition(Type expected)
        {
            if (expected == null) return false;
            if (expected.IsValueType) return false;          // numbers, enums, structs: never a handle
            if (expected == typeof(string)) return false;
            if (expected == typeof(byte[])) return false;
            if (expected == typeof(object)) return false;    // ambiguous -> treat as scalar
            if (expected == typeof(Type)) return false;
            if (expected.IsArray) return false;
            if (expected.IsGenericType && expected.GetGenericTypeDefinition() == typeof(List<>)) return false;
            return true;
        }

        public static bool IsBareInteger(object wire) => wire is long || wire is ulong || wire is int || wire is uint;

        private object Resolve(uint handle, string context)
        {
            if (!_store.TryResolve(handle, out var target))
            {
                throw LpcpFault.StaleHandle(
                    context + ": handle 0x" + handle.ToString("x") + " is not live",
                    Lpcp.Map("handle", (long)handle));
            }

            return target;
        }

        private static Type ResolveTypeOrThrow(string typeName, string context)
        {
            var type = Type.GetType(typeName, throwOnError: false);
            if (type == null) throw LpcpFault.NotFound(context + ": type '" + typeName + "' is not loadable");
            return type;
        }

        // -- managed -> wire ----------------------------------------------------------------

        public object ToWire(object value)
        {
            switch (value)
            {
                case null:
                    return null;
                case bool b:
                    return b;
                case string s:
                    return s;
                case byte[] bytes:
                    return bytes;
                case sbyte sb:
                    return (long)sb;
                case byte by:
                    return (long)by;
                case short sh:
                    return (long)sh;
                case ushort ush:
                    return (long)ush;
                case int i:
                    return (long)i;
                case uint ui:
                    return (long)ui;
                case long l:
                    return l;
                case ulong ul:
                    return ul <= long.MaxValue ? (object)(long)ul : ul;
                case float f:
                    return (double)f;
                case double d:
                    return d;
                case char c:
                    return c.ToString();
                case Enum e:
                    return e.ToString();
                case Type t:
                    return t.FullName;
                case object[] array:
                {
                    var result = new object[array.Length];
                    for (var index = 0; index < array.Length; index++) result[index] = ToWire(array[index]);
                    return result;
                }
                case IDictionary dictionary:
                {
                    var result = new Dictionary<string, object>(StringComparer.Ordinal);
                    foreach (DictionaryEntry entry in dictionary)
                    {
                        result[entry.Key?.ToString() ?? "null"] = ToWire(entry.Value);
                    }
                    return result;
                }
                case IEnumerable enumerable when !(value is string):
                {
                    var items = new List<object>();
                    foreach (var item in enumerable) items.Add(ToWire(item));
                    return items.ToArray();
                }
            }

            if (value.GetType().IsValueType)
            {
                // Boxing a struct into a handle would alias a copy; refuse instead of lying.
                throw LpcpFault.Marshal(
                    "value type " + value.GetType().FullName + " cannot be bridged as-is " +
                    "(return a flat map / primitive, or add a struct mapping)");
            }

            var handle = _store.Store(value);
            if (handle == 0) throw LpcpFault.Marshal("value of type " + value.GetType().FullName + " is not bridgeable");
            var kind = _store.KindOf(value) ?? "obj";
            return Lpcp.Map("t", kind, "h", (long)handle);
        }

        // -- numeric helpers ---------------------------------------------------------------

        private static LpcpFault MarshalError(string context, object wire, Type expected)
            => LpcpFault.Marshal(
                context + ": cannot convert " + DescribeWire(wire) + " to " + expected.FullName,
                Lpcp.Map("expected", expected.FullName, "actual", DescribeWire(wire)));

        public static string DescribeWire(object wire)
        {
            if (wire == null) return "null";
            switch (wire)
            {
                case string s: return "str(" + (s.Length > 32 ? s.Substring(0, 32) + "..." : s) + ")";
                case bool b: return "bool(" + b + ")";
                case long l: return "int(" + l + ")";
                case ulong ul: return "uint(" + ul + ")";
                case double d: return d.ToString(CultureInfo.InvariantCulture);
                case byte[] bytes: return "bin(" + bytes.Length + ")";
                case object[] array: return "array(" + array.Length + ")";
                case Dictionary<string, object> map:
                    return map.ContainsKey("h") ? "ref(" + Lpcp.GetString(map, "t") + ":" + Lpcp.GetLong(map, "h") + ")" : "map(" + map.Count + ")";
                default: return wire.GetType().Name;
            }
        }

        private static long ToInt64(object wire)
        {
            switch (wire)
            {
                case long l: return l;
                case ulong ul: return unchecked((long)ul);
                case double d: return (long)d;
                case bool b: return b ? 1 : 0;
                case string s when long.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed): return parsed;
                default: throw LpcpFault.Marshal("expected a number, got " + DescribeWire(wire));
            }
        }

        private static long ToInt64Strict(object wire, string context)
        {
            switch (wire)
            {
                case long l: return l;
                case ulong ul:
                    if (ul > long.MaxValue) throw LpcpFault.Marshal(context + ": value " + ul + " does not fit Int64");
                    return (long)ul;
                case double d:
                    if (Math.Abs(d - Math.Round(d)) > 0.0000001)
                        throw LpcpFault.Marshal(context + ": " + d + " is not an integer");
                    if (d > long.MaxValue || d < long.MinValue)
                        throw LpcpFault.Marshal(context + ": " + d + " does not fit Int64");
                    return (long)d;
                case bool b:
                    return b ? 1 : 0;
                case string s:
                    if (long.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed)) return parsed;
                    throw LpcpFault.Marshal(context + ": '" + s + "' is not an integer");
                default:
                    throw LpcpFault.Marshal(context + ": expected an integer, got " + DescribeWire(wire));
            }
        }

        private static double ToDoubleStrict(object wire, string context)
        {
            switch (wire)
            {
                case double d: return d;
                case long l: return l;
                case ulong ul: return ul;
                case string s when double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed):
                    return parsed;
                default:
                    throw LpcpFault.Marshal(context + ": expected a number, got " + DescribeWire(wire));
            }
        }

        private static long CheckedNumber(long value, long min, long max, string context)
        {
            if (value < min || value > max)
                throw LpcpFault.Marshal(context + ": " + value + " is out of range [" + min + ", " + max + "]");
            return value;
        }

        private static long CheckedUnsigned(long value, long max, string context)
        {
            if (value < 0 || value > max) throw LpcpFault.Marshal(context + ": " + value + " is out of range [0, " + max + "]");
            return value;
        }

        private static object NormalizeWireScalar(object wire)
        {
            switch (wire)
            {
                case ulong ul when ul <= long.MaxValue: return (long)ul;
                case Dictionary<string, object> map when map.ContainsKey("h"):
                {
                    // ref map handled before this call; defensive fallback
                    return map;
                }
                default: return wire;
            }
        }
    }
}
