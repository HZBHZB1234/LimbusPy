using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Reflect;

namespace LimbusPy.Bridge.Patching
{
    /// <summary>
    /// Applies Python-declared patches through Harmony from a data table.
    ///
    /// Properties that make this safe enough for a scripted mod layer:
    ///  * **no recompilation**: patches are added/removed at runtime, which is what lets a Python
    ///    mod add a hook for a method nobody wrapped;
    ///  * **one Harmony patch per method**: several bindings on the same method share it, and
    ///    removing a binding never unpatches something another binding still needs;
    ///  * **fail-open**: a Python timeout or exception leaves the original behaviour untouched
    ///    (prefix continues, postfix keeps the original result) and is counted, never thrown.
    /// </summary>
    public sealed class HarmonyPatchHub
    {
        private readonly Harmony _harmony;
        private readonly ITypeIndex _types;
        private readonly CallMarshaller _marshaller;
        private readonly Action _ensureMainThread;

        private readonly Dictionary<string, PatchBinding> _byId = new Dictionary<string, PatchBinding>(StringComparer.Ordinal);
        private readonly Dictionary<MethodBase, List<PatchBinding>> _byMethod = new Dictionary<MethodBase, List<PatchBinding>>();
        private readonly Dictionary<MethodBase, bool> _patched = new Dictionary<MethodBase, bool>();
        private readonly object _lock = new object();

        public HarmonyPatchHub(string harmonyId, ITypeIndex types, CallMarshaller marshaller, Action ensureMainThread = null)
        {
            _harmony = new Harmony(string.IsNullOrEmpty(harmonyId) ? "LimbusPy.PatchHub" : harmonyId);
            _types = types ?? throw new ArgumentNullException(nameof(types));
            _marshaller = marshaller ?? throw new ArgumentNullException(nameof(marshaller));
            _ensureMainThread = ensureMainThread;
            PatchHooks.Active = this;
        }

        /// <summary>Bridges one dispatch into Python. Set by the kernel; may block (nested pump).</summary>
        public Func<PatchCall, object> Dispatch { get; set; }

        /// <summary>Deadline for one dispatch; the kernel applies its own budget on top.</summary>
        public int TimeoutMs { get; set; } = 20;

        public long Dispatches { get; private set; }
        public long Failures { get; private set; }

        // -- apply / remove ----------------------------------------------------------------

        public PatchBinding Apply(PatchSpec spec, string id = null)
        {
            if (spec == null) throw new ArgumentNullException(nameof(spec));
            if (Dispatch == null) throw LpcpFault.Unsupported("patch hub has no dispatch target");

            var method = PatchMethodResolver.Resolve(_types, spec.Owner, spec.Method, spec.Signature);
            if (method.DeclaringType != null && method.DeclaringType.IsValueType)
            {
                throw LpcpFault.Unsupported(
                    "cannot patch a method on value type " + method.DeclaringType.FullName +
                    " (Harmony needs the exact instance type; IL2CPP has no runtime codegen)");
            }

            id = string.IsNullOrEmpty(id) ? spec.Owner + "." + spec.Method + "#" + PatchIdCounter.Next : id;
            var binding = new PatchBinding(id, spec, _types.TryGetType(spec.Owner, out var owner) ? owner : method.DeclaringType, method);

            lock (_lock)
            {
                if (_byId.ContainsKey(id)) throw LpcpFault.Marshal("patch id already in use: " + id);
                _byId[id] = binding;
                if (!_byMethod.TryGetValue(method, out var list))
                {
                    list = new List<PatchBinding>();
                    _byMethod[method] = list;
                }

                list.Add(binding);
            }

            EnsureHarmonyPatch(method, spec.Kind);
            return binding;
        }

        public bool Remove(string id)
        {
            if (string.IsNullOrEmpty(id)) return false;
            PatchBinding binding;
            lock (_lock)
            {
                if (!_byId.TryGetValue(id, out binding)) return false;
                _byId.Remove(id);
                if (_byMethod.TryGetValue(binding.Method, out var list))
                {
                    list.RemoveAll(candidate => candidate.Id == id);
                    // The Harmony patch itself stays applied (harmless pass-through); unpatching
                    // could remove a sibling binding's patch.
                }
            }
            return true;
        }

        public int RemoveAll()
        {
            lock (_lock)
            {
                var count = _byId.Count;
                _byId.Clear();
                _byMethod.Clear();
                return count;
            }
        }

        /// <summary>Harmony-level cleanup (plugin unload). Only safe when nothing else patched.</summary>
        public void UnpatchAll()
        {
            RemoveAll();
            try
            {
                _harmony.UnpatchSelf();
            }
            catch
            {
                // best effort
            }
            PatchHooks.Active = null;
        }

        public IReadOnlyList<PatchBinding> Bindings
        {
            get
            {
                lock (_lock) return _byId.Values.ToArray();
            }
        }

        public int Count
        {
            get
            {
                lock (_lock) return _byId.Count;
            }
        }

        // -- dispatch (called from Harmony) -------------------------------------------------

        internal bool RunPrefix(MethodBase method, object instance, object[] args)
        {
            PatchBinding[] bindings = PickBindings(method, PatchKind.Prefix);
            if (bindings.Length == 0) return true;

            _ensureMainThread?.Invoke();
            foreach (var binding in bindings)
            {
                var call = new PatchCall(binding, instance, args) { Result = null };
                var reply = Invoke(binding, call);
                if (reply == null) continue;

                if (reply is bool flag)
                {
                    if (!flag) return false;
                    continue;
                }

                if (reply is Dictionary<string, object> map)
                {
                    if (Lpcp.GetBool(map, "skip")) return false;
                    if (map.TryGetValue("args", out var replacement) && replacement is object[] newArgs)
                    {
                        var parameters = binding.Method.GetParameters();
                        var length = Math.Min(Math.Min(args.Length, newArgs.Length), parameters.Length);
                        for (var index = 0; index < length; index++)
                        {
                            try
                            {
                                args[index] = _marshaller.ToManaged(newArgs[index], parameters[index].ParameterType,
                                    "patch arg " + index);
                            }
                            catch (LpcpFault fault)
                            {
                                binding.Faults++;
                                Failures++;
                                LastFailure = fault.Message;
                            }
                        }
                    }
                }
            }

            return true;
        }

        /// <summary>Returns true when the (boxed) result should be written back.</summary>
        internal bool RunPostfix(MethodBase method, object instance, object[] args, Type resultType, ref object boxedResult)
        {
            PatchBinding[] bindings = PickBindings(method, PatchKind.Postfix);
            if (bindings.Length == 0) return false;

            _ensureMainThread?.Invoke();
            var changed = false;
            foreach (var binding in bindings)
            {
                var call = new PatchCall(binding, instance, args) { Result = boxedResult };
                var reply = Invoke(binding, call);
                if (reply == null) continue;

                if (resultType == typeof(void)) continue; // observation only

                try
                {
                    boxedResult = _marshaller.ToManaged(reply, resultType, "patch result");
                    changed = true;
                }
                catch (LpcpFault fault)
                {
                    binding.Faults++;
                    Failures++;
                    // report but keep the original result
                    LastFailure = fault.Message;
                }
            }

            return changed;
        }

        public string LastFailure { get; private set; }

        private object Invoke(PatchBinding binding, PatchCall call)
        {
            var dispatch = Dispatch;
            if (dispatch == null) return null;

            Dispatches++;
            binding.Calls++;
            try
            {
                return dispatch(call);
            }
            catch (Exception ex)
            {
                binding.Faults++;
                Failures++;
                LastFailure = ex.GetType().Name + ": " + ex.Message;
                return null;
            }
        }

        private PatchBinding[] PickBindings(MethodBase method, PatchKind kind)
        {
            lock (_lock)
            {
                if (!_byMethod.TryGetValue(method, out var list) || list.Count == 0) return Array.Empty<PatchBinding>();
                return list.Where(binding => binding.Spec.Kind == kind).ToArray();
            }
        }

        // -- harmony plumbing --------------------------------------------------------------

        private void EnsureHarmonyPatch(MethodBase method, PatchKind kind)
        {
            lock (_lock)
            {
                if (_patched.ContainsKey(method)) return;
                _patched[method] = true;
            }

            try
            {
                // Both entry points are always installed: a second binding on the same method may
                // be of the other kind, and removing a binding never unpatches the method.
                var prefix = new HarmonyMethod(MethodOf(typeof(PatchHooks), nameof(PatchHooks.Prefix)));
                var postfix = new HarmonyMethod(MethodOf(typeof(PatchHooks), PostfixNameFor(method)));
                var target = method as MethodInfo;
                if (target == null)
                {
                    throw LpcpFault.Unsupported("only methods can be patched: " + method);
                }

                _harmony.Patch(target, prefix: prefix, postfix: postfix);
            }
            catch (Exception ex)
            {
                throw LpcpFault.Unsupported("harmony refused to patch " +
                                            PatchMethodResolver.Describe(method as MethodInfo) + ": " + ex.Message);
            }
        }

        private static string PostfixNameFor(MethodBase method)
        {
            var returnType = (method as MethodInfo)?.ReturnType ?? typeof(void);
            string name;
            if (returnType == typeof(void)) name = nameof(PatchHooks.Postfix_Void);
            else if (returnType == typeof(int)) name = nameof(PatchHooks.Postfix_Int);
            else if (returnType == typeof(long)) name = nameof(PatchHooks.Postfix_Long);
            else if (returnType == typeof(float)) name = nameof(PatchHooks.Postfix_Float);
            else if (returnType == typeof(double)) name = nameof(PatchHooks.Postfix_Double);
            else if (returnType == typeof(bool)) name = nameof(PatchHooks.Postfix_Bool);
            else if (returnType == typeof(string)) name = nameof(PatchHooks.Postfix_String);
            else if (!returnType.IsValueType) name = nameof(PatchHooks.Postfix_Object);
            else name = nameof(PatchHooks.Postfix_Observe);

            return name;
        }

        public Dictionary<string, object> Stats()
        {
            lock (_lock)
            {
                return Lpcp.Map(
                    "patches", (long)_byId.Count,
                    "methods", (long)_byMethod.Count,
                    "dispatches", Dispatches,
                    "failures", Failures,
                    "last_failure", LastFailure ?? string.Empty,
                    "bindings", _byId.Values.Select(binding => (object)Lpcp.Map(
                        "id", binding.Id,
                        "target", PatchMethodResolver.Describe(binding.Method),
                        "kind", binding.Spec.Kind == PatchKind.Prefix ? "prefix" : "postfix",
                        "fn", binding.Spec.Fn,
                        "calls", binding.Calls,
                        "faults", binding.Faults)).ToArray());
            }
        }

        private static MethodInfo MethodOf(Type type, string name)
            => type.GetMethod(name, BindingFlags.Public | BindingFlags.Static)
               ?? throw LpcpFault.Unsupported("patch hook method missing: " + type.Name + "." + name);

        /// <summary>Monotonic counter for auto-generated patch ids.</summary>
        private static class PatchIdCounter
        {
            private static int _value;
            public static int Next => System.Threading.Interlocked.Increment(ref _value);
        }
    }
}
