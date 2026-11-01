using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Bridge.Core.Reflect
{
    public enum PatchKind
    {
        /// <summary>Runs before the target. May skip the original.</summary>
        Prefix,

        /// <summary>Runs after the target. May replace the return value.</summary>
        Postfix,
    }

    /// <summary>
    /// A patch declared from Python (``PATCH_ADD``). The wire shape is deliberately flat so it can
    /// be logged, diffed and replayed:
    ///
    /// <code>
    /// {"owner": "BattleUnitModel", "method": "Heal", "kind": "postfix",
    ///  "sig": ["int"], "fn": "mymod.on_heal", "fallback": null}
    /// </code>
    /// </summary>
    public sealed class PatchSpec
    {
        public string Owner { get; private set; } = string.Empty;
        public string Method { get; private set; } = string.Empty;
        public PatchKind Kind { get; private set; } = PatchKind.Postfix;
        public string Fn { get; private set; } = string.Empty;
        public string[] Signature { get; private set; } = Array.Empty<string>();
        public bool HasFallback { get; private set; }
        public object Fallback { get; private set; }

        public static PatchKind ParseKind(string text)
        {
            switch ((text ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "prefix":
                case "before":
                    return PatchKind.Prefix;
                case "postfix":
                case "after":
                case "":
                    return PatchKind.Postfix;
                default:
                    throw LpcpFault.Marshal("unknown patch kind '" + text + "' (expected prefix|postfix)");
            }
        }

        public static PatchSpec FromWire(Dictionary<string, object> payload)
        {
            if (payload == null) throw LpcpFault.Marshal("PATCH_ADD requires a payload");

            var spec = new PatchSpec();
            var nested = Lpcp.GetMap(payload, "spec");
            if (nested != null) payload = nested;

            spec.Owner = Lpcp.GetString(payload, "owner");
            spec.Method = Lpcp.GetString(payload, "method");
            spec.Fn = Lpcp.GetString(payload, "fn");
            spec.Kind = ParseKind(Lpcp.GetString(payload, "kind"));
            spec.Signature = Lpcp.GetStringArray(payload, "sig");
            spec.HasFallback = payload.ContainsKey("fallback");
            spec.Fallback = spec.HasFallback ? payload["fallback"] : null;

            if (string.IsNullOrWhiteSpace(spec.Owner))
                throw LpcpFault.Marshal("PATCH_ADD requires 'owner' (a type name)");
            if (string.IsNullOrWhiteSpace(spec.Method))
                throw LpcpFault.Marshal("PATCH_ADD requires 'method'");
            if (string.IsNullOrWhiteSpace(spec.Fn))
                throw LpcpFault.Marshal("PATCH_ADD requires 'fn' (the exposed Python function name)");

            return spec;
        }

        public Dictionary<string, object> ToWire()
        {
            var payload = Lpcp.Map(
                "owner", Owner,
                "method", Method,
                "kind", Kind == PatchKind.Prefix ? "prefix" : "postfix",
                "fn", Fn);
            if (Signature.Length > 0) payload["sig"] = Signature.Cast<object>().ToArray();
            if (HasFallback) payload["fallback"] = Fallback;
            return payload;
        }

        public override string ToString()
            => Owner + "." + Method + "(" + string.Join(",", Signature) + ") " + Kind + " -> " + Fn;
    }

    /// <summary>One applied patch: the resolved method plus what Python asked for.</summary>
    public sealed class PatchBinding
    {
        public PatchBinding(string id, PatchSpec spec, Type owner, MethodInfo method)
        {
            Id = id;
            Spec = spec;
            Owner = owner;
            Method = method;
        }

        public string Id { get; }
        public PatchSpec Spec { get; }
        public Type Owner { get; }
        public MethodInfo Method { get; }
        public long Calls;
        public long Faults;

        public string Describe()
            => Method.DeclaringType?.FullName + "." + Method.Name + " <- " + Id + " (" + Spec.Kind + " -> " + Spec.Fn + ")";
    }

    /// <summary>One dispatch of a prefix/postfix into Python.</summary>
    public sealed class PatchCall
    {
        public PatchCall(PatchBinding binding, object instance, object[] args)
        {
            Binding = binding;
            Instance = instance;
            Args = args;
        }

        public PatchBinding Binding { get; }
        public object Instance { get; }
        public object[] Args { get; }

        /// <summary>Result value seen/returned by the hook (postfix: original result).</summary>
        public object Result { get; set; }

        /// <summary>Prefix only: skip the original method.</summary>
        public bool Skip { get; set; }

        /// <summary>Set when the hook could not be reached (deadline, kernel fault).</summary>
        public string Failure { get; set; }
    }

    /// <summary>
    /// Resolves the wire target of a patch to a concrete <see cref="MethodInfo"/>.
    ///
    /// Overload selection: an explicit ``sig`` wins; otherwise a unique name match is required -
    /// guessing between overloads silently patching the wrong method is worse than failing.
    /// </summary>
    public static class PatchMethodResolver
    {
        private const BindingFlags AllMembers =
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.FlattenHierarchy;

        public static MethodInfo Resolve(ITypeIndex types, string owner, string method, string[] signature)
        {
            if (!types.TryGetType(owner, out var type))
            {
                throw LpcpFault.NotFound("patch target type not found: '" + owner + "'",
                    Lpcp.Map("owner", owner));
            }

            var candidates = type.GetMethods(AllMembers)
                .Where(candidate => candidate.Name == method && !candidate.IsGenericMethodDefinition)
                .ToArray();

            if (candidates.Length == 0)
            {
                throw LpcpFault.NotFound("no method '" + method + "' on " + type.FullName,
                    Lpcp.Map("owner", type.FullName, "method", method,
                        "known", type.GetMethods(AllMembers)
                            .Where(candidate => candidate.Name.IndexOf(method, StringComparison.OrdinalIgnoreCase) >= 0)
                            .Select(candidate => candidate.Name).Distinct().Take(8).Cast<object>().ToArray()));
            }

            if (signature != null && signature.Length > 0)
            {
                var matches = candidates.Where(candidate => SignatureMatches(candidate, signature)).ToArray();
                if (matches.Length == 1) return matches[0];
                if (matches.Length > 1)
                {
                    throw LpcpFault.Ambiguous("several overloads of " + type.FullName + "." + method +
                                              " match the given signature",
                        Lpcp.Map("candidates", matches.Select(Describe).Cast<object>().ToArray()));
                }

                throw LpcpFault.NotFound("no overload of " + type.FullName + "." + method +
                                         " matches (" + string.Join(", ", signature) + ")",
                    Lpcp.Map("candidates", candidates.Select(Describe).Take(8).Cast<object>().ToArray()));
            }

            var byArity = candidates;
            if (byArity.Length > 1)
            {
                throw LpcpFault.Ambiguous(type.FullName + "." + method + " has " + byArity.Length +
                                          " overloads; pass 'sig' to disambiguate",
                    Lpcp.Map("candidates", byArity.Select(Describe).Take(8).Cast<object>().ToArray()));
            }

            return byArity[0];
        }

        private static bool SignatureMatches(MethodInfo method, string[] signature)
        {
            var parameters = method.GetParameters();
            if (parameters.Length != signature.Length) return false;
            for (var index = 0; index < parameters.Length; index++)
            {
                if (!TypeNameMatches(parameters[index].ParameterType, signature[index])) return false;
            }
            return true;
        }

        private static bool TypeNameMatches(Type type, string text)
        {
            if (string.IsNullOrEmpty(text)) return true;
            var candidate = text.Trim();
            return string.Equals(type.Name, candidate, StringComparison.OrdinalIgnoreCase)
                   || string.Equals(type.FullName, candidate, StringComparison.OrdinalIgnoreCase)
                   || string.Equals(Simplify(type), candidate, StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>"int"/"string"/"List&lt;X&gt;" style names for signatures people actually write.</summary>
        public static string Simplify(Type type)
        {
            if (type == typeof(int)) return "int";
            if (type == typeof(float)) return "float";
            if (type == typeof(bool)) return "bool";
            if (type == typeof(string)) return "string";
            if (type == typeof(long)) return "long";
            if (type == typeof(double)) return "double";
            if (type == typeof(void)) return "void";
            if (type == typeof(byte)) return "byte";
            if (type == typeof(short)) return "short";
            if (type == typeof(object)) return "object";
            if (type.IsGenericType)
            {
                var name = type.Name.Substring(0, type.Name.IndexOf('`'));
                return name + "<" + string.Join(",", type.GetGenericArguments().Select(Simplify)) + ">";
            }
            return type.Name;
        }

        public static string Describe(MethodInfo method)
            => (method.IsStatic ? "static " : "") + Simplify(method.ReturnType) + " " + method.Name +
               "(" + string.Join(", ", method.GetParameters().Select(parameter => Simplify(parameter.ParameterType))) + ")";
    }
}
