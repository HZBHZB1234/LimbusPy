using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Bridge.Core.Reflect
{
    /// <summary>
    /// The reflection escape hatch (docs/03-architecture.md §4). Everything the modding API can
    /// reach ultimately goes through here, so the same code path is exercised by the Unity-free
    /// conformance harness and by the in-game kernel.
    ///
    /// Target specs accepted by every entry point:
    ///   * <c>""</c> + method <c>"Ns.Type::Method"</c>  - static call
    ///   * <c>{"t":"unit","h":123}</c>                 - instance call on a handle
    ///   * <c>"global:BattleObjectManager"</c>         - named singleton registered by the plugin
    ///   * <c>"type:Ns.Type"</c>                       - static members of a type
    /// </summary>
    public sealed class ReflectionInvoker
    {
        private readonly ITypeIndex _types;
        private readonly IGlobalRegistry _globals;
        private readonly CallMarshaller _marshaller;
        private readonly Action _ensureMainThread;

        private readonly ConcurrentDictionary<string, MethodInfo[]> _methodCache =
            new ConcurrentDictionary<string, MethodInfo[]>(StringComparer.Ordinal);

        private readonly ConcurrentDictionary<string, Type> _typeCache = new ConcurrentDictionary<string, Type>(StringComparer.Ordinal);

        public ReflectionInvoker(ITypeIndex types, IGlobalRegistry globals, CallMarshaller marshaller, Action ensureMainThread = null)
        {
            _types = types ?? throw new ArgumentNullException(nameof(types));
            _globals = globals ?? new EmptyGlobals();
            _marshaller = marshaller ?? throw new ArgumentNullException(nameof(marshaller));
            _ensureMainThread = ensureMainThread;
        }

        public CallMarshaller Marshaller => _marshaller;

        // -- public API --------------------------------------------------------------------

        public object Invoke(object targetSpec, string method, object[] args)
        {
            _ensureMainThread?.Invoke();
            args = args ?? Array.Empty<object>();

            var (receiver, type, name) = ResolveCallable(targetSpec, method);
            var candidates = FindMethods(type, name, args.Length);
            if (candidates.Length == 0)
            {
                throw LpcpFault.NotFound(
                    "no method " + type.FullName + "." + name + "/" + args.Length + " (candidates: " +
                    string.Join(", ", EnumerateNames(type, name).Take(8)) + ")",
                    Lpcp.Map("type", type.FullName, "method", name, "argc", (long)args.Length));
            }

            var ordered = candidates.OrderByDescending(candidate => Score(candidate.GetParameters(), args)).ToArray();
            var failures = new List<string>();
            foreach (var candidate in ordered)
            {
                try
                {
                    var parameters = candidate.GetParameters();
                    var converted = new object[parameters.Length];
                    for (var index = 0; index < parameters.Length; index++)
                    {
                        converted[index] = _marshaller.ToManaged(args[index], parameters[index].ParameterType,
                            "arg " + index + " (" + parameters[index].Name + ")");
                    }

                    object result;
                    try
                    {
                        result = candidate.Invoke(receiver, converted);
                    }
                    catch (TargetInvocationException ex)
                    {
                        var inner = ex.InnerException ?? ex;
                        throw new LpcpFault(ErrorCode.Internal, "target threw " + inner.GetType().Name + ": " + inner.Message,
                            Lpcp.Map("target", type.FullName, "method", name, "source", "target"));
                    }

                    return _marshaller.ToWire(result);
                }
                catch (LpcpFault fault) when (fault.Code == ErrorCode.Marshal)
                {
                    failures.Add(Signature(candidate) + " -> " + fault.Message);
                }
            }

            throw LpcpFault.Marshal(
                "no overload of " + type.FullName + "." + name + " accepts the given arguments",
                Lpcp.Map(
                    "type", type.FullName,
                    "method", name,
                    "candidates", ordered.Select(Signature).Take(8).Cast<object>().ToArray(),
                    "reasons", failures.Take(8).Cast<object>().ToArray()));
        }

        public object GetMember(object targetSpec, string member)
        {
            _ensureMainThread?.Invoke();
            var (receiver, type, name) = ResolveMemberTarget(targetSpec, member);

            var property = FindProperty(type, name);
            if (property != null)
            {
                var getter = property.GetGetMethod(true);
                if (getter == null) throw LpcpFault.Unsupported("property " + type.FullName + "." + name + " has no getter");
                return _marshaller.ToWire(getter.Invoke(receiver, Array.Empty<object>()));
            }

            var field = FindField(type, name);
            if (field != null) return _marshaller.ToWire(field.GetValue(receiver));

            throw LpcpFault.NotFound("no member " + type.FullName + "." + name,
                Lpcp.Map("type", type.FullName, "member", name));
        }

        public object SetMember(object targetSpec, string member, object value)
        {
            _ensureMainThread?.Invoke();
            var (receiver, type, name) = ResolveMemberTarget(targetSpec, member);

            var property = FindProperty(type, name);
            if (property != null)
            {
                var setter = property.GetSetMethod(true);
                if (setter == null) throw LpcpFault.Unsupported("property " + type.FullName + "." + name + " has no setter");
                var parameter = setter.GetParameters()[0];
                setter.Invoke(receiver, new[] { _marshaller.ToManaged(value, parameter.ParameterType, "value") });
                return _marshaller.ToWire(property.GetGetMethod(true)?.Invoke(receiver, Array.Empty<object>()));
            }

            var field = FindField(type, name);
            if (field != null)
            {
                if (field.IsInitOnly || field.IsLiteral)
                    throw LpcpFault.Unsupported("field " + type.FullName + "." + name + " is read-only");
                field.SetValue(receiver, _marshaller.ToManaged(value, field.FieldType, "value"));
                return _marshaller.ToWire(field.GetValue(receiver));
            }

            throw LpcpFault.NotFound("no member " + type.FullName + "." + name,
                Lpcp.Map("type", type.FullName, "member", name));
        }

        /// <summary>Introspection helper used by <c>limbuspy doctor</c> and the REPL's <c>dir()</c>.</summary>
        public Dictionary<string, object> Describe(object targetSpec, int limit = 200)
        {
            _ensureMainThread?.Invoke();
            var (receiver, type, _) = ResolveMemberTarget(targetSpec, string.Empty);

            var members = new List<object>();
            var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.FlattenHierarchy;

            foreach (var method in type.GetMethods(flags).OrderBy(m => m.Name, StringComparer.Ordinal))
            {
                if (method.IsSpecialName) continue;
                members.Add(Lpcp.Map("k", "method", "n", method.Name, "sig", Signature(method)));
                if (members.Count >= limit) break;
            }

            if (members.Count < limit)
            {
                foreach (var property in type.GetProperties(flags).OrderBy(p => p.Name, StringComparer.Ordinal))
                {
                    members.Add(Lpcp.Map(
                        "k", "property", "n", property.Name,
                        "type", property.PropertyType.FullName,
                        "get", property.GetGetMethod(true) != null,
                        "set", property.GetSetMethod(true) != null));
                    if (members.Count >= limit) break;
                }
            }

            if (members.Count < limit)
            {
                foreach (var field in type.GetFields(flags).OrderBy(f => f.Name, StringComparer.Ordinal))
                {
                    members.Add(Lpcp.Map("k", "field", "n", field.Name, "type", field.FieldType.FullName,
                        "readonly", field.IsInitOnly || field.IsLiteral));
                    if (members.Count >= limit) break;
                }
            }

            var result = Lpcp.Map(
                "type", type.FullName,
                "instance", receiver != null,
                "count", (long)members.Count,
                "members", members.ToArray());
            if (receiver != null) result["value"] = _marshaller.ToWire(receiver);
            return result;
        }

        /// <summary>Lists types from the index (used by doctor / stub verification).</summary>
        public Dictionary<string, object> Types(string filter, int limit = 200)
        {
            var matches = new List<object>();
            foreach (var type in _types.AllTypes)
            {
                var full = type.FullName;
                if (filter != null && full.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0) continue;
                matches.Add(full);
                if (matches.Count >= limit) break;
            }

            return Lpcp.Map("build", _types.Build, "count", (long)matches.Count, "types", matches.ToArray());
        }

        // -- target resolution -------------------------------------------------------------

        private (object receiver, Type type, string name) ResolveCallable(object targetSpec, string method)
        {
            if (string.IsNullOrEmpty(method)) throw LpcpFault.Marshal("method name is required");

            var separator = method.IndexOf("::", StringComparison.Ordinal);
            if (separator > 0 && IsEmptyTarget(targetSpec))
            {
                var typeName = method.Substring(0, separator);
                var methodName = method.Substring(separator + 2);
                return (null, LookupType(typeName), methodName);
            }

            var (receiver, type, _) = ResolveMemberTarget(targetSpec, method);
            return (receiver, type, method);
        }

        private (object receiver, Type type, string name) ResolveMemberTarget(object targetSpec, string name)
        {
            if (targetSpec == null || (targetSpec is string text && string.IsNullOrEmpty(text)))
            {
                throw LpcpFault.Marshal("target is required (use 'Ns.Type::Member' for statics, 'global:Name' for singletons)");
            }

            if (targetSpec is string spec)
            {
                if (spec.StartsWith("global:", StringComparison.OrdinalIgnoreCase))
                {
                    var globalName = spec.Substring("global:".Length);
                    if (!_globals.TryGet(globalName, out var value) || value == null)
                        throw LpcpFault.NotFound("unknown global '" + globalName + "'",
                            Lpcp.Map("known", _globals.Names.Take(32).Cast<object>().ToArray()));
                    return (value, value.GetType(), name);
                }

                if (spec.StartsWith("type:", StringComparison.OrdinalIgnoreCase))
                {
                    var type = LookupType(spec.Substring("type:".Length));
                    return (null, type, name);
                }

                throw LpcpFault.Marshal("unrecognised target '" + spec + "' (expected a handle ref, 'global:Name' or 'type:Ns.Type')");
            }

            if (Lpcp.TryReadRef(targetSpec, out _, out var handle, out var isNull))
            {
                if (!_marshaller.Store.TryResolve(handle, out var target) || target == null)
                    throw LpcpFault.StaleHandle("handle 0x" + handle.ToString("x") + " is not live",
                        Lpcp.Map("handle", (long)handle));
                return (target, target.GetType(), name);
            }

            if (isNull) throw LpcpFault.Marshal("target is an explicit null reference");
            throw LpcpFault.Marshal("unsupported target specification: " + CallMarshaller.DescribeWire(targetSpec));
        }

        private Type LookupType(string name)
        {
            if (string.IsNullOrEmpty(name)) throw LpcpFault.Marshal("type name is required");
            if (_typeCache.TryGetValue(name, out var cached)) return cached;
            if (!_types.TryGetType(name, out var type))
            {
                throw LpcpFault.NotFound("unknown type '" + name + "'",
                    Lpcp.Map("build", _types.Build));
            }
            _typeCache[name] = type;
            return type;
        }

        private static bool IsEmptyTarget(object targetSpec)
            => targetSpec == null || (targetSpec is string text && text.Length == 0);

        // -- member lookup -----------------------------------------------------------------

        private MethodInfo[] FindMethods(Type type, string name, int argCount)
        {
            var key = type.FullName + "|" + name + "|" + argCount;
            return _methodCache.GetOrAdd(key, _ =>
            {
                var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance |
                            BindingFlags.Static | BindingFlags.FlattenHierarchy;
                return type.GetMethods(flags)
                    .Where(m => m.Name == name && m.GetParameters().Length == argCount && !m.IsGenericMethodDefinition)
                    .ToArray();
            });
        }

        private IEnumerable<string> EnumerateNames(Type type, string name)
        {
            var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.FlattenHierarchy;
            return type.GetMethods(flags)
                .Where(m => m.Name.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0)
                .Select(Signature);
        }

        private static PropertyInfo FindProperty(Type type, string name)
        {
            var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.FlattenHierarchy;
            return type.GetProperty(name, flags);
        }

        private static FieldInfo FindField(Type type, string name)
        {
            var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.FlattenHierarchy;
            return type.GetField(name, flags);
        }

        // -- overload scoring -------------------------------------------------------------

        /// <summary>Cheap structural score used to order overloads before the real conversion.</summary>
        private int Score(ParameterInfo[] parameters, object[] args)
        {
            if (parameters.Length != args.Length) return int.MinValue;
            var score = 0;
            for (var index = 0; index < parameters.Length; index++)
            {
                score += ScoreOne(parameters[index].ParameterType, args[index]);
            }
            return score;
        }

        private int ScoreOne(Type expected, object wire)
        {
            var nullable = Nullable.GetUnderlyingType(expected);
            if (nullable != null) expected = nullable;

            if (expected == typeof(object)) return 1;
            if (wire == null) return expected.IsValueType ? -100 : 3;

            if (Lpcp.TryReadRefMap(wire, out _, out var refHandle, out var explicitNull))
            {
                if (explicitNull) return expected.IsValueType ? -100 : 3;
                return _marshaller.Store.TryResolve(refHandle, out var target) && target != null &&
                       (expected == typeof(object) || expected.IsInstanceOfType(target))
                    ? 5
                    : -100;
            }

            if (CallMarshaller.IsHandlePosition(expected) && CallMarshaller.IsBareInteger(wire))
            {
                var raw = wire is ulong unsigned ? (uint)unsigned : unchecked((uint)Convert.ToInt64(wire));
                return _marshaller.Store.TryResolve(raw, out var bareTarget) && bareTarget != null && expected.IsInstanceOfType(bareTarget)
                    ? 4
                    : -100;
            }

            if (expected == typeof(string)) return wire is string ? 6 : -100;
            if (expected == typeof(bool)) return wire is bool ? 6 : (wire is long ? 2 : -100);
            if (expected == typeof(byte[])) return wire is byte[] ? 6 : -100;

            if (expected.IsEnum)
            {
                if (wire is string s) return Enum.GetNames(expected).Any(n => string.Equals(n, s, StringComparison.OrdinalIgnoreCase)) ? 4 : -50;
                return wire is long ? 3 : -100;
            }

            var numeric = wire is long || wire is ulong || wire is double;
            if (expected == typeof(long)) return wire is long ? 6 : (numeric ? 3 : -100);
            if (expected == typeof(int)) return wire is long ? (long)wire is var l && l >= int.MinValue && l <= int.MaxValue ? 5 : -100 : (numeric ? 2 : -100);
            if (expected == typeof(short)) return numeric ? 2 : -100;
            if (expected == typeof(byte)) return numeric ? 2 : -100;
            if (expected == typeof(sbyte)) return numeric ? 2 : -100;
            if (expected == typeof(uint)) return numeric ? 2 : -100;
            if (expected == typeof(ushort)) return numeric ? 2 : -100;
            if (expected == typeof(ulong)) return numeric ? 2 : -100;
            if (expected == typeof(double)) return wire is double ? 6 : (numeric ? 3 : -100);
            if (expected == typeof(float)) return numeric ? 2 : -100;
            if (expected == typeof(Type)) return wire is string ? 3 : -100;
            if (expected.IsArray) return wire is object[] ? 2 : -100;

            return wire != null && expected.IsInstanceOfType(wire) ? 6 : -100;
        }

        private static string Signature(MethodInfo method)
        {
            var parameters = method.GetParameters();
            var text = string.Join(", ", parameters.Select(p => p.ParameterType.Name + " " + p.Name));
            var prefix = method.IsStatic ? "static " : string.Empty;
            return prefix + method.ReturnType.Name + " " + method.Name + "(" + text + ")";
        }

        private sealed class EmptyGlobals : IGlobalRegistry
        {
            public bool TryGet(string name, out object value)
            {
                value = null;
                return false;
            }

            public IEnumerable<string> Names => Array.Empty<string>();
        }
    }
}
