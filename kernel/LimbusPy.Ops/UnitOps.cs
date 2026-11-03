using System;
using System.Collections.Generic;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Ops
{
    /// <summary>
    /// 单位级 op 的原生实现。
    ///
    /// 这些方法里的每一行都对应一条"游戏知识"：``ChangeHp`` 的 value 是目标 HP 值、
    /// 枚举该传什么、哨兵值是多少 —— 收在这里，Python 侧就不用知道。
    /// </summary>
    internal sealed class UnitOps
    {
        /// <summary>``unit.info``：读单位扁平状态，一次 crossing 拿全。</summary>
        public object Info(Dictionary<string, object> args) => OpsArgs.Info(OpsArgs.Unit(args));

        /// <summary>``unit.state``：info 的超集（多带 buff 数量与等级），同样一次 crossing。</summary>
        public object State(Dictionary<string, object> args) => OpsArgs.State(OpsArgs.Unit(args));

        /// <summary>``unit.buff_stacks``：按主关键字读层数（没有则 0）。</summary>
        public object BuffStacks(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var keyword = OpsArgs.BuffKeyword(args, "keyword");
            return (long)OpsArgs.SafeInt(() => unit.GetActivatedBuffStack(keyword, false));
        }

        /// <summary>``unit.buff_turns``：按主关键字读剩余回合。</summary>
        public object BuffTurns(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var keyword = OpsArgs.BuffKeyword(args, "keyword");
            return (long)OpsArgs.SafeInt(() => unit.GetActivatedBuffTurn(keyword, false));
        }

        /// <summary>``unit.buffs``：列出全部 Buff（扁平）。</summary>
        public object Buffs(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var rows = new List<object>();
            var list = unit.GetActivatedBuffModels();
            if (list != null)
            {
                for (var index = 0; index < list.Count; index++)
                {
                    var buff = list[index];
                    if (buff != null) rows.Add(OpsArgs.BuffInfo(buff));
                }
            }

            return rows.ToArray();
        }

        /// <summary>``unit.has_keyword``：是否带某个单位关键字。</summary>
        public object HasKeyword(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var keyword = OpsArgs.EnumValue(args, "keyword", default(UNIT_KEYWORD));
            return OpsArgs.SafeBool(() => unit.HasUnitKeyword(keyword));
        }

        /// <summary>
        /// ``unit.damage``：造成伤害。
        ///
        /// 内部把"扣 N 点"翻译成 ``ChangeHp(Hp - N, ...)`` —— 这是本 op 存在的最大理由：
        /// ``ChangeHp`` 的参数是**目标 HP 值**，写错方向就是把敌人治满血。
        /// </summary>
        public object Damage(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var amount = OpsArgs.Int(args, "amount", 0);
            if (amount <= 0 || OpsArgs.SafeBool(() => unit.IsDead()))
            {
                return OpsArgs.Info(unit, false);
            }

            var source = OpsArgs.EnumValue(args, "source", DAMAGE_SOURCE_TYPE.PASSIVE);
            var timing = OpsArgs.EnumValue(args, "timing", BATTLE_EVENT_TIMING.ON_SUCCESS_ATTACK);
            var targetHp = Math.Max(0, unit.Hp - amount);
            ChangeHp(unit, targetHp, source, timing);
            return OpsArgs.Info(unit);
        }

        /// <summary>``unit.heal``：治疗（同一根 HP 管线，不超上限）。</summary>
        public object Heal(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var amount = OpsArgs.Int(args, "amount", 0);
            if (amount <= 0 || OpsArgs.SafeBool(() => unit.IsDead()))
            {
                return OpsArgs.Info(unit, false);
            }

            var timing = OpsArgs.EnumValue(args, "timing", BATTLE_EVENT_TIMING.ON_SUCCESS_ATTACK);
            var targetHp = Math.Min(unit.MaxHp, unit.Hp + amount);
            ChangeHp(unit, targetHp, DAMAGE_SOURCE_TYPE.BUFF, timing);
            return OpsArgs.Info(unit);
        }

        /// <summary>``unit.set_hp``：直接设成目标 HP（走 OnChangeHp 通知）。</summary>
        public object SetHp(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var target = OpsArgs.Int(args, "hp", unit.Hp);
            var source = OpsArgs.EnumValue(args, "source", DAMAGE_SOURCE_TYPE.PASSIVE);
            var timing = OpsArgs.EnumValue(args, "timing", BATTLE_EVENT_TIMING.ON_SUCCESS_ATTACK);
            ChangeHp(unit, Math.Max(0, Math.Min(target, unit.MaxHp)), source, timing);
            return OpsArgs.Info(unit);
        }

        /// <summary>``unit.add_passive``：按 id 施加被动。</summary>
        public object AddPassive(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var passiveId = OpsArgs.Int(args, "passive_id", 0);
            if (passiveId <= 0) return false;
            unit.AddPassive(passiveId);
            return true;
        }

        /// <summary>
        /// ``unit.burst_by_buff``：按某关键字的叠层数打一笔伤害。
        ///
        /// **不消耗层数**（消耗要走 BuffDetail 的施加/移除管线，语义未验证）。复合 op 的价值在于
        /// "读层数 → 算伤害 → 改血"发生在同一个主线程时间片里，中间不会被帧边界插进来。
        /// </summary>
        public object BurstByBuff(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var keyword = OpsArgs.BuffKeyword(args, "keyword");
            var perStack = OpsArgs.Int(args, "per_stack", 3);
            var maxStacks = OpsArgs.Int(args, "max_stacks", 6);

            var stacks = OpsArgs.SafeInt(() => unit.GetActivatedBuffStack(keyword, false));
            var used = Math.Max(0, Math.Min(stacks, maxStacks));
            var hpBefore = unit.Hp;
            var result = Lpcp.Map(
                "stacks", (long)used,
                "damage", (long)(used * perStack),
                "hp_before", (long)hpBefore,
                "hp_after", (long)hpBefore,
                "applied", false,
                "keyword", OpsArgs.SafeEnumName(() => keyword.ToString()));

            if (used <= 0 || OpsArgs.SafeBool(() => unit.IsDead())) return result;

            var source = OpsArgs.EnumValue(args, "source", DAMAGE_SOURCE_TYPE.PASSIVE);
            var targetHp = Math.Max(0, hpBefore - used * perStack);
            ChangeHp(unit, targetHp, source, BATTLE_EVENT_TIMING.ON_SUCCESS_ATTACK);
            result["hp_after"] = (long)unit.Hp;
            result["applied"] = true;
            return result;
        }

        /// <summary>
        /// ``unit.add_buff``（实验性）：走 ``BuffDetail.AddBuff`` 施加 Buff。
        ///
        /// 真机时序（activeRound / history 列表）没有验证过，所以整段 try/catch：失败返回 false，
        /// 不把异常抛给 Python —— 实验性 op 不该让 mod 崩。
        /// </summary>
        public object AddBuff(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var keyword = OpsArgs.BuffKeyword(args, "keyword");
            var stacks = OpsArgs.Int(args, "stacks", 1);
            var turns = OpsArgs.Int(args, "turns", 1);
            var timing = OpsArgs.EnumValue(args, "timing", BATTLE_EVENT_TIMING.ON_SUCCESS_ATTACK);
            if (stacks <= 0 || turns <= 0 || OpsArgs.SafeBool(() => unit.IsDead())) return false;

            try
            {
                var detail = unit._buffDetail;
                if (detail == null) return false;
                // dump.cs 的参数表是「值参数」，但 Il2CppInterop 把后面 4 个 int 生成为 out ——
                // 以编译期的真实签名为准（A 层存在的意义之一就是把这种差异收进 C#）。
                int addedStack, addedTurn, overStack, overTurn;
                var applied = detail.AddBuff(unit, keyword, null, 0, ABILITY_SOURCE_TYPE.PASSIVE, timing,
                    null, out addedStack, out addedTurn, out overStack, out overTurn, true);
                return applied;
            }
            catch
            {
                return false;
            }
        }

        // -- 共用 ---------------------------------------------------------------------------

        /// <summary>
        /// ``unit.set_sp``：把理智直接设成某个值（走游戏自己的 ``ChangeMp``，会触发相关通知）。
        ///
        /// 签名取自 full0：``ChangeMp(int newMp) -> int``（返回实际生效后的值）。
        /// </summary>
        public object SetSp(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var target = OpsArgs.Int(args, "sp", unit.Mp);
            try
            {
                unit.ChangeMp(target);
            }
            catch
            {
                return OpsArgs.State(unit, false);
            }

            return OpsArgs.State(unit);
        }

        /// <summary>
        /// ``unit.add_shield``：给单位加护盾。
        ///
        /// 签名取自 full0：``AddShield(int value, bool isVolatile, ABILITY_SOURCE_TYPE source,
        /// BATTLE_EVENT_TIMING timing, SHIELD_MANAGABLE_TYPE = 0, AbilityBase = default)``，
        /// 后两个是可选参数，这里不传。
        /// </summary>
        public object AddShield(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var value = OpsArgs.Int(args, "value", 0);
            if (value <= 0) return OpsArgs.State(unit, false);

            var timing = OpsArgs.EnumValue(args, "timing", BATTLE_EVENT_TIMING.ON_SUCCESS_ATTACK);
            try
            {
                unit.AddShield(value, false, ABILITY_SOURCE_TYPE.PASSIVE, timing);
            }
            catch
            {
                return OpsArgs.State(unit, false);
            }

            return OpsArgs.State(unit);
        }

        /// <summary>
        /// ``unit.set_speed``：改速度并重算行动顺序。
        ///
        /// 签名取自 full0：``SetSpeed(int value, bool checkMinMax = false)`` +
        /// ``RefreshSpeed()``。改完不刷新的话，行动顺序要等下一回合才生效。
        /// </summary>
        public object SetSpeed(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var value = OpsArgs.Int(args, "value", 0);
            try
            {
                unit.SetSpeed(value, true);
                unit.RefreshSpeed();
            }
            catch
            {
                return OpsArgs.State(unit, false);
            }

            return OpsArgs.State(unit);
        }

        /// <summary>``unit.refresh_speed``：只重算速度与行动顺序（不改值）。</summary>
        public object RefreshSpeed(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            try
            {
                unit.RefreshSpeed();
            }
            catch
            {
                return OpsArgs.State(unit, false);
            }

            return OpsArgs.State(unit);
        }

        /// <summary>
        /// ``unit.break``：强制让单位进入崩溃（break）。
        ///
        /// 签名取自 full0：``Break(BattleUnitModel attackerOrNull, BATTLE_EVENT_TIMING timing,
        /// BattleActionModel actionOrnull, CoinModel coinOrNull, bool isPureBreak = true,
        /// int hpDmg = 0, DAMAGE_SOURCE_TYPE dmgSrcType = 10, bool isBreakForcely = false)``。
        /// 后四个是可选参数；``isBreakForcely`` 走参数名传入，避免依赖默认值的语义。
        /// </summary>
        public object Break(Dictionary<string, object> args)
        {
            var unit = OpsArgs.Unit(args);
            var timing = OpsArgs.EnumValue(args, "timing", BATTLE_EVENT_TIMING.ON_SUCCESS_ATTACK);
            try
            {
                unit.Break(null, timing, null, null);
            }
            catch
            {
                return OpsArgs.State(unit, false);
            }

            return OpsArgs.State(unit);
        }

        private static void ChangeHp(BattleUnitModel unit, int targetHp, DAMAGE_SOURCE_TYPE source,
            BATTLE_EVENT_TIMING timing)
        {
            // 参数顺序（dump.cs 验证）：value, dmgSrcType, timing, attacker, action, coin,
            //                      keyword, deactivePassedBreakSection, ignorePassedBreakSection
            unit.ChangeHp(targetHp, source, timing, null, null, null,
                BUFF_UNIQUE_KEYWORD.None, false, false);
        }
    }
}
