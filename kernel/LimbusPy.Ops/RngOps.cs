using System;
using System.Collections.Generic;

namespace LimbusPy.Ops
{
    /// <summary>
    /// 随机数相关的 op。
    ///
    /// 为什么把它们放进 A 层而不是让作者在 Python 里 <c>random</c>：
    /// 战斗里的"掷骰"要能和游戏的随机源同频、并且**不引入额外的 Python↔C# 往返**。
    /// 这两个 op 是纯本地的（不碰游戏对象），所以它们的存在只是为了让作者少写一层
    /// "我怎么知道该用哪个随机源"。
    /// </summary>
    internal sealed class RngOps
    {
        //: 独立的随机源：不去污染游戏自己的 RNG 状态（那会影响存档一致性）。
        private static readonly Random Rng = new Random();

        /// <summary><c>rng.int</c>：<c>[min, max]</c> 闭区间整数。</summary>
        public object Int(Dictionary<string, object> args)
        {
            var min = OpsArgs.Int(args, "min", 0);
            var max = OpsArgs.Int(args, "max", 100);
            if (max < min) return (long)min;
            try
            {
                lock (Rng) return (long)Rng.Next(min, max == int.MaxValue ? max : max + 1);
            }
            catch
            {
                return (long)min;
            }
        }

        /// <summary><c>rng.chance</c>：千分位概率判定（<c>permille=300</c> 表示 30%）。</summary>
        public object Chance(Dictionary<string, object> args)
        {
            var permille = OpsArgs.Int(args, "permille", 0);
            if (permille <= 0) return false;
            if (permille >= 1000) return true;
            try
            {
                lock (Rng) return Rng.Next(0, 1000) < permille;
            }
            catch
            {
                return false;
            }
        }

        /// <summary><c>rng.pick</c>：从列表里随机取一个（空列表返回空串）。</summary>
        public object Pick(Dictionary<string, object> args)
        {
            var items = new List<string>();
            if (args != null && args.TryGetValue("items", out var raw) && raw is System.Collections.IEnumerable list)
            {
                foreach (var item in list) items.Add(Convert.ToString(item) ?? string.Empty);
            }

            if (items.Count == 0) return string.Empty;
            try
            {
                lock (Rng) return items[Rng.Next(0, items.Count)];
            }
            catch
            {
                return items[0];
            }
        }
    }
}
