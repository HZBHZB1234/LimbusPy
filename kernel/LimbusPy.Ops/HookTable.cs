using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using LimbusPy.Bridge.Core.Ops;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Ops
{
    /// <summary>
    /// A 层时机（curated hooks）的安装与采集。
    ///
    /// 三条泳道：
    /// <list type="bullet">
    /// <item><b>postfix（观测）</b>——回调里不碰 Python。postfix 只把参数按契约装成扁平 payload
    ///       塞进队列（O(1)，主线程零阻塞），帧末由内核一次 EVENTBATCH 推过去。高频路径用它。</item>
    /// <item><b>postfix_result（改返回值）</b>——主线程同步问 Python 要新值，带 deadline、拒绝重入、
    ///       失败即放弃改动。伤害倍率/威力修正一类走这条。</item>
    /// <item><b>prefix（改参数）</b>——同样是同步的，但改的是入参。</item>
    /// </list>
    ///
    /// 需要同步的泳道装进单独的 Harmony id（<c>LimbusPy.Curated.Mutate</c>），
    /// 与观测钩子的补丁互不干扰，也便于按需整体卸载。
    /// </summary>
    internal sealed class HookTable
    {
        private const string MutateHarmonyId = "LimbusPy.Curated.Mutate";

        private readonly Harmony _harmony;
        private readonly Harmony _mutateHarmony;
        private readonly Dictionary<MethodBase, string> _timingByMethod = new Dictionary<MethodBase, string>();
        private readonly Dictionary<MethodBase, string> _mutatorByMethod = new Dictionary<MethodBase, string>();

        private readonly ConcurrentQueue<Dictionary<string, object>> _queue =
            new ConcurrentQueue<Dictionary<string, object>>();

        private IOpsHost _host;
        private PayloadShaper _shaper;

        internal HookTable(string harmonyId)
        {
            _harmony = new Harmony(string.IsNullOrEmpty(harmonyId) ? "LimbusPy.Curated" : harmonyId);
            _mutateHarmony = new Harmony(MutateHarmonyId);
            CuratedHooks.Active = this;
        }

        /// <summary>已安装的时机（诊断用）。</summary>
        internal List<string> Installed { get; } = new List<string>();

        /// <summary>安装期发现的问题（签名漂移、找不到方法等）。</summary>
        internal List<string> Problems { get; } = new List<string>();

        /// <summary>安装契约清单里的全部时机。问题进 <see cref="Problems"/>，不抛。</summary>
        internal List<string> Install()
        {
            foreach (var spec in OpsManifest.Hooks)
            {
                try
                {
                    InstallOne(spec);
                }
                catch (Exception ex)
                {
                    Problems.Add(spec.Timing + ": " + ex.GetType().Name + ": " + ex.Message);
                }
            }

            return Problems;
        }

        private void InstallOne(HookSpec spec)
        {
            var type = PayloadShaper.ResolveType(spec.Target);
            if (type == null)
            {
                Problems.Add(spec.Timing + ": type " + spec.Target + " not found");
                return;
            }

            if (!TryResolveMethod(type, spec, out var method, out var problem))
            {
                Problems.Add(spec.Timing + ": " + problem);
                return;
            }

            if (!string.IsNullOrEmpty(problem)) Problems.Add(problem);

            // payload 绑定：按参数名解析下标 + 校验签名漂移
            Problems.AddRange(Shaper.Bind(spec.Timing, method));

            if (IsObserving(spec))
            {
                var postfix = new HarmonyMethod(typeof(CuratedHooks).GetMethod(nameof(CuratedHooks.Postfix)));
                _harmony.Patch(method, postfix: postfix);
            }
            else
            {
                var prefix = new HarmonyMethod(MutatorEntry(spec));
                _mutateHarmony.Patch(method, prefix: prefix);
                _mutatorByMethod[method] = spec.Timing;
            }

            _timingByMethod[method] = spec.Timing;
            Installed.Add(spec.Timing);
        }

        private static bool IsObserving(HookSpec spec)
            => string.Equals(spec.Kind, "postfix", StringComparison.OrdinalIgnoreCase);

        private static MethodInfo MutatorEntry(HookSpec spec)
        {
            if (string.Equals(spec.Kind, "prefix", StringComparison.OrdinalIgnoreCase))
            {
                return typeof(CuratedHooks).GetMethod(nameof(CuratedHooks.PrefixMutate));
            }

            var resultType = "int";
            if (spec.Extra != null && spec.Extra.TryGetValue("result_type", out var declared) && declared != null)
            {
                resultType = Convert.ToString(declared);
            }

            switch ((resultType ?? "int").ToLowerInvariant())
            {
                case "float":
                case "double":
                    return typeof(CuratedHooks).GetMethod(nameof(CuratedHooks.PostfixResultFloat));
                case "bool":
                    return typeof(CuratedHooks).GetMethod(nameof(CuratedHooks.PostfixResultBool));
                case "long":
                case "int64":
                    return typeof(CuratedHooks).GetMethod(nameof(CuratedHooks.PostfixResultLong));
                default:
                    return typeof(CuratedHooks).GetMethod(nameof(CuratedHooks.PostfixResultInt));
            }
        }

        /// <summary>
        /// 按**参数名**解析目标方法，而不是 <c>AccessTools.Method(type, name)</c>。
        ///
        /// 后者不传参数类型，遇到同名重载会挑错；而 <c>dump.cs</c> 里 <c>OnSucceedAttack</c>
        /// 有四种签名共存。这里用契约声明的参数名表做**逐字匹配**，既能唯一定位重载，
        /// 又顺手完成签名漂移检测。
        /// </summary>
        private static bool TryResolveMethod(Type type, HookSpec spec, out MethodInfo method, out string problem)
        {
            method = null;
            problem = null;

            var declared = HookPayloadMap.Params.TryGetValue(spec.Timing, out var names) ? names : null;
            var candidates = Candidates(type, spec.Method);
            if (candidates.Count == 0)
            {
                problem = "method " + spec.Target + "." + spec.Method + " not found";
                return false;
            }

            if (declared != null)
            {
                foreach (var candidate in candidates)
                {
                    var actual = ParameterNames(candidate);
                    if (actual.Length == declared.Length && actual.SequenceEqual(declared))
                    {
                        method = candidate;
                        return true;
                    }
                }
            }

            // 退回：没有声明参数名（或没匹配上）时，只在唯一候选上妥协，并明确报警。
            if (candidates.Count == 1)
            {
                method = candidates[0];
                if (declared != null)
                {
                    problem = string.Format(
                        "signature drift on {0}.{1}: contract [{2}] but method has [{3}]",
                        spec.Target, spec.Method, string.Join(", ", declared),
                        string.Join(", ", ParameterNames(method)));
                }

                return true;
            }

            problem = string.Format(
                "ambiguous overload: {0}.{1} has {2} candidates and none matches declared params [{3}]",
                spec.Target, spec.Method, candidates.Count,
                declared == null ? "" : string.Join(", ", declared));
            return false;
        }

        /// <summary>
        /// 取该方法名在类型链上**最靠近具体类型**的那一层声明。
        ///
        /// 子类 override 才是真正被调用的实现；同时这样也把「基类里同名但不同签名的重载」
        /// 排除在候选之外，减少歧义。
        /// </summary>
        private static List<MethodInfo> Candidates(Type type, string name)
        {
            var found = new List<MethodInfo>();
            for (var current = type; current != null && current != typeof(object); current = current.BaseType)
            {
                MethodInfo[] declared;
                try
                {
                    declared = current.GetMethods(BindingFlags.Public | BindingFlags.NonPublic |
                                                 BindingFlags.Instance | BindingFlags.Static |
                                                 BindingFlags.DeclaredOnly);
                }
                catch
                {
                    continue;
                }

                var matches = declared.Where(m => string.Equals(m.Name, name, StringComparison.Ordinal)).ToList();
                if (matches.Count > 0)
                {
                    found.AddRange(matches);
                    break;
                }
            }

            return found;
        }

        private static string[] ParameterNames(MethodBase method)
        {
            var parameters = method.GetParameters();
            var names = new string[parameters.Length];
            for (var i = 0; i < parameters.Length; i++) names[i] = parameters[i].Name;
            return names;
        }

        private PayloadShaper Shaper => _shaper ?? (_shaper = new PayloadShaper(_host));

        internal void Attach(IOpsHost host)
        {
            _host = host;
            _shaper = new PayloadShaper(host);
        }

        /// <summary>观测泳道的补丁回调入口（Harmony postfix 转进来）。</summary>
        internal void Capture(MethodBase method, object instance, object[] args)
        {
            try
            {
                if (method == null || !_timingByMethod.TryGetValue(method, out var timing)) return;
                var payload = Shaper.Shape(timing, instance, args ?? Array.Empty<object>(), null);
                if (payload == null) return;
                _queue.Enqueue(Lpcp.Map("timing", timing, "payload", payload));
            }
            catch
            {
                // 观测钩子永远不能影响游戏
            }
        }

        /// <summary>
        /// 改值泳道的入口：问 Python 要一个新值并返回它（null = 不改）。
        /// </summary>
        /// <param name="args">原始参数（prefix 时用来回写入参）。</param>
        /// <param name="returnValue">原返回值（postfix_result 时用来算新值）。</param>
        internal object Mutate(MethodBase method, object instance, object[] args, object returnValue)
        {
            try
            {
                if (method == null || !_mutatorByMethod.TryGetValue(method, out var timing)) return null;
                var host = _host;
                if (host == null) return null;

                var payload = Shaper.Shape(timing, instance, args ?? Array.Empty<object>(), returnValue);
                if (payload == null) return null;

                var context = Lpcp.Map(
                    "timing", timing,
                    "payload", payload,
                    "args", ArgWire(args),
                    "result", WireValue(returnValue),
                    "target", method.DeclaringType == null ? "" : method.DeclaringType.Name + "." + method.Name);

                return host.CallPython("curated:" + timing, context);
            }
            catch
            {
                // 改值失败 = 保持原样，绝不影响游戏
                return null;
            }
        }

        private object ArgWire(object[] args)
        {
            if (args == null) return Lpcp.Map();
            var wire = new List<object>(args.Length);
            foreach (var arg in args) wire.Add(WireValue(arg));
            return wire.ToArray();
        }

        private object WireValue(object value)
        {
            if (value == null) return null;
            try
            {
                return _host?.Wire(value) ?? value;
            }
            catch
            {
                return null;
            }
        }

        /// <summary>prefix 泳道：Python 返回 <c>{参数名: 新值}</c> 时回写入参。</summary>
        internal void MutateArgs(MethodBase method, object instance, object[] args)
        {
            var answer = Mutate(method, instance, args, null);
            if (!(answer is IDictionary<string, object> sets) || args == null) return;

            var parameterNames = ParameterNames(method);
            foreach (var pair in sets)
            {
                var index = Array.IndexOf(parameterNames, pair.Key);
                if (index < 0 || index >= args.Length) continue;
                args[index] = pair.Value;
            }
        }

        /// <summary>帧末：把攒下的事件交给内核（一次 EVENTBATCH）。</summary>
        internal void Tick()
        {
            var host = _host;
            if (host == null) return;
            while (_queue.TryDequeue(out var evt))
            {
                try
                {
                    host.EmitEvent(evt);
                }
                catch
                {
                    // 内核不在/断了：丢掉这一帧的事件，别影响游戏
                }
            }
        }

        internal void Uninstall()
        {
            try
            {
                _harmony.UnpatchSelf();
            }
            catch
            {
                // best effort
            }

            try
            {
                _mutateHarmony.UnpatchSelf();
            }
            catch
            {
                // best effort
            }

            CuratedHooks.Active = null;
        }
    }

    /// <summary>
    /// Harmony 的静态入口（与内核的 PatchHooks 同款模式：IL2CPP 没有 JIT，注入方法必须手写）。
    ///
    /// 每个重载只做一件事：把 Harmony 的调用约定翻译成 <see cref="HookTable"/> 的一次调用。
    /// 这些方法**不随覆盖范围增长** —— 加时点改的是 toml，不是这里。
    /// </summary>
    public static class CuratedHooks
    {
        internal static HookTable Active;

        // -- 观测泳道 ---------------------------------------------------------------------

        public static void Postfix(MethodBase __originalMethod, object __instance, object[] __args)
        {
            var table = Active;
            if (table == null) return;
            table.Capture(__originalMethod, __instance, __args);
        }

        // -- 改参数泳道 -------------------------------------------------------------------

        public static void PrefixMutate(MethodBase __originalMethod, object __instance, object[] __args)
        {
            var table = Active;
            if (table == null) return;
            table.MutateArgs(__originalMethod, __instance, __args);
        }

        // -- 改返回值泳道 -----------------------------------------------------------------
        // 用 postfix + ref __result 而不是 prefix：改伤害倍率这类逻辑本来就要先让游戏算完，
        // 再改它的结果。prefix 还要额外处理"跳过原方法"，语义更绕、更容易出错。

        public static void PostfixResultInt(MethodBase __originalMethod, object __instance, object[] __args,
            ref int __result)
        {
            var table = Active;
            if (table == null) return;
            var answer = table.Mutate(__originalMethod, __instance, __args, __result);
            if (answer != null) __result = ToInt(answer, __result);
        }

        public static void PostfixResultLong(MethodBase __originalMethod, object __instance, object[] __args,
            ref long __result)
        {
            var table = Active;
            if (table == null) return;
            var answer = table.Mutate(__originalMethod, __instance, __args, __result);
            if (answer != null) __result = ToLong(answer, __result);
        }

        public static void PostfixResultFloat(MethodBase __originalMethod, object __instance, object[] __args,
            ref float __result)
        {
            var table = Active;
            if (table == null) return;
            var answer = table.Mutate(__originalMethod, __instance, __args, __result);
            if (answer != null) __result = (float)ToDouble(answer, __result);
        }

        public static void PostfixResultBool(MethodBase __originalMethod, object __instance, object[] __args,
            ref bool __result)
        {
            var table = Active;
            if (table == null) return;
            var answer = table.Mutate(__originalMethod, __instance, __args, __result);
            if (answer == null) return;
            try
            {
                __result = Convert.ToBoolean(answer);
            }
            catch
            {
                // 保持原值
            }
        }

        // -- 转换（宽容，绝不抛）----------------------------------------------------------

        private static int ToInt(object value, int fallback)
        {
            try { return value == null ? fallback : Convert.ToInt32(value); }
            catch { return fallback; }
        }

        private static long ToLong(object value, long fallback)
        {
            try { return value == null ? fallback : Convert.ToInt64(value); }
            catch { return fallback; }
        }

        private static double ToDouble(object value, double fallback)
        {
            try { return value == null ? fallback : Convert.ToDouble(value); }
            catch { return fallback; }
        }
    }
}
