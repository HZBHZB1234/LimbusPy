using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using LimbusPy.Bridge.Core.Protocol;
using UnityEngine;

namespace LimbusPy.Ops
{
    /// <summary>战斗级 op：全场单位、回合数。</summary>
    internal sealed class BattleOps
    {
        /// <summary>``battle.units``：全场单位的扁平快照，按 instance_id 升序。</summary>
        public object Units(Dictionary<string, object> args)
        {
            var rows = new List<Dictionary<string, object>>();
            var manager = OpsArgs.Manager;
            if (manager == null) return rows.ToArray();

            var source = manager._allUnitDictionary;
            if (source == null) return rows.ToArray();

            // 与 SnapshotService 同一套取法：字典的值是 BattleObjectManager.BattleUnit 包装体，
            // 真正的模型在 .Model 上。
            if (source is IDictionary dictionary)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    var unit = ExtractModel(entry.Value);
                    if (unit != null) rows.Add(OpsArgs.Info(unit));
                }
            }

            rows.Sort((left, right) =>
                Convert.ToInt64(left["instance_id"]).CompareTo(Convert.ToInt64(right["instance_id"])));
            return rows.ToArray();
        }

        /// <summary>
        /// ``battle.states``：全场单位的**富状态**一次拿全（``unit.state`` 的批版）。
        ///
        /// 这是"属性组"的落地：遍历一次管理器字典，每个单位多读 buff 数量与等级，
        /// 全部在同一个主线程时间片里完成 —— 全场 11 个单位也只要 **1 次**跨进程往返，
        /// 而逐个 ``unit.state`` 是 11 次。
        /// </summary>
        public object States(Dictionary<string, object> args)
        {
            var rows = new List<Dictionary<string, object>>();
            var manager = OpsArgs.Manager;
            if (manager == null) return rows.ToArray();

            var source = manager._allUnitDictionary;
            if (source == null) return rows.ToArray();

            if (source is IDictionary dictionary)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    var unit = ExtractModel(entry.Value);
                    if (unit != null) rows.Add(OpsArgs.State(unit));
                }
            }

            rows.Sort((left, right) =>
                Convert.ToInt64(left["instance_id"]).CompareTo(Convert.ToInt64(right["instance_id"])));
            return rows.ToArray();
        }

        /// <summary>
        /// ``battle.players`` / ``battle.enemies``：按阵营过滤的全场快照。
        ///
        /// 比"把全场拉回来再在 Python 里筛"省一半字节，而且语义更清楚。
        /// </summary>
        public object Players(Dictionary<string, object> args) => Filtered(true);

        public object Enemies(Dictionary<string, object> args) => Filtered(false);

        private static object Filtered(bool players)
        {
            var rows = new List<Dictionary<string, object>>();
            var manager = OpsArgs.Manager;
            if (manager == null) return rows.ToArray();

            var source = manager._allUnitDictionary;
            if (source == null) return rows.ToArray();

            if (source is IDictionary dictionary)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    var unit = ExtractModel(entry.Value);
                    if (unit == null) continue;
                    var info = OpsArgs.Info(unit);
                    if (info != null && Convert.ToBoolean(info["is_player"]) == players) rows.Add(info);
                }
            }

            rows.Sort((left, right) =>
                Convert.ToInt64(left["instance_id"]).CompareTo(Convert.ToInt64(right["instance_id"])));
            return rows.ToArray();
        }

        /// <summary>
        /// ``battle.damage`` / ``battle.heal``：对一组单位做同一件事。
        ///
        /// N 个目标仍然只有 **1 次**跨进程往返 —— 逐目标调 ``unit.damage`` 是 N 次。
        /// 这也是"批量动作"和"批量读"对称的地方。
        /// </summary>
        public object DamageMany(Dictionary<string, object> args) => ApplyMany(args, true);

        public object HealMany(Dictionary<string, object> args) => ApplyMany(args, false);

        private static object ApplyMany(Dictionary<string, object> args, bool damage)
        {
            var rows = new List<Dictionary<string, object>>();
            if (args == null || !args.TryGetValue("units", out var raw) || !(raw is IEnumerable list))
            {
                return rows.ToArray();
            }

            var amount = OpsArgs.Int(args, "amount", 0);
            var source = OpsArgs.EnumValue(args, "source", DAMAGE_SOURCE_TYPE.PASSIVE);
            var timing = OpsArgs.EnumValue(args, "timing", BATTLE_EVENT_TIMING.ON_SUCCESS_ATTACK);

            foreach (var item in list)
            {
                BattleUnitModel unit;
                try
                {
                    unit = OpsArgs.Unit(new Dictionary<string, object>(StringComparer.Ordinal) { ["unit"] = item });
                }
                catch
                {
                    continue; // 单个目标解析失败不影响其它目标
                }

                if (unit == null) continue;
                if (OpsArgs.SafeBool(() => unit.IsDead()))
                {
                    rows.Add(OpsArgs.Info(unit, false));
                    continue;
                }

                var targetHp = damage
                    ? Math.Max(0, unit.Hp - Math.Max(0, amount))
                    : Math.Min(unit.MaxHp, unit.Hp + Math.Max(0, amount));
                try
                {
                    unit.ChangeHp(targetHp, damage ? source : DAMAGE_SOURCE_TYPE.BUFF, timing,
                        null, null, null, BUFF_UNIQUE_KEYWORD.None, false, false);
                }
                catch
                {
                    rows.Add(OpsArgs.Info(unit, false));
                    continue;
                }

                rows.Add(OpsArgs.Info(unit));
            }

            return rows.ToArray();
        }

        /// <summary>
        /// ``battle.round``：当前回合数。
        ///
        /// 游戏把回合数放在 ``StageController._currentRound``（ObscuredInt，混淆包装），
        /// 读不到就返回 null —— 契约里就是 Optional[int]，调用方必须能处理 None。
        /// </summary>
        public object Round(Dictionary<string, object> args)
        {
            try
            {
                // StageController 继承 Singleton<StageController>（不是 MonoBehaviour），所以
                // FindObjectOfType 拿不到；找它的静态访问器（Instance/Current）。
                var controller = StaticInstance(typeof(StageController));
                if (controller == null) return null;
                var field = controller.GetType().GetField("_currentRound",
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (field == null) return null;
                var raw = field.GetValue(controller);
                if (raw == null) return null;
                // ObscuredInt 提供到 int 的隐式转换；用 Convert 兜住不同的实现。
                return (long)Convert.ToInt32(raw);
            }
            catch
            {
                return null;
            }
        }

        private static object StaticInstance(Type type)
        {
            const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
            foreach (var name in new[] { "Instance", "instance", "Current", "current", "_instance" })
            {
                try
                {
                    var property = type.GetProperty(name, flags);
                    if (property != null && property.GetIndexParameters().Length == 0)
                    {
                        var value = property.GetValue(null);
                        if (value != null) return value;
                    }

                    var field = type.GetField(name, flags);
                    if (field != null)
                    {
                        var value = field.GetValue(null);
                        if (value != null) return value;
                    }
                }
                catch
                {
                    // 换下一个候选
                }
            }

            return null;
        }

        private static BattleUnitModel ExtractModel(object wrapper)
        {
            if (wrapper == null) return null;
            if (wrapper is BattleUnitModel model) return model;
            var type = wrapper.GetType();
            var property = type.GetProperty("Model",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            if (property != null && property.GetIndexParameters().Length == 0)
            {
                try
                {
                    return property.GetValue(wrapper) as BattleUnitModel;
                }
                catch
                {
                    return null;
                }
            }

            return null;
        }
    }
}
