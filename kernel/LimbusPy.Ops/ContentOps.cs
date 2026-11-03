using System;
using System.Collections.Generic;
using HarmonyLib;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Ops
{
    /// <summary>
    /// 内容注册相关的 op。
    ///
    /// 目前只有一个：<c>content.plan</c>。它把「这个 mod 关心哪些 buff」从 Python 带到 C#，
    /// 之后每次补丁回调都由 <see cref="MaskPlan"/> 算出一个整数位图 —— 于是
    /// "受击者身上有没有某 Buff"在 Python 侧退化成一次按位与，**零跨进程往返**。
    /// </summary>
    internal sealed class ContentOps
    {
        /// <summary><c>content.plan</c>：接收并（覆盖）保存一个 mod 的位图计划。</summary>
        public object Plan(Dictionary<string, object> args)
        {
            if (args == null) return false;
            var mod = OpsArgs.Str(args, "mod", "");
            return MaskPlan.Load(mod, args);
        }

        /// <summary>
        /// <c>content.register</c>：把自定义 Buff 的关键字真注入游戏枚举，并登记图标。
        ///
        /// 幂等；失败时返回 false 而**不抛**（注入不可用不该让 mod 起不来）。
        /// 注入后这些关键字可以直接用于 <c>unit.add_buff</c> / <c>unit.buff_stacks</c>，
        /// 以及注册层谓词的位图判断。
        /// </summary>
        public object Register(Dictionary<string, object> args)
        {
            if (args == null) return false;
            var keywords = StringList(args, "keywords");
            var icons = StringList(args, "icons");
            BuffInject.Inject(keywords, icons);
            var root = OpsArgs.Str(args, "icon_root", "");
            if (!string.IsNullOrEmpty(root))
            {
                // 补丁只装一次；重复调用是幂等的（Harmony 按方法去重）
                BuffInject.InstallIcons(new HarmonyLib.Harmony("LimbusPy.Curated.Content"), root);
            }

            return BuffInject.Available;
        }

        private static List<string> StringList(Dictionary<string, object> args, string key)
        {
            var result = new List<string>();
            if (!args.TryGetValue(key, out var raw) || !(raw is System.Collections.IEnumerable items)) return result;
            foreach (var item in items) result.Add(Convert.ToString(item) ?? string.Empty);
            return result;
        }
    }

    /// <summary>
    /// 位图计划：时点 → 要判断的 buff 位。
    ///
    /// 位序号由 Python 侧分配（<c>watch</c> 数组的下标），C# 只按同一份顺序求值。
    /// 多个 mod 的计划会合并（同一个时点的位取并集），因为补丁回调是全局面板。
    /// </summary>
    internal static class MaskPlan
    {
        private static readonly object Gate = new object();

        // 时点 -> 位序号数组
        private static readonly Dictionary<string, int[]> VictimBits =
            new Dictionary<string, int[]>(StringComparer.Ordinal);

        private static readonly Dictionary<string, int[]> SelfBits =
            new Dictionary<string, int[]>(StringComparer.Ordinal);

        // 位序号 -> 关键字（各 mod 的下标空间不同，所以按 (mod) 存一份映射）
        private static readonly Dictionary<string, string[]> WatchByMod =
            new Dictionary<string, string[]>(StringComparer.Ordinal);

        // 合并后的求值表：时点 -> (位序号 -> 关键字)
        private static readonly Dictionary<string, Dictionary<int, string>> VictimKeywords =
            new Dictionary<string, Dictionary<int, string>>(StringComparer.Ordinal);

        private static readonly Dictionary<string, Dictionary<int, string>> SelfKeywords =
            new Dictionary<string, Dictionary<int, string>>(StringComparer.Ordinal);

        /// <summary>已收到计划的数量（诊断用）。</summary>
        internal static int Plans { get; private set; }

        /// <summary>合并进求值表的时点数（诊断用）。</summary>
        internal static int Timings => VictimKeywords.Count + SelfKeywords.Count;

        /// <summary>解析并保存一个 mod 的计划。</summary>
        internal static bool Load(string mod, Dictionary<string, object> args)
        {
            if (!(args.TryGetValue("watch", out var rawWatch) && rawWatch is System.Collections.IEnumerable watchList))
            {
                return false;
            }

            var watch = new List<string>();
            foreach (var item in watchList) watch.Add(Convert.ToString(item) ?? string.Empty);

            lock (Gate)
            {
                WatchByMod[mod ?? string.Empty] = watch.ToArray();
                if (!(args.TryGetValue("hooks", out var rawHooks) && rawHooks is System.Collections.IEnumerable hooks))
                {
                    Plans++;
                    return true;
                }

                foreach (var item in hooks)
                {
                    if (!(item is Dictionary<string, object> entry)) continue;
                    var timing = OpsArgs.Str(entry, "timing", "");
                    if (string.IsNullOrEmpty(timing)) continue;
                    Merge(VictimKeywords, timing, entry, "victim", watch);
                    Merge(SelfKeywords, timing, entry, "self", watch);
                }

                Plans++;
            }

            return true;
        }

        private static void Merge(Dictionary<string, Dictionary<int, string>> table, string timing,
            Dictionary<string, object> entry, string key, List<string> watch)
        {
            if (!(entry.TryGetValue(key, out var raw) && raw is System.Collections.IEnumerable bits)) return;
            if (!table.TryGetValue(timing, out var map))
            {
                map = new Dictionary<int, string>();
                table[timing] = map;
            }

            foreach (var item in bits)
            {
                var index = OpsArgs.SafeInt(() => Convert.ToInt32(item));
                if (index < 0 || index >= watch.Count) continue;
                map[index] = watch[index];
            }
        }

        /// <summary>这个时点要不要算位图。</summary>
        internal static bool TryGet(string timing, out Dictionary<int, string> victim,
            out Dictionary<int, string> self)
        {
            victim = null;
            self = null;
            if (string.IsNullOrEmpty(timing)) return false;
            lock (Gate)
            {
                VictimKeywords.TryGetValue(timing, out victim);
                SelfKeywords.TryGetValue(timing, out self);
            }

            return victim != null || self != null;
        }

        /// <summary>
        /// 算一个单位的位图。每个被关注的 buff 查一次层数——
        /// 这是 C# 侧的同进程调用，不产生 IPC。
        /// </summary>
        internal static long Mask(BattleUnitModel unit, Dictionary<int, string> bits)
        {
            if (unit == null || bits == null || bits.Count == 0) return 0L;
            long mask = 0L;
            foreach (var pair in bits)
            {
                if (pair.Key < 0 || pair.Key > 62) continue;
                if (!TryParseKeyword(pair.Value, out var keyword)) continue;
                var stacks = OpsArgs.SafeInt(() => unit.GetActivatedBuffStack(keyword, false));
                if (stacks > 0) mask |= 1L << pair.Key;
            }

            return mask;
        }

        /// <summary>
        /// 解析 Buff 关键字。走 <see cref="BuffInject.TryParse"/> 而不是裸
        /// <c>Enum.TryParse</c>：自定义 Buff 的关键字只存在于注入表里，
        /// 原版枚举解析认不出来（这条在真机上是否成立还没验证，注入表是确定的）。
        /// </summary>
        private static bool TryParseKeyword(string text, out BUFF_UNIQUE_KEYWORD keyword)
            => BuffInject.TryParse(text, out keyword);

        /// <summary>热重载/卸载时清空（避免旧代际的位序号残留）。</summary>
        internal static void Clear()
        {
            lock (Gate)
            {
                VictimBits.Clear();
                SelfBits.Clear();
                WatchByMod.Clear();
                VictimKeywords.Clear();
                SelfKeywords.Clear();
                Plans = 0;
            }
        }
    }
}
