using System;
using System.Collections.Generic;
using LimbusPy.Bridge.Core.Ops;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Reflect;

namespace LimbusPy.Ops
{
    /// <summary>
    /// op 参数的解析工具：Python 侧的命名参数 → 游戏对象/枚举。
    ///
    /// 单位引用接受三种写法（Python 的 ``to_wire_unit`` 会归一化）：
    /// ``{"h": n}``（句柄）、``{"instance_id": n}``（稳定 id）、直接传给 handle 表。
    /// </summary>
    internal static class OpsArgs
    {
        /// <summary>战斗对象管理器（单例；战斗未开始时为 null）。</summary>
        internal static BattleObjectManager Manager
        {
            get
            {
                try
                {
                    return SingletonBehaviorCollector.BattleObjectManager;
                }
                catch
                {
                    return null;
                }
            }
        }

        internal static BattleUnitModel Unit(Dictionary<string, object> args, string key = "unit")
        {
            if (args == null || !args.TryGetValue(key, out var wire) || wire == null)
            {
                throw LpcpFault.Marshal("missing argument '" + key + "' (unit reference)");
            }

            if (Lpcp.TryReadRef(wire, out _, out var handle, out var isNull) && !isNull)
            {
                return FromHandle(handle);
            }

            if (wire is Dictionary<string, object> map)
            {
                if (map.TryGetValue("h", out var rawHandle) && rawHandle != null)
                {
                    return FromHandle(Convert.ToUInt32(rawHandle));
                }

                if (map.TryGetValue("instance_id", out var rawId) && rawId != null)
                {
                    var instanceId = Convert.ToInt32(rawId);
                    var model = Manager?.GetModel(instanceId);
                    if (model != null) return model;
                    throw LpcpFault.NotFound("no unit with instance_id=" + instanceId);
                }
            }

            throw LpcpFault.Marshal("unit argument must be a handle ref or {\"instance_id\": n}");
        }

        private static BattleUnitModel FromHandle(uint handle)
        {
            var store = OpsEntry.Store;
            if (store != null && store.TryResolve(handle, out var target) && target is BattleUnitModel unit)
            {
                return unit;
            }

            throw LpcpFault.StaleHandle("unit handle 0x" + handle.ToString("x") + " is not live");
        }

        internal static int Int(Dictionary<string, object> args, string key, int fallback)
        {
            if (args != null && args.TryGetValue(key, out var value) && value != null)
            {
                try
                {
                    return Convert.ToInt32(value);
                }
                catch (Exception ex) when (ex is FormatException || ex is InvalidCastException || ex is OverflowException)
                {
                    throw LpcpFault.Marshal("argument '" + key + "' must be an int");
                }
            }

            return fallback;
        }

        internal static string Str(Dictionary<string, object> args, string key, string fallback)
        {
            if (args != null && args.TryGetValue(key, out var value) && value != null)
            {
                return Convert.ToString(value);
            }

            return fallback;
        }

        /// <summary>按**成员名**解析枚举（大小写不敏感）；未知名字报 marshal 并附提示。</summary>
        internal static TEnum EnumValue<TEnum>(Dictionary<string, object> args, string key, TEnum fallback)
            where TEnum : struct, Enum
        {
            var text = Str(args, key, null);
            if (string.IsNullOrEmpty(text)) return fallback;

            if (System.Enum.TryParse<TEnum>(text, true, out var parsed)) return parsed;

            throw LpcpFault.Marshal("argument '" + key + "' is not a member of " + typeof(TEnum).Name + ": '" + text + "'");
        }

        /// <summary>
        /// 解析 Buff 关键字：先查注入表（自定义 Buff），再退回原版枚举。
        ///
        /// 必须走这一层而不是直接 <c>Enum.TryParse</c>：真机上 <c>Enum.TryParse</c>
        /// 能不能认出 <c>EnumInjector</c> 注入的成员还没验证，而我们的注入表是确定的。
        /// </summary>
        internal static BUFF_UNIQUE_KEYWORD BuffKeyword(Dictionary<string, object> args, string key)
        {
            var text = Str(args, key, null);
            if (string.IsNullOrEmpty(text)) return default;
            if (BuffInject.TryParse(text, out var parsed)) return parsed;
            throw LpcpFault.Marshal("argument '" + key + "' is not a known buff keyword: '" + text + "'");
        }

        // -- 扁平状态 -------------------------------------------------------------------

        internal static Dictionary<string, object> Info(BattleUnitModel unit, bool applied = true)
        {
            if (unit == null) return null;
            var faction = SafeEnumName(() => unit.GetOpponentFaction().ToString());
            // GetOpponentFaction() 返回的是"对手"的阵营：玩家单位 → ENEMY，敌人 → PLAYER。
            var isPlayer = string.Equals(faction, "ENEMY", StringComparison.OrdinalIgnoreCase);
            return Lpcp.Map(
                "instance_id", (long)unit.InstanceID,
                "origin_id", (long)SafeInt(() => unit.GetOriginUnitID()),
                "hp", (long)unit.Hp,
                "max_hp", (long)unit.MaxHp,
                "mp", (long)unit.Mp,
                "position", SafeEnumName(() => unit.Position.ToString()),
                "is_dead", SafeBool(() => unit.IsDead()),
                "is_break", SafeBool(() => unit.IsBreak()),
                "faction", faction,
                "is_player", isPlayer,
                "applied", applied);
        }

        internal static Dictionary<string, object> BuffInfo(BuffModel buff)
        {
            return Lpcp.Map(
                "keyword", SafeEnumName(() => buff.GetMainKeyword().ToString()),
                "stacks", (long)SafeInt(() => buff.GetCurrentStack()),
                "turns", (long)SafeInt(() => buff.GetCurrentTurn()),
                "max_stacks", (long)SafeInt(() => buff.GetMaxStack()),
                "max_turns", (long)SafeInt(() => buff.GetMaxTurn()),
                "name", SafeString(() => buff.GetName()));
        }

        /// <summary>
        /// ``unit.state``：比 <see cref="Info"/> 多带 buff 数量，仍然是**一次** crossing。
        ///
        /// 为什么不把所有字段都塞进来：每多读一个属性就多一次游戏侧调用，而绝大多数场景
        /// 只要 "血量 + 是否崩溃 + 身上几个 buff"。要全景就别用 state，用 B 层的 props。
        /// </summary>
        internal static Dictionary<string, object> State(BattleUnitModel unit, bool applied = true)
        {
            var state = Info(unit, applied);
            if (state == null) return null;
            state["buff_count"] = (long)SafeInt(() => unit.GetActivatedBuffModels().Count);
            state["level"] = (long)SafeInt(() => unit.GetLevel());
            return state;
        }

        internal static string SafeEnumName(Func<string> read)
        {
            try
            {
                return read() ?? string.Empty;
            }
            catch
            {
                return string.Empty;
            }
        }

        internal static int SafeInt(Func<int> read)
        {
            try
            {
                return read();
            }
            catch
            {
                return 0;
            }
        }

        internal static bool SafeBool(Func<bool> read)
        {
            try
            {
                return read();
            }
            catch
            {
                return false;
            }
        }

        internal static string SafeString(Func<string> read)
        {
            try
            {
                return read() ?? string.Empty;
            }
            catch
            {
                return string.Empty;
            }
        }
    }
}
