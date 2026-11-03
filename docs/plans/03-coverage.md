# 03 — A 层覆盖面扩展计划（时点 / 函数 / 属性）

> 状态：**计划文档**。本文不改任何源码，只定义"扩到哪、怎么扩、先扩什么"。
> 事实来源与版本：`il2csharp/out/limbus/cw4d/Assembly-CSharp/*.cs`（2026-11-02，**带方法体、最新**）、
> `Il2CppDumper-release/dump.cs`（2026-10-23）、`Script.json`（2026-10-29）、
> `ModularLimbis`（ModularSkillScripts v5.1.7）、`ModularCustomA`（MTCustomScripts v35.123.4）、
> `analysis/lethe-dll-decompiled/decomp/Lethe.Patches/`。
> **签名一律以上述 il2csharp 全量导出为准**（见 §9 风险 R1：三份产物不是同一版本）。
>
> 标注约定：行尾 `?` = **需要真机验证**（签名/可访问性/触发时机至少一项未核实），§9 末尾有汇总清单。

---

## 0. 一页速读

| | 现状 | 本计划目标（P0/P1/P2 全部落地后） |
|---|---|---|
| 时点（hooks） | **5** | **76**（观测 ~62 + 可干预 ~14） |
| 函数（ops） | **13** | **72** |
| 属性（getters） | 混在 `unit.info` 里（10 字段） | **10 个属性组 + 40 个单读**，高频字段一次往返拿 12–18 个 |
| 加一个时点的成本 | 改 `api.toml` + **手写 `HookTable.Shape()` 的 switch 分支（数签名第几个参数）+ 手写 `OpsEntry.Register`** | **只改 `api.toml`**（`arg = "参数名"` 声明）；`Shape()` 被生成表 + 通用 shaper 取代 |
| 加一个 op 的成本 | 改 `api.toml` + 手写 C# 方法 + 手写 `OpsEntry.Register` 一行 | 改 `api.toml` + 手写 C# 方法（`Register` 由生成器产出） |

**核心结论**：覆盖面的瓶颈不是"写不出 60 个 op"，而是 **A 层的扩展机制是手写的**（`HookTable.cs:123-179` 的位置下标 `switch` + `OpsEntry.cs:33-45` 的手写注册表）。
不先把它改成声明式，覆盖 60 个时点 = 至少 60 次"人肉数方法签名"，且每次改 C# 都要重编译重启。**P0 的第一件事是修这个瓶颈，不是加时点。**

### 与同目录另两份计划的分工（避免重叠）

| 计划 | 负责 | 与本文的接口 |
|---|---|---|
| `01-api-ergonomics.md` | 作者侧书写体验（调用形态、门面、类型） | 本文只定义"有哪些 op/hook/属性"，**怎么写得顺手归 01** |
| `02-registration-layer.md` | 内容注册（Buff/被动/人格/技能）+ 条件过滤 DSL + 位图（bitmask）判定 | 02 需要时点里**预置一个整型位图字段**把判定压到 0 次往返 → 该字段的占位就在本文 §5.4 的 payload 预算里；02 的「只有 5 个时点有发射方」与本文 §6 是同一个病灶的两种表述 |
| **本文（03）** | A 层的**覆盖面与扩展机制**：时点表 / op 表 / 属性组 / 生成式注册与 payload 装配 | 给 01、02 提供底座：没有声明式 `Shape()`，02 的注册层无处挂载 |

---

## 1. 两个参照生态的能力画像

### 1.1 规模与形态

| 生态 | 路径 | 能力原子数 | 对外 API 形态 |
|---|---|---|---|
| ModularLimbis (ModularSkillScripts v5.1.7) | `ModularLimbis/ModularSkillScripts/` | `Acquirer/` **111**、`Consequence/` **117**、`LuaFunction/` **19**、`Patches/` **9** 文件（内含 **164** 个 `[HarmonyPatch]`）、root 4 个 | 字符串指令 DSL：`TIMING:OnSucceedAttack(Head)/buff(MainTarget,SINKING,2,1)`；Lua 侧 `getbuffstack(unit, kw)` |
| ModularCustomA (MTCustomScripts v35.123.4) | `ModularCustomA/ModularCustomConsequences/` | `Acquirers/` **52**、`Consequences/` **77**、`LuaFunctions/` **20**、`Patches/` **31** 文件（**68** 个 `[HarmonyPatch]`） | **`MTCSLuaDef.lua`（1121 行）= 它对外暴露的全部 API 清单**：`Acquisitions and Consequences` 区 **111** 个全局函数 + `Exclusive .lua functions` 区 **21** 个 + **189 个 TimingID** |

`MTCSLuaDef.lua` 是覆盖面核对的**金标准**：它逐条给出了函数名、参数、返回语义。本计划的三张表都以它为对照基线扫过一遍（逐条归并见 §2/§3/§4 各表的备注列）。

### 1.2 三条必须继承的设计洞察

按"能不能被 A 层吸收"重新看这 200+ 个原子，真正决定覆盖面的是三件事，不是原子数量：

1. **目标选择器 DSL（最重要）**
   `ModularSA.GetTargetModel(string)` 支持 `Null / Self / SelfCore / Target / TargetCore / MainTarget / Victim / Killer / id<N> / inst<N> / adjLeft / adjRight / MOBJ<n>`；
   `GetTargetModelList(string)` 另支持 `All / Ally / Enemy / ExceptSelf / ExceptTarget / Random / Deploy / Reversedeploy / Slowest|Fastest / HighestHP|LowestHP / HighestHPRatio|LowestHPRatio / HighestMaxHP / HighestMP|LowestMP`（`ModularScripts.cs:960-1034`）。
   → **这是 Modular 生态生产力的真正来源**：作者写 `"LowestHPRatioEnemy"`，一次调用作用于一个集合。LimbusPy 的 `unit.info`/`unit.damage` 只能打单个单位，所以任何"对敌方全体"的写法在 A 层根本表达不出来，只能掉到 B 层循环 N 次。
   **结论：批量（multi-target）不是可选优化，是覆盖面的一部分。** 见 §6.3。

2. **权限分级：观测 / 改值**
   Modular 的 164 + 68 个补丁里，约 **1/3 不是 `On*` 观测点，而是 `Get*Adder` / `Get*Multiplier` / `Check*` 这类"返回值可改"的判定钩**（例：`SkillModel.GetCoinScaleAdder`、`BattleUnitModel.GetAttackWeightAdder`、`BattleUnitModel.CheckImmortal`、`BattleActionModel.CanDuel`）。
   → A 层现在的 5 个时点全是 `void` postfix（`HookTable.cs:257-271` 只有 `Postfix` 一个变体），**结构上不可能做"改伤害/改威力/免死/能不能拼点"**。这是覆盖面最大的结构性缺口，见 §7.2 P0-3。

3. **细粒度原子要"归并 + 提抽象"，不要 1:1 搬**
   例子（`Acquirer` × `Acquirers` 去重）：
   * `getbuffstack / getbuffturn / getbuffcount / hasbuffkeyword / getbuffhascount / listbuffs / getbufflocaledata` → **1 个属性组** `unit.buffs`（现在已有，但缺 `has_count`/`category`/`locale`）；
   * `getskilldata` 的 30 个 `DataType`（`Scale/Final/Clash/Weight/Level/AtkType/DefType/...`）→ **1 个属性组** `battle.skill_state`，不是 30 个 op；
   * `AcquirerHpCheck / MpCheck / SpeedCheck / AcquirerAbsolute / AcquirerMath` → 这些是**纯比较/算术**，留在 Python 侧（A 层只提供 `field, op, value` 的比较辅助，不新增跨进程 op）。

---

## 2. 表 1：时点（hooks / timings）

**列说明**：`参数` = payload 预置字段（作者拿到即用，不必再补读）；`往返成本(次)` = **触发本身 0 次**（帧末一次 `EVENTBATCH` 批量推，见 `HookTable.Tick()`），标 `1` 表示"常规用法还需要一次 op 补读"；`现状` = 已有（当前 5 个）/ 缺失。

| 名称 | 语义一句话 | 对应游戏方法或字段 | 参数（payload） | 返回 | 往返成本(次) | 优先级 | 现状 |
|---|---|---|---|---|---|---|---|
| **A. HP / 伤害** | | | | | | | |
| `DamageDealt` | 任意来源的 HP 变化 | `BattleUnitModel.OnChangeHp` | unit, attacker, old_hp, new_hp, source, timing, keyword | void | 0 | P0 | **已有** |
| `HpDamageTaken` | 单次扣血结算（含 break/护盾上下文） | `BattleUnitModel.OnTakeHpDamage` | unit, attacker, final_damage, hp_damage, source, keyword, timing | void | 0 | P0 | 缺失 |
| `Healed` | 治疗落地（试算量 vs 实际量） | `BattleUnitModel.OnAfterTryTakeHpHeal` | unit, healer, try_heal, result_heal, src_type, timing | void | 0 | P1 | 缺失 |
| `MpDamaged` | SP 被扣 | `BattleUnitModel.OnTakeMpDamage` | unit, attacker, value, source, timing | void | 0 | P0 | 缺失 |
| `MpChanged` | SP 变化（含回复） | `BattleUnitModel.OnChangeMp` | unit, old_mp, new_mp | void | 0 | P0 | 缺失 |
| `ZeroHp` | HP 归零（真正死亡前） | `BattleUnitModel.OnZeroHp` | unit | void | 1 | P1 | 缺失 |
| `DamageCalculated` | 最终伤害算出，**可改** | `BattleActionModel.ChangeAttackDamage` | unit, coin_index, target, damage, is_critical | int（改后伤害） | 0 | P0 | 缺失 |
| `BeforeGiveDamage` | 伤害投出前，**可改暴击标记** | `BattleActionModel.BeforeGiveAttackDamage` | unit, coin_index, target, is_critical, timing | void | 1 | P1 | 缺失 |
| **B. 攻击 / 硬币** | | | | | | | |
| `AttackSucceeded` | 一次攻击命中结算完成（带分解） | `BattleUnitModel.OnSucceedAttack` | unit, target, final_damage, real_damage, hp_damage, is_critical, timing | void | 0 | P0 | **已有** |
| `AttackConfirmed` | 命中确认（硬币级，目标已定） | `BattleUnitModel.OnAttackConfirmed` | unit, coin_index, target, skill_id, timing | void | 0 | P1 | 缺失 |
| `BeforeAttack` | 攻击行为前 | `BattleUnitModel.BeforeAttack` | unit, skill_id, timing | void | 0 | P1 | 缺失 |
| `AttackEnded` | 一次攻击收尾（累计伤害） | `BattleUnitModel.OnEndAttack` | unit, skill_id, accum_damage, accum_hp_damage, is_win_duel, timing | void | 0 | P1 | 缺失 |
| `CriticalActivated` | 暴击判定触发 | `BattleUnitModel.OnCriticalIsActivated` | unit, coin_index, timing | void | 0 | P1 | 缺失 |
| `CoinStarted` | 一枚硬币开始投掷 | `BattleUnitModel.OnStartCoin` | unit, coin_index, skill_id, timing | void | 0 | P0 | 缺失 |
| `CoinTossed` | 硬币结果落地吗（正/反面微值） | `BattleUnitModel.OnRollOneCoin_AfterAttack` | unit, coin_index, value, timing | void | 0 | P0 | 缺失 |
| `CoinEnded` | 硬币结算结束 | `BattleUnitModel.OnEndCoin_AfterLog` | unit, coin_index, skill_id, timing | void | 0 | P1 | 缺失 |
| `EvadeSucceeded` | 闪避成功 | `BattleUnitModel.OnSucceedEvade`（**private**） | unit, skill_id, timing | void | 0 | P2 | 缺失 ? |
| `EvadeFailed` | 闪避失败 | `BattleUnitModel.OnFailedEvade`（**private**） | unit, skill_id, timing | void | 0 | P2 | 缺失 ? |
| `AttackCanceled` | 攻击被能力取消 | `BattleUnitModel.OnAttackCanceledByAbility`（**private**） | unit, skill_id, timing | void | 0 | P2 | 缺失 ? |
| **C. 拼点 / 决斗** | | | | | | | |
| `ClashStarted` | 拼点开始 | `BattleUnitModel.OnStartDuel` | unit, oppo_unit, skill_id, oppo_skill_id, timing | void | 0 | P0 | 缺失 |
| `ClashWon` | 拼点胜（含剩余硬币） | `BattleUnitModel.OnWinDuel` | unit, oppo_unit, parrying_count, remain_self_coin, remain_oppo_coin, timing | void | 0 | P0 | 缺失 |
| `ClashLost` | 拼点负 | `BattleUnitModel.OnLoseDuel` | 同上 | void | 0 | P0 | 缺失 |
| `ParryWon` | 单次对拼胜 | `BattleUnitModel.OnWinParrying` | unit, oppo_unit, timing | void | 0 | P1 | 缺失 |
| `ParryLost` | 单次对拼负 | `BattleUnitModel.OnLoseParrying` | unit, oppo_unit, timing | void | 0 | P1 | 缺失 |
| `ParryResolved` | 单次对拼结算完（带结果枚举） | `BattleUnitModel.OnAfterParryingOnce_AfterLog` | unit, result, parrying_count, timing | void | 1 | P1 | 缺失 ? |
| `ParryPowerRolled` | 拼点威力加值，**可改** | `BattleActionModel.GetParryingResultAdder` | actor_result, oppo_result, parrying_count | int | 0 | P2 | 缺失 |
| `UnopposedAttack` | 无对峙攻击（对方没有可对拼技能） | `BattleActionModelManager.Run(BattleActionModel)` | unit, skill_id | void | 1 | P2 | 缺失 ? |
| **D. 技能 / 威力** | | | | | | | |
| `SkillUsed` | 技能行为开始 | `BattleUnitModel.OnStartBehaviour` | unit, skill_id, timing | void | 0 | P0 | 缺失 |
| `SkillEnded` | 技能行为结束 | `BattleUnitModel.OnEndBehaviour` | unit, skill_id, timing | void | 0 | P0 | 缺失 |
| `SkillBeforeBehaviour` | 行为开始前 | `BattleActionModel.BeforeBehaviour` | unit, skill_id, timing | void | 0 | P1 | 缺失 |
| `SkillPowerRolled` | 技能威力加值，**可改**（帕累最值钱的可干预点） | `BattleActionModel.GetSkillPowerResultAdder` | unit, skill_id, timing | int | 0 | P0 | 缺失 |
| `CoinPowerRolled` | 硬币威力加值，**可改** | `BattleActionModel.GetCoinScaleAdder` | unit, coin_index | int | 0 | P0 | 缺失 |
| `AttackWeightRolled` | 攻击权重加值，**可改** | `BattleUnitModel.GetAttackWeightAdder` | unit, skill_id | int | 0 | P1 | 缺失 |
| `SkillDiscarded` | 技能被丢弃 | `BattleActionModel.OnDiscarded` | unit, skill_id, timing | void | 0 | P2 | 缺失 |
| **E. 回合 / 战斗循环** | | | | | | | |
| `TurnStarted` | 单位回合开始（日志之后） | `BattleUnitModel.OnStartTurn_AfterLog` | unit, timing | void | 0 | P0 | **已有** |
| `TurnStartBeforeLog` | 单位回合开始（日志前） | `BattleUnitModel.OnStartTurn_BeforeLog` | unit, is_duel_expected, timing | void | 1 | P1 | 缺失 |
| `TurnEnded` | 单位回合结束 | `BattleUnitModel.OnEndTurn` | unit, timing | void | 0 | P0 | 缺失 |
| `RoundStarted` | 回合开始（全场一次性，真正的"回合钩"） | `BattleUnitModel.OnRoundStart_After_Event` | unit, round, timing | void | 1 | P0 | 缺失 |
| `RoundStartBefore` | 回合开始前（技能选择前） | `BattleUnitModel.OnRoundStart_Before` | unit | void | 1 | P1 | 缺失 |
| `RoundEnded` | 回合结束 | `BattleUnitModel.OnRoundEnd` | unit, timing | void | 0 | P0 | 缺失 |
| `BattleStarted` | 战斗开始 | `BattleUnitModel.OnBattleStart` | unit, timing | void | 0 | P0 | 缺失 |
| `BattleEnded` | 战斗结束 | `BattleUnitModel.OnBattleEnd` | unit, timing | void | 0 | P1 | 缺失 |
| `WaveStarted` | 波次开始 | `BattleUnitModel.OnWaveStart` | unit, timing | void | 0 | P1 | 缺失 |
| `WaveEnded` | 波次结束 | `BattleUnitModel.OnWaveEnd` | unit | void | 0 | P2 | 缺失 |
| `StageStarted` | 关卡（遭遇）开始 | `BattleUnitModel.OnStageStart` | unit, timing | void | 0 | P1 | 缺失 |
| `StageEnded` | 关卡结束（能否通关由 on_clear 给出） | `BattleUnitModel.OnStageEnd` | unit, on_clear | void | 0 | P2 | 缺失 |
| `CommandCompleted` | 玩家指令确认完（行动已锁定） | `BattleUnitModel.OnCompleteCommand` / `StageController.CompleteCommand` | round | void | 1 | P0 | 缺失 |
| `ActionsSorted` | 本回合行动顺序排定 | `BattleActionModelManager.SortActions` | round | void | 1 | P0 | 缺失 ? |
| **F. 死亡 / 崩溃 / 士气** | | | | | | | |
| `UnitDied` | 单位死亡 | `BattleUnitModel.OnDie` | unit, killer, source, timing | void | 0 | P0 | **已有** |
| `UnitBroken` | 进入崩溃（break） | `BattleUnitModel.OnBreak` | unit, attacker, is_forced, timing | void | 0 | P0 | **已有** |
| `BreakForced` | 被强制击溃 | `BattleUnitModel.OnBreakForcely` | unit, attacker, src_type, timing | void | 0 | P2 | 缺失 |
| `BreakRecovered` | 从崩溃恢复 | `BattleUnitModel.OnRecoverBreak`（protected） | unit, timing | void | 0 | P1 | 缺失 |
| `EnemyKilled` | 击杀者侧结算（触发 On Kill 效果的地方） | `BattleUnitModel.OnKillTarget` | unit（击杀者）, target, source, timing | void | 0 | P0 | 缺失 |
| `Panicked` | 恐慌 | `BattleUnitModel.OnPanic`（protected） | unit, timing | void | 0 | P1 | 缺失 |
| `LowMorale` | 低士气 | `BattleUnitModel.OnLowMorale`（protected） | unit, timing | void | 0 | P1 | 缺失 |
| `MentalCollapsed` | 精神崩溃（侵蚀/眩晕） | `BattleUnitModel.OnMentalCollapse` | unit | void | 1 | P2 | 缺失 |
| `UnitRetreated` | 撤退 / 退场 | `BattleUnitModel.OnRetreat` | unit, trigger_unit, keyword, timing | void | 0 | P1 | 缺失 |
| `UnitReturned` | 返场 | `BattleUnitModel.OnReturnToField` | unit, retreat_turn, timing | void | 0 | P2 | 缺失 |
| `ImmortalityActivated` | 不死效果发动 | `BattleUnitModel.OnActivateImmortality` | unit, attacker, immortal_activator, timing | void | 0 | P2 | 缺失 |
| `PartDestroyed` | 部位破坏（异形） | `BattleUnitModel.OnEnemyPartDestroyedByAttacker` / `PassiveDetail.OnPartDestroyed` | unit, part, timing | void | 1 | P1 | 缺失 ? |
| **G. Buff / 护盾 / 被动** | | | | | | | |
| `BuffGained` | 单位获得 Buff（层/回合/生效回合/来源） | `BattleUnitModel.RightAfterGetAnyBuff` | unit, keyword, stacks, turns, active_round, src_type, giver, over_stacks, over_turns, timing | void | 0 | P0 | 缺失 |
| `BuffLost` | Buff 被消耗/移除 | `BattleUnitModel.RightAfterLosingBuff` | unit, stacks, turns, keyword?, timing | void | 0 | P0 | 缺失 ? |
| `BuffUsed` | Buff 被"使用"（效果结算） | `BattleUnitModel.OnUseBuff` | unit, keyword, stacks, turns, timing | void | 0 | P1 | 缺失 |
| `BuffDestroyed` | Buff 被摧毁/过期 | `BuffDetail.RightAfterDestroyAnyBuff` | unit, keyword, stacks, turns, timing | void | 0 | P1 | 缺失 ? |
| `BuffGivenBySkill` | 技能给 Buff 之后（取改写后的最终值） | `BattleUnitModel.RightAfterGiveBuffBySkill` | unit, giver, target, keyword, original_stack, original_turn, result_stack, result_turn, skill_id, timing | void | 0 | P1 | 缺失 |
| `BuffMaxStackQueried` | 询问最大层数加成，**可改** | `BattleUnitModel.GetMaxBuffStackAdder` | unit, keyword | int | 0 | P2 | 缺失 |
| `ShieldAdded` | 护盾增加 | `BattleUnitModel.OnAddShield` | unit, value, timing | void | 0 | P1 | 缺失 |
| `ShieldReduced` | 护盾减少 | `BattleUnitModel.OnReduceShield` | unit, old, value, attacker, timing | void | 0 | P1 | 缺失 |
| `ShieldDestroyed` | 护盾破碎 | `BattleUnitModel.OnDestroyShield` | unit, attacker, timing | void | 0 | P2 | 缺失 |
| `EgoPassiveActivated` | EGO 被动激活 | `BattleUnitModel.OnActivateEgoPassive` | unit, passive_id | void | 0 | P2 | 缺失 |
| `VibrationExploded` | 振动引爆 | `BattleUnitModel.OnVibrationExplosion` | unit, giver, src_type, timing | void | 0 | P2 | 缺失 |
| `SinBuffActivated` | 罪孽 Buff 效果发动 | `BattleUnitModel.OnActivateSinBuffEffect` | unit, keyword, stacks, turns, take_damage, timing | void | 0 | P1 | 缺失 |
| **H. 操作 / 阶段** | | | | | | | |
| `EquipDefense` | 装上防御技能 | `NewOperationController.EquipDefense` | unit, skill_id | void | 1 | P1 | 缺失 |
| `WaitCommand` | 指令阶段等待（可在此期做任意准备） | `StageController.FixedUpdate` / `WaitCommand_UI` | round | void | 1 | P2 | 缺失 ? |
| `InfoOpened` | 单位信息面板被打开 | `BattleUnitUIManager.OpenInformation` | unit | void | 1 | P2 | 缺失 ? |
| `SpeedRefreshed` | 速度重算完成 | `BattleUnitModel.RefreshSpeed` | unit | void | 0 | P1 | 缺失 ? |

**表 1 条目数：76 行**（已有 5 / 缺失 71；P0 27 / P1 31 / P2 18；其中 `?` 待真机验证 **12** 行）。参考实盘：ModularLimbis 已挂 164 个补丁点、ModularCustomA 68 个、Lethe 在战斗链上占 `BattleUnitView.StartBehaviourAction`、`PassiveDetail.OnPartBreaked`、`BattleObjectManager.OnRoundStart_Model`、`StageController.EndStage`、`StageModel.Init`——表 1 的 P0 项全部落在这些已被验证可挂的类型/方法附近。

---

## 3. 表 2：函数（ops / actions）

**列说明**：`往返成本(次)` 对 op 恒为 **1**（一次 `OPCALL` request/response）；标 `1(批)` 表示该 op 自带 multi-target 选择器，一次调用作用于 N 个目标（等价于把 N 次往返压成 1 次，见 §6.3）。

| 名称 | 语义一句话 | 对应游戏方法或字段 | 参数 | 返回 | 往返成本(次) | 优先级 | 现状 |
|---|---|---|---|---|---|---|---|
| **A. HP / SP / 等级** | | | | | | | |
| `unit.damage` | 造成伤害（内部算目标 HP） | `BattleUnitModel.ChangeHp` | unit, amount, source, timing | UnitInfo | 1 | P0 | **已有** |
| `battle.damage` | 对一组单位造成伤害 | `ChangeHp` × N | selector, amount, source | List[UnitInfo] | 1(批) | P0 | 缺失 |
| `unit.damage_ext` | 带罪孽/攻击属性、无视防御、强制暴击的伤害 | `TakeAbsHpDamage` / `ChangeHp`（`ignoreShield`/`ignorePassedBreakSection` 参数） | unit, amount, sin, atk_type, ignore_shield, force_crit | UnitInfo | 1 | P0 | 缺失 ? |
| `unit.heal` | 治疗（同一根 HP 管线） | `ChangeHp` / `TryRecoverMp` 之外走 `OnAfterTryTakeHpHeal` 语义 | unit, amount, timing | UnitInfo | 1 | P0 | **已有** |
| `unit.set_hp` | 把 HP 设为某值 | `BattleUnitModel.ChangeHp` | unit, hp, source, timing | UnitInfo | 1 | P0 | **已有** |
| `unit.set_max_hp` | 改最大 HP（会触发 break 段重算） | `BattleUnitModel` 的 `MaxHp`/`RecalculateBreakSection(int)` | unit, max_hp | UnitInfo | 1 | P1 | 缺失 |
| `unit.set_sp` | 把 SP 设为某值 | `BattleUnitModel.ChangeMp(int)` | unit, sp | UnitInfo | 1 | P0 | 缺失 |
| `unit.heal_sp` | 回复 SP（带来源类型） | `BattleUnitModel.TryRecoverMp` | unit, amount, src_type | UnitInfo | 1 | P0 | 缺失 |
| `unit.set_level` | 改单位等级 | `BattleUnitModel.Level` / `GetLevel()` | unit, level | UnitInfo | 1 | P1 | 缺失 |
| **B. 护盾 / 防御** | | | | | | | |
| `unit.get_shield` | 读当前护盾 | `BattleUnitModel.GetShield()` | unit | int | 1 | P1 | 缺失 |
| `unit.add_shield` | 加护盾 | `BattleUnitModel.AddShield(int, bool, ABILITY_SOURCE_TYPE, BATTLE_EVENT_TIMING, ...)` | unit, value, volatile, source | UnitInfo | 1 | P0 | 缺失 |
| `unit.clear_shield` | 清空护盾 | `BattleUnitModel.ResetShield(timing, force)` | unit | UnitInfo | 1 | P2 | 缺失 |
| `unit.set_defense` | 覆盖防御值 | `BattleUnitModel.GetDefense(...)` 的 adder 链 / `_overridedDef` | unit, value | UnitInfo | 1 | P1 | 缺失 ? |
| **C. 速度 / 行动** | | | | | | | |
| `unit.set_speed` | 设速度（可夹到 min/max） | `BattleUnitModel.SetSpeed(int, bool checkMinMax)` | unit, value, check_min_max | UnitInfo | 1 | P0 | 缺失 |
| `unit.set_speed_limit` | 设速度上下限 | `OverwriteMinSpeed` / `OverwriteMaxSpeed` | unit, limit_type, value | bool | 1 | P1 | 缺失 ? |
| `unit.refresh_speed` | 重算速度（修好 MaxSpeedAdder 后的固定值） | `BattleUnitModel.RefreshSpeed()` | unit | UnitInfo | 1 | P1 | 缺失 |
| `battle.remove_all_actions` | 清空本回合剩余行动 | `BattleActionModelManager` 行动列表 / `StageController` | — | int（清掉几条） | 1 | P1 | 缺失 ? |
| `battle.set_action_index` | 把当前行动插到队列指定位置 | `BattleActionModelManager` 队列重排 | new_index, priority_group | bool | 1 | P2 | 缺失 ? |
| **D. Buff / 关键词** | | | | | | | |
| `unit.add_buff` | 施加 Buff | `BuffDetail.AddBuff(...)` | unit, keyword, stacks, turns, timing | bool | 1 | P0 | **已有**（experimental） |
| `unit.remove_buff` | 移除某 Buff（全部或按层） | `BuffDetail.DestroyBuff` / `LoseBuffAllStack` | unit, keyword, all | bool | 1 | P0 | 缺失 |
| `unit.add_buff_stack` | 改层数（正负均可） | `BuffDetail` 的 stack 管线 | unit, keyword, delta | BuffInfo | 1 | P0 | 缺失 |
| `unit.set_buff_stack` | 设层数 | 同上 | unit, keyword, value | BuffInfo | 1 | P0 | 缺失 |
| `unit.set_buff_turn` | 设剩余回合 | `BuffDetail` 的 turn 管线 | unit, keyword, value | BuffInfo | 1 | P0 | 缺失 |
| `unit.buff_edit_max` | 改某 Buff 的最大层/回合 | `BuffDetail.OverwriteMaxBuffStack` / `OverwriteMaxBuffTurn` | unit, keyword, prop, mode, value | bool | 1 | P1 | 缺失 ? |
| `unit.destroy_buffs_by_type` | 按类型/分类随机摧毁 N 个 Buff | `BuffDetail.DestroyRandomNegativeBuff` / `DestroyBuff(unit, BUF_TYPE, timing)` | unit, mode, destroy_round, amount | int（摧毁数） | 1 | P1 | 缺失 |
| `unit.burst_by_buff` | 按层数换一笔伤害（复合 op 样板） | `GetActivatedBuffStack` + `ChangeHp` | unit, keyword, per_stack, max_stacks, source | BurstInfo | 1 | P0 | **已有** |
| `unit.add_keyword` | 中途给单位加 unit keyword / association | `UNIT_KEYWORD` 集合（`HasUnitKeyword` 的反向） | unit, keyword, is_association | bool | 1 | P2 | 缺失 ? |
| `unit.remove_keyword` | 移除 keyword | 同上 | unit, keyword | bool | 1 | P2 | 缺失 ? |
| **E. 被动 / 技能 / 硬币** | | | | | | | |
| `unit.add_passive` | 按 id 施加被动 | `BattleUnitModel.AddPassive(int)` | unit, passive_id | bool | 1 | P0 | **已有** |
| `unit.remove_passive` | 移除被动 | `PassiveDetail` 被动列表（`GetAllActivatedPassives` 反向） | unit, passive_id | bool | 1 | P1 | 缺失 ? |
| `unit.add_skill` | 加技能到技能池 | `BattleUnitModel.GetSkillList` / `SkillStaticData` 池注入 | unit, skill_id, level, uptie, amount | bool | 1 | P1 | 缺失 ? |
| `unit.remove_skill` | 永久移除技能 | 同上 | unit, skill_id | bool | 1 | P2 | 缺失 ? |
| `unit.replace_dashboard_skill` | 替换仪表盘某格技能 | `SinActionModel` 的 slot（`GetSinAction(int)`） | unit, column, top_or_bottom, skill_id | bool | 1 | P1 | 缺失 ? |
| `unit.clear_temp_skill_abilities` | 清掉临时技能 ability | `SkillModel` ability 列表 | unit | bool | 1 | P2 | 缺失 ? |
| `unit.set_skill_sp_usage` | 设技能 SP 消耗（EGO 式） | `BattleActionModel.GetMpUsage(bool)` 的覆盖 | unit, skill_id, value, add | bool | 1 | P2 | 缺失 ? |
| `unit.activate_ego_passive` | 激活已装备 EGO 的被动 | `EgoPassiveModel` / `OnActivateEgoPassive` | unit, ego_id | bool | 1 | P2 | 缺失 ? |
| `unit.remove_coin` | 移除指定硬币 | `SkillModel.CoinList` | unit, skill_id, coin_index | bool | 1 | P2 | 缺失 ? |
| `unit.add_coin` | 造一枚硬币（按威力/算子/颜色） | `SkillModel` coin 构造 | unit, skill_id, coin_index, power, operator, color | bool | 1 | P2 | 缺失 ? |
| **F. 崩溃 / 死亡 / 精神** | | | | | | | |
| `unit.break` | 立即击溃（自然/强制） | `BattleUnitModel.Break(...)` / `BreakForcely(...)` | unit, stagger_type, attacker | UnitInfo | 1 | P0 | 缺失 |
| `unit.break_recover` | 从崩溃恢复 | `BattleUnitModel.OnRecoverBreak` 的调用方管线 | unit | UnitInfo | 1 | P1 | 缺失 ? |
| `unit.deactivate_break_bar` | 关掉 1 条或全部 break 条 | `BreakSection` 列表（`GetBreakSections()`） | unit, break_index, sort, reverse | int（关掉几条） | 1 | P1 | 缺失 ? |
| `unit.break_add_bar` | 加一条 break 条 | `RecalculateBreakSection` / BreakSection 构造 | unit, value | UnitInfo | 1 | P2 | 缺失 ? |
| `unit.instant_kill` | 立即杀死（可无视不死） | `BattleUnitModel.OnDie` 前置管线 / `CheckImmortal` | unit, ignore_immortal, source, killer | bool | 1 | P1 | 缺失 |
| `unit.set_immortal` | 设不死（层/回合） | `BattleUnitModel.CheckImmortal` / `OnActivateImmortality` | unit, turns, value | bool | 1 | P2 | 缺失 |
| `unit.set_panic_level` | 设恐慌/低士气/侵蚀等级 | `BattleUnitModel.OnPanicOrLowMorale(PANIC_LEVEL, timing)` 的调用方 | unit, level, variable | bool | 1 | P1 | 缺失 |
| `unit.set_panic_type` | 设恐慌类型 | `BuffModel.GetOverwritePanicType` | unit, panic_type, propriety | bool | 1 | P2 | 缺失 ? |
| **G. 抗性 / 资源** | | | | | | | |
| `unit.set_sin_resist` | 覆盖罪孽抗性 | `BattleUnitModel.GetAttributeResistValueByType(ATTRIBUTE_TYPE)` 覆盖 | selector, sin, value, add | int | 1(批) | P1 | 缺失 |
| `unit.set_atk_resist` | 覆盖攻击抗性 | `BattleUnitModel` 的 `GetAtkResist*` 覆盖 | selector, atk_type, value, add | int | 1(批) | P1 | 缺失 |
| `battle.add_sin_resource` | 增减罪孽资源 | `SinManager` / 罪孽资源表 | sin, delta | int（新值） | 1 | P0 | 缺失 |
| `battle.set_sin_resource` | 设罪孽资源 | 同上 | sin, value | int | 1 | P1 | 缺失 |
| `battle.sin_resonance` | 读共鸣（绝对值 + 最高共鸣） | `BattleActionModel.GetPerfectResonanceStack()` 等 | — | dict | 1 | P1 | 缺失 |
| **H. 战场 / 指令** | | | | | | | |
| `battle.units` | 全场单位扁平快照 | `BattleObjectManager._allUnitDictionary` | — | List[UnitInfo] | 1 | P0 | **已有** |
| `battle.round` | 当前回合数 | `StageController.GetCurrentRound()` | — | Optional[int] | 1 | P0 | **已有** |
| `battle.wave` | 当前波次 | `StageController.GetCurrentWave()` / `GetCurrentWaveIndex()` | — | int | 1 | P1 | 缺失 |
| `battle.action_order` | 本回合行动顺序（含速度/优先级） | `BattleActionModelManager` 排序结果 | — | List[ActionInfo] | 1 | P0 | 缺失 |
| `battle.slot_info` | 行动槽 / 参与顺序 | `SinActionModel` + `PARTICIPATE_ORDER` | — | List[SlotInfo] | 1 | P1 | 缺失 ? |
| `battle.skill_send` | 立刻发出一次攻击 | `BattleActionModel` 构造 + `BattleActionModelManager` 入队 | attacker, targets, skill, action_index | bool | 1 | P1 | 缺失 ? |
| `battle.add_duel` | 立刻让两个单位拼点 | `BattleDuelModel` / `CanDuel` 前置 | unit_a, unit_b, skill_a, skill_b | bool | 1 | P2 | 缺失 ? |
| **I. 生成 / 退场** | | | | | | | |
| `battle.summon` | 召唤友方单位（助理等） | `BattleUnitModelFactory` / `StageController.CreateSupportUnits` | unit_id, position | UnitInfo | 1 | P2 | 缺失 ? |
| `battle.spawn_enemy` | 生成敌方单位 | `BattleUnitModelFactory` / `StageController.CreateEnemyWave` | unit_id, wave | UnitInfo | 1 | P2 | 缺失 ? |
| `unit.retreat` | 让单位撤退 | `BattleUnitModel.OnRetreat` 的调用方管线 | unit, keyword | bool | 1 | P1 | 缺失 ? |
| **J. 随机（抽卡式）** | | | | | | | |
| `rng.int` | 取随机整数（内核侧 RNG，可复现种子） | 内核实现（不进游戏） | low, high | int | 1 | P0 | 缺失 |
| `rng.choice` | 从列表里随机取一个 | 内核实现 | items | Any | 1 | P0 | 缺失 |
| `rng.heads_chance` | 按概率判定（硬币式） | 内核实现 | chance | bool | 1 | P1 | 缺失 |
| **K. 表现层** | | | | | | | |
| `fx.log` | 战斗中写一条战斗日志 | `BattleLogger` / `StageController.CreateHpHealLogBySystem` 一类系统日志 | text, timing | bool | 1 | P1 | 缺失 ? |
| `fx.upper_text` | 屏幕上方飘字 | `BattleUIRoot` / UpperText 类 | text, unit | bool | 1 | P2 | 缺失 ? |
| `fx.popup` | 弹窗提示 | `BattlePopupController` | text, duration | bool | 1 | P2 | 缺失 ? |
| `fx.play_sound` | 播放音效 | `BattleSoundEventEmitter` / `BattleSoundGenerator` | clip_name, volume | bool | 1 | P2 | 缺失 ? |
| `fx.lyrics` | 战斗歌词（演出） | `BattleLyricsContoller` | line | bool | 1 | P2 | 缺失 ? |
| **L. 结算** | | | | | | | |
| `battle.end_win` | 立即通关当前关卡 | `StageController.EndStageForcely_NormalEnd()` | — | bool | 1 | P1 | 缺失 |
| `battle.end_lose` | 立即判负 | `StageController.EndStageForcely()` | — | bool | 1 | P2 | 缺失 |
| `battle.retry_stage` | 重开当前关卡 | `StageController.RetryStage()` | — | bool | 1 | P2 | 缺失 |

**表 2 条目数：72 行**（已有 8 / 缺失 64；P0 23 / P1 27 / P2 22；`?` 待真机验证 **32** 行）。其中 **13 行**必须 C# 侧特殊处理（复合时间片、out 参数、枚举哨兵、私有成员）：`unit.damage`、`unit.damage_ext`、`unit.burst_by_buff`、`unit.add_buff`、`unit.add_shield`、`unit.break`、`unit.instant_kill`、`unit.deactivate_break_bar`、`unit.buff_edit_max`、`unit.replace_dashboard_skill`、`battle.action_order`、`battle.skill_send`——都已在"对应游戏方法"列标出。

---

## 4. 表 3：属性（getters）

**列说明**：`批量组` = 能否作为一个属性组一次往返拿多个字段（组名在括号里）；`往返成本(次)` = 单读时 1；进组后摊薄为 `1/N`（N = 组内字段数）。

### 4.1 属性组（先做这 10 个，它们承担 80% 的读）

| 名称 | 语义一句话 | 对应游戏方法或字段 | 参数 | 返回 | 往返成本(次) | 优先级 | 现状 |
|---|---|---|---|---|---|---|---|
| `unit.state` | **单位状态组**（16 字段）：hp/max_hp/hp_ratio/sp/max_sp/level/speed/shield/break_level/panic_level/position/participate_order/unit_id/attribute/is_actionable/is_retreated | `Hp`,`MaxHp`,`Mp`,`Level`,`GetIntegerOfOriginSpeed`,`GetShield`,`GetCurrentBrokenLevel`,`IsPanic`/`IsLowMorale`,`Position`,`_participateOrder`,`GetOriginUnitID`,`GetPrimeAttribute`,`IsActionable`,`IsRetreated` | unit | UnitState | 1（摊薄 1/16） | P0 | 缺失（`unit.info` 只覆盖 10 字段） |
| `unit.buffs` | 全部 Buff：keyword/stacks/turns/max_stacks/max_turns/name/has_count/category | `GetActivatedBuffModels()` → `GetMainKeyword/GetCurrentStack/GetCurrentTurn/GetMaxStack/GetMaxTurn/GetName` | unit | List[BuffInfo] | 1（N 个 Buff 摊薄） | P0 | **已有**（缺 has_count/category） |
| `unit.keywords` | 单位带的所有 unit keyword + association | `HasUnitKeyword` 的枚举全集反查 | unit | List[str] | 1 | P1 | 缺失 |
| `unit.passives` | 全部被动（常规 + EGO） | `PassiveDetail.GetAllActivatedNormalPassives()` / `GetAllActivatedEgoPassive()` | unit | PassivesInfo | 1（摊薄 1/3） | P1 | 缺失 |
| `unit.skills` | 技能池：id/level/uptie/次数 | `BattleUnitModel.GetSkillList()` → `SkillModel` 字段 | unit | List[SkillInfo] | 1 | P1 | 缺失 |
| `battle.skill_state` | **当前行动技能组**：skill_id/slot_index/base_power/power/level/attribute/atk_type/def_type/attack_weight/coin_count/coin_heads/coin_tails | `BattleActionModel.GetSkillID/GetAttackWeight/GetSkillPower/GetCoinScale/GetFirstCoin/GetHeadCoinNum/GetTailCoinNum/IsAllCoinHead` + `SkillModel` | unit | SkillState | 1（摊薄 1/11） | P0 | 缺失 |
| `unit.resistances` | 罪孽抗性 ×7 + 攻击抗性 ×3（×100 整数） | `GetAttributeResistValueByType(ATTRIBUTE_TYPE)`、攻击侧 `GetAtkResist*` | unit | ResistInfo | 1（摊薄 1/10） | P1 | 缺失 |
| `unit.break_info` | break 条明细：sections 值 / 激活位 / 当前等级 | `GetBreakSections()` / `GetBreakSectionValueList(bool)` / `GetCurrentBrokenLevel()` | unit, active_only | BreakInfo | 1（摊薄 1/3） | P1 | 缺失 |
| `battle.state` | **战斗状态组**（12 字段）：round/wave/wave_index/max_round/is_first_round/is_first_round_on_wave/is_abnormality_battle/is_end_wave/player_units/enemy_units/ready_player/ready_enemy | `StageController.GetCurrentRound/GetCurrentWave/GetCurrentWaveIndex/GetMaxRound/IsFirstRound/IsFirstRoundOnWave/IsAbnormalityBattle/IsEndWave/GetReadyUnitCount(UNIT_FACTION)` | — | BattleState | 1（摊薄 1/12） | P0 | 缺失 |
| `battle.resources` | 罪孽资源 ×7 + 共鸣 + EGO 资源 | `SinManager` / `UnitSinModel` 资源表 | — | ResourceInfo | 1（摊薄 1/9） | P0 | 缺失 |

### 4.2 单读字段（高频、但单读更自然；进组后应优先用组）

| 名称 | 语义一句话 | 对应游戏方法或字段 | 参数 | 返回 | 往返成本(次) | 优先级 | 现状 |
|---|---|---|---|---|---|---|---|
| `hp` / `max_hp` / `sp` | 三根基础数值 | `CombatUnitModel.Hp` / `BattleUnitModel.MaxHp` / `.Mp` | unit | int | 1（组：unit.state） | P0 | 已有（在 UnitInfo 内） |
| `hp_ratio` | HP 比例（排序/阈值最常用） | `CombatUnitModel.GetHpRatio()` | unit | float | 1（组：unit.state） | P0 | 已有（派生属性） |
| `level` | 等级 | `BattleUnitModel.Level`（ObscuredInt）/ `GetLevel()` | unit | int | 1（组：unit.state） | P1 | 缺失 |
| `speed` | 当前/原始速度 | `GetIntegerOfOriginSpeed()` / `GetIntegerOfOverwritedSpeed()` | unit | int | 1（组：unit.state） | P0 | 缺失 |
| `shield` | 当前护盾 | `BattleUnitModel.GetShield()` | unit | int | 1（组：unit.state） | P1 | 缺失 |
| `break_level` | 崩溃等级 0/1/2/3 | `BattleUnitModel.GetCurrentBrokenLevel()` | unit | int | 1（组：unit.state） | P0 | 已有 `is_break`（只有 bool） |
| `panic_level` | 恐慌等级（-1 侵蚀 / 0 / 1 低士气 / 2 恐慌） | `IsPanic()` / `IsLowMorale()` / `IsErodedThisTurn()` | unit | int | 1（组：unit.state） | P0 | 缺失 |
| `is_actionable` | 能否行动（崩溃/眩晕/不可控） | `BattleUnitModel.IsActionable()` | unit | bool | 1（组：unit.state） | P0 | 缺失 |
| `is_retreated` | 是否已退场（≠ 死亡） | `IsRetreated()` / `IsOnBattleField()` | unit | bool | 1（组：unit.state） | P1 | 缺失 |
| `position` | 站位 | `BattleUnitModel.Position`（UNIT_POSITION） | unit | str | 1（组：unit.state） | P1 | 已有 |
| `participate_order` | 参与顺序（"Deploy" 排序用） | `_participateOrder` / `PARTICIPATE_ORDER` | unit | int | 1（组：unit.state） | P2 | 缺失 ? |
| `unit_id` | 单位静态 id | `GetOriginUnitID()` | unit | int | 1（组：unit.state） | P0 | 已有（`origin_id`） |
| `character_id` | 人格/角色 id（1=Yi Sang … 12=Gregor） | `OriginID` → `UnitStaticData` 映射（`IsSinnerFielded` 同源） | unit | int | 1（组：unit.state） | P1 | 缺失 ? |
| `attribute` | 主罪孽属性 | `BattleUnitModel.GetPrimeAttribute(SinActionModel)` | unit | str | 1（组：unit.state） | P1 | 缺失 ? |
| `appearance_id` | 外观 id（皮肤/立绘相关） | 单位外观数据（Modular `getappearanceid` 同源） | unit | str | 1 | P2 | 缺失 ? |
| `attack_level` | 攻击等级（Offense Level） | 技能侧 `SkillModel.GetSkillLevelCorrection()` + 单位侧 adder | unit, skill_id | int | 1（组：battle.skill_state） | P1 | 缺失 |
| `defense_level` | 防御等级 | `BattleUnitModel.GetDefense(ref,int,ref,int)` 一族 | unit | int | 1 | P1 | 缺失 ? |
| `attack` / `defense` | 攻/防基础值 | `GetAttackAdder(skill, action)` / `GetDefenseAdder()` | unit | int | 1 | P2 | 缺失 ? |
| `buff_stacks` / `buff_turns` | 按主关键字读层数/回合 | `GetActivatedBuffStack/Turn(keyword, bool)` | unit, keyword | int | 1（组：unit.buffs） | P0 | **已有** |
| `buff_exists` | 是否有某 Buff（未激活/已激活） | `HasBuff` / `HasActivatedBuff` / `HasReadyBuff` | unit, keyword | bool | 1（组：unit.buffs） | P0 | 缺失 |
| `buff_has_count` | 该 Buff 是否有 count（有 count 的才能扣回合） | `BuffInfo` 的 max_turn / `HasActivatedBuff` 组合 | unit, keyword | bool | 1（组：unit.buffs） | P1 | 缺失 |
| `buff_by_category` | 按分类聚合层数/回合 | `GetActivatedBuffStack(BUFF_CATEGORY_KEYWORD, exceptions)` | unit, category | int | 1（组：unit.buffs） | P1 | 缺失 |
| `has_passive` | 是否带某被动 | `PassiveDetail.HasPassive(int)` | unit, passive_id | bool | 1（组：unit.passives） | P1 | 缺失 |
| `current_skill_id` | 单位当前行动用的技能 id | `BattleActionModel.GetSkillID()` | unit | int | 1（组：battle.skill_state） | P0 | 缺失 |
| `current_skill_slot` | 技能在第几槽（0 = 最左） | `SinActionModel` slot index | unit | int | 1（组：battle.skill_state） | P1 | 缺失 ? |
| `skill_power` | 技能当前威力（含加值） | `GetSkillPowerResultAdder` / `GetFinalSkillPowerResult(timing)` | unit, skill_id | int | 1（组：battle.skill_state） | P0 | 缺失 |
| `skill_base_power` | 技能基础威力 | `SkillModel.GetSkillDefaultPower()` | unit, skill_id | int | 1（组：battle.skill_state） | P1 | 缺失 |
| `skill_level` | 技能等级 | `BattleUnitModel.GetSkillLevel(skill, null, out, out)` | unit, skill_id | int | 1（组：battle.skill_state） | P1 | 缺失 |
| `skill_attribute` / `skill_atk_type` / `skill_def_type` | 技能的罪孽属性 / 攻击类型 / 防御类型 | `SkillModel.GetAttribute` / `GetAttackType()` / `GetDefenseType()` | unit, skill_id | str | 1（组：battle.skill_state） | P0 | 缺失 |
| `attack_weight` | 攻击权重（原值 + 当前值） | `GetAttackWeight(action)` / `GetOriginAttackWeight()` | unit, skill_id | int | 1（组：battle.skill_state） | P1 | 缺失 |
| `coin_count` | 当前硬币数 | `SkillModel.CoinList.Count` / `GetCoin(i)` | unit, skill_id | int | 1（组：battle.skill_state） | P0 | 缺失 |
| `coin_state` | 每枚硬币的正/反面与是否已投 | `CoinModel` 状态 + `IsAllCoinHead` / `GetHeadCoinNum` / `GetTailCoinNum` | unit, skill_id | List[bool] | 1（组：battle.skill_state） | P1 | 缺失 |
| `round` | 回合数 | `StageController.GetCurrentRound()` | — | int | 1（组：battle.state） | P0 | **已有**（`battle.round`） |
| `wave` / `wave_index` | 波次 | `GetCurrentWave()` / `GetCurrentWaveIndex()` | — | int | 1（组：battle.state） | P1 | 缺失 |
| `max_round` | 回合上限 | `StageController.GetMaxRound()` | — | int | 1（组：battle.state） | P2 | 缺失 |
| `action_order` | 行动顺序（含实例 id / 技能 / 速度） | `BattleActionModelManager` 排序结果 | — | List | 1（组：battle.state） | P0 | 缺失 |
| `remaining_actions` | 本回合剩余行动数 | 单位 `_actionList` / 已做行动列表 | unit | int | 1（组：battle.state） | P2 | 缺失 ? |
| `sin_resource` | 罪孽资源（7 种） | 罪孽资源表 | — | dict | 1（组：battle.resources） | P0 | 缺失 |
| `sin_resonance` | 共鸣（是否有 + 最高共鸣） | `GetPerfectResonanceStack()` 一族 | — | dict | 1（组：battle.resources） | P1 | 缺失 |
| `ego_resource` | EGO 资源 | `BattleEgoModel.GetResourceNeedArray` | — | dict | 1（组：battle.resources） | P2 | 缺失 ? |

**表 3 条目数：50 行**（10 个组 + 40 个单读；已有 8 / 缺失 42；P0 21 / P1 23 / P2 6；`?` 待真机验证 **9** 行）。
> 注：13 个现有 op 里另外 5 个（`unit.info` `unit.buff_stacks` `unit.buff_turns` `unit.buffs` `unit.has_keyword`）在语义上属于本表，其中 `unit.info` 由 `unit.state` 取代（超集、只增不改）。

---

## 5. 成本模型与批量策略

### 5.1 「1 次往返」的真实定义

* 载体：命名管道上的一次 LPCP `request` → `response`（`Bridge.request`）。Python 侧**阻塞**，内核侧对 `CALL` 会切主线程执行后回包。
* **A 层单次 op = 1 次往返 + 0 次反射**（`Session.op` → `OPCALL`）。
* **B 层单次读/写/调用 = 1 次往返 + 1 次反射**（`Session.read/write/invoke` → `reflect.get/set/invoke`）。
* **B 层批量 = 1 次往返 + N 次反射**（`Session.read_many` → `BATCH`，内核广告 `batch.v1` 时生效；否则退化成 N 次）。
* 现有实测对照（`docs/05-typed-api.md` §2）：A 层 `unit.info` 1 次拿 6 字段；B 层读同一组字段 4 次。
* **一个 hook 触发本身不是往返**：补丁回调只入队，帧末 `HookTable.Tick()` → `host.EmitEvent` 一次 `EVENTBATCH`。所以"payload 大"只是**字节成本**，"payload 小"才是**往返成本**。

### 5.2 三种成本的相对量级（用于取舍，不需要绝对值）

| 成本项 | 量级关系 | 结论 |
|---|---|---|
| 一次往返（跨进程 + 主线程切换） | **1×**（最贵） | 能用 payload/组省掉的，一律省 |
| payload 每多 1 个 int 字段 | ≪ 0.01×（同一帧批量里的一行字节） | 时点 payload 宁可多预置几个字段 |
| 一次反射（成员名查找 + 重载打分 + 封送） | 约 0.2–0.5× | A 层 op 的价值一半来自"去掉反射" |

**取舍规则（写进评审标准）**：一个字段只要**在 ≥3 个 mod 场景里会被用到**，就预置进 payload；只被 1 个场景用到的，留 B 层。

### 5.3 属性组（state groups）：分组建议与节省倍数

现状：`unit.info` 一个 op 打包了 10 个字段（已经是组的雏形），但没有"第二个组"，也没有"组的选择器"。

| 组 | 字段数 | 现状若用单读的往返 | 用组后 | 节省倍数 | 备注 |
|---|---|---|---|---|---|
| `unit.state` | 16 | 16（B 层）或 16（A 层单读） | 1 | **16×** | 把 `unit.info` 扩到 16 字段（向后兼容：只加不改） |
| `battle.skill_state` | 11 | 11 | 1 | **11×** | 吸收 Modular `getskilldata` 的 30 个 DataType 中真正高频的 11 个 |
| `battle.state` | 12 | 12 | 1 | **12×** | 回合/波次/双方计数，几乎所有 mod 的回合钩都要 |
| `battle.resources` | 9 | 9 | 1 | **9×** | 罪孽/共鸣/EGO 一起给 |
| `unit.resistances` | 10 | 10 | 1 | **10×** | 抗性覆盖（`ovwsinres`/`ovwatkres`）读改写都要 |
| `unit.buffs` | 8/个 Buff | N×8 | 1 | **N×8×** | 已有，补 has_count/category |
| `unit.passives` | 3 类 | 3 | 1 | **3×** | — |
| `unit.break_info` | 3 | 3 | 1 | **3×** | — |
| `unit.skills` | 4/技能 | N×4 | 1 | **N×4×** | — |
| `unit.keywords` | 全集 | N | 1 | **N×** | — |

**一个典型的"回合开始"钩子**（作者最常见的写法）：
```
纯单读：round + 3 个 player 单位 × 16 字段 + buffs        ≈ 1 + 48 + 3 = 52 次往返
用组：  battle.state + 3 × unit.state + 2 × unit.buffs    =  1 +  3 + 2 =  6 次往返     → 8.7×
```

### 5.4 时点 payload 预算

给的预算是"字段数"，不是字节数（字节量在 EVENTBATCH 里可以忽略）：

| 时点类别 | 建议 payload 字段数 | 理由 |
|---|---|---|
| 高频路径（`DamageDealt`/`CoinTossed`/`HpDamageTaken`） | **≤ 10** | 一次攻击可能触发几十次；每字段都要做一次 `Convert`，主线程回调里保持 O(1) |
| 中频（`SkillUsed`/`ClashWon`/`BuffGained`/`TurnStarted`） | **10–14** | 后者是"取到就能干活"，少一次补读的收益远大于字节成本 |
| 低频（`BattleStarted`/`RoundStarted`/`ActionsSorted`/`StageEnded`） | **12–18，允许带集合** | 一回合/一场一次，可以直接塞 `List[UnitInfo]` 快照，让作者 0 次补读 |

**硬约束（主线程安全）**：`payload` 里**不能**出现"遍历全场单位"这种 O(N) 工作量（例如给 `AttackSucceeded` 塞全员快照）。要快照就用低频时点 + `battle.units` 显式取。这条与现有 `HookTable` 的注释一致（`HookTable.cs:11-17`：回调里不碰 Python、O(1)）。

### 5.5 必须留在 B 层的能力（不要为了覆盖率硬塞进 A 层）

| 能力 | 为什么留 B 层 |
|---|---|
| 泛型单例（`Singleton<T>`、`SingletonBehavior<T>`）的任意成员 | IL2CPP 泛型实例化是共享的，反射拿到的 `Type` 不一定是游戏真正用的那个实例化；`BattleOps.StaticInstance` 这种逐名试的写法只能覆盖已知几个 |
| `UniTask` / 协程（`IEnumerator StartRound` 等）驱动的时序 | A 层是同步 request/response；等异步等于把主线程挂住 |
| Unity 表现层（`BattleUnitView`、`CharacterAppearance*`、`BattleMapPreset*`、材质/特效/相机） | 类型量大、版本漂移快、几乎全是引用类型；`bindings.py`（5243 成员）已经是更好的入口 |
| 静态数据表（`*StaticDataList`、`TextAsset` JSON） | 已在 `limbuspy.game.statics` 解决，不需要跨进程 |
| 非战斗场景（大厅、迷宫、编队、抽卡、UI） | 与 A 层"战斗内原生能力"的定位无关；Lethe 式全量替换属于另一个项目 |
| 参数组合爆炸的调用（`ChangeHp` 的 9 个参数全排列、`AddBuff` 的 history 列表） | A 层只暴露"语义化的那一支"，其余走 B 层 |
| 单次调试/冷门类型 | 就是 B 层存在的理由 |

---

## 6. 【核心】可扩展性瓶颈：A 层的两个手写点

这一节是 §7 的前置。**不解决这里，覆盖 60 个时点是不可能的。**

### 6.1 现状：加一个东西要改几处

| 加什么 | 必改处 | 现状 |
|---|---|---|
| 加一个 **op** | ① `ops/spec/api.toml`；② C# 实现方法；③ **`OpsEntry.cs:33-45` 手写 `Register("unit.info", _units.Info)` 一行** | ③ 是纯机械映射，却必须人手写 |
| 加一个 **时点** | ① `api.toml`（含 `csharp.target/method` + payload 名字/类型）；② **`HookTable.cs:123-179` 的 `Shape()` 加一个 `case`，里面用 `Arg(args, 4)` 这样的位置下标拆参** | ② 要求作者**人肉数目标方法的第几个参数**，且方法签名一变就静默取错位 |

具体证据（`HookTable.cs`）：

```csharp
// 现状：位置下标 + 手写 case
case "AttackSucceeded":
    return Lpcp.Map(
        "unit",   Ref(instance),
        "target", Ref(Arg(args, 2)),      // ← 2 是"target 是第 3 个参数"
        "final_damage", Long(Arg(args, 3)),
        ...
        "timing", Text(Arg(args, 8)));    // ← 8 是"timing 是第 9 个参数"
```

而生成器**已经把 payload 的名字和类型**写进 `OpsManifest.g.cs:31-35` 的 `HookSpec` 里了：

```csharp
new HookSpec("AttackSucceeded", "BattleUnitModel", "OnSucceedAttack", "postfix",
             new string[] { "unit", "target", "final_damage", ... },   // 名字有了
             new string[] { "unit", "unit", "int", ... }),             // 类型有了
//            ↑ 唯独缺「这个字段对应目标方法的哪个参数」
```

→ **只差一个"参数绑定"声明，整个 `Shape()` 就能被生成 + 通用化。**

### 6.2 结论：可行，而且不需要新的 toml 顶层概念

在 `[[hook]].payload[]` 里加三个可选键（对现有 5 个时点**完全向后兼容**——不写就退化成现在的手写路径）：

| 键 | 取值 | 含义 |
|---|---|---|
| `arg` | 目标方法的**参数名**（字符串），或 `"self"` / `"return"` | 该字段的来源 |
| `arg` 的字符串形式 | `"loseBuffInfo.keyword"` | 从参数对象上取成员（`RightAfterLosingBuff` 的 keyword 只能这么拿） |
| `const` | 字面量 | 常量字段（例如固定写 `"turn=0"`） |
| `static` | `"StageController.GetCurrentRound"` | 无参数来源的字段（回合数等） |

**为什么用参数名而不是下标**：`AccessTools.Method(type, name)`（`HookTable.cs:50`）在不传参数类型时**遇到同名重载会歧义/选错**——这是当前实现的真实隐患（`dump.cs` 里 `OnChangeHp` 有 30+ 个同名声明，分属不同程序集/类型）。改成"按参数名解析下标"后，安装期就能：
1. 用**参数名集合**唯一定位重载（同名重载的参数名表不同）；
2. 方法改名/改参数 → `Install()` 返回 `problems`（已经是现有机制，见 `OpsEntry.cs:57-60`），**而不是静默取错位**。

### 6.3 生成出来的 C# 长什么样

`gen_contract.py` 新增一个产物 `kernel/LimbusPy.Ops/HookPayloadMap.g.cs`（纯生成，不手改）：

```csharp
// <auto-generated>由 tools/gen_contract.py 从 ops/spec/api.toml 生成 —— 不要手改。</auto-generated>
using System;
using System.Collections.Generic;

namespace LimbusPy.Ops
{
    internal enum HookFieldSource { Argument, Self, Return, Constant, Static, Member }

    /// <summary>一个 payload 字段怎么从补丁参数里取出来。</summary>
    internal sealed class HookFieldBinding
    {
        public string Name;         // payload 字段名（Python 侧 dataclass 一致）
        public string Type;         // "unit" | "int" | "float" | "bool" | "str"
        public HookFieldSource Source;
        public string ArgName;      // 目标方法参数名（Argument 时用于解析下标）
        public string MemberPath;   // Member 时：参数对象上的成员链，如 "keyword"
        public string Literal;      // Constant / Static 时
        public int ArgIndex = -1;   // 安装期按 ArgName 解析填入；-1 = 未解析
    }

    internal static class HookPayloadMap
    {
        // 每个时点的目标方法签名（安装期用来校验：方法没变、重载能唯一确定）
        internal static readonly Dictionary<string, string[]> Params = new(StringComparer.Ordinal)
        {
            ["AttackSucceeded"] = new[] { "action", "coin", "target", "finalDmg", "realDmg",
                                          "realHpDmg", "isCritical", "isWinDuel", "timing" },
            ["HpDamageTaken"]   = new[] { "finalDamage", "hpDamage", "timing", "sourceType",
                                          "attackerOrNull", "actionOrNull", "relatedUnitsOrNull",
                                          "keyword", "attributeOrNone", "atkTypeOrNone" },
            ["BuffGained"]      = new[] { "keyword", "stack", "turn", "activeRound", "srcType",
                                          "timing", "giverOrNull", "actionOrNull", "overStack", "overTurn" },
            // ... 每个 [[hook]] 一条，生成器产出
        };

        internal static readonly Dictionary<string, HookFieldBinding[]> Fields = new(StringComparer.Ordinal)
        {
            ["AttackSucceeded"] = new[]
            {
                new HookFieldBinding { Name = "unit",         Type = "unit", Source = HookFieldSource.Self },
                new HookFieldBinding { Name = "target",       Type = "unit", Source = HookFieldSource.Argument, ArgName = "target" },
                new HookFieldBinding { Name = "final_damage", Type = "int",  Source = HookFieldSource.Argument, ArgName = "finalDmg" },
                new HookFieldBinding { Name = "real_damage",  Type = "int",  Source = HookFieldSource.Argument, ArgName = "realDmg" },
                new HookFieldBinding { Name = "hp_damage",    Type = "int",  Source = HookFieldSource.Argument, ArgName = "realHpDmg" },
                new HookFieldBinding { Name = "is_critical",  Type = "bool", Source = HookFieldSource.Argument, ArgName = "isCritical" },
                new HookFieldBinding { Name = "timing",       Type = "str",  Source = HookFieldSource.Argument, ArgName = "timing" },
            },
            ["BuffGained"] = new[]
            {
                new HookFieldBinding { Name = "keyword",     Type = "str",  Source = HookFieldSource.Argument, ArgName = "keyword" },
                new HookFieldBinding { Name = "stacks",      Type = "int",  Source = HookFieldSource.Argument, ArgName = "stack" },
                new HookFieldBinding { Name = "turns",       Type = "int",  Source = HookFieldSource.Argument, ArgName = "turn" },
                new HookFieldBinding { Name = "giver",       Type = "unit", Source = HookFieldSource.Argument, ArgName = "giverOrNull" },
                new HookFieldBinding { Name = "timing",      Type = "str",  Source = HookFieldSource.Argument, ArgName = "timing" },
            },
            ["BuffLost"] = new[]
            {
                new HookFieldBinding { Name = "stacks",  Type = "int", Source = HookFieldSource.Argument, ArgName = "loseStack" },
                new HookFieldBinding { Name = "turns",   Type = "int", Source = HookFieldSource.Argument, ArgName = "loseTurn" },
                // 嵌套成员：keyword 只能从 BuffInfo 里拿
                new HookFieldBinding { Name = "keyword", Type = "str", Source = HookFieldSource.Member, ArgName = "loseBuffInfo", MemberPath = "keyword" },
                new HookFieldBinding { Name = "timing",  Type = "str", Source = HookFieldSource.Argument, ArgName = "timing" },
            },
            ["RoundStarted"] = new[]
            {
                new HookFieldBinding { Name = "unit",  Type = "unit", Source = HookFieldSource.Self },
                new HookFieldBinding { Name = "timing", Type = "str", Source = HookFieldSource.Argument, ArgName = "timing" },
                new HookFieldBinding { Name = "round", Type = "int",  Source = HookFieldSource.Static, Literal = "StageController.GetCurrentRound" },
            },
        };
    }
}
```

`HookTable.cs` 里的手写 `Shape()` switch **整个删掉**，换成一个通用 shaper（手写一次，约 70 行，永不随 op 增长）：

```csharp
// 手写一次：与具体时点无关
internal sealed class PayloadShaper
{
    private readonly HookFieldBinding[] _fields;

    internal PayloadShaper(HookFieldBinding[] fields) { _fields = fields; }

    internal Dictionary<string, object> Shape(object instance, object[] args, object ret)
    {
        var row = new Dictionary<string, object>(_fields.Length, StringComparer.Ordinal);
        foreach (var f in _fields)
        {
            object raw;
            switch (f.Source)
            {
                case HookFieldSource.Self:   raw = instance; break;
                case HookFieldSource.Return: raw = ret; break;
                case HookFieldSource.Static: raw = HookStatics.Read(f.Literal); break;
                case HookFieldSource.Constant: raw = f.Literal; break;
                case HookFieldSource.Member:
                    raw = HookStatics.Member(f.ArgIndex >= 0 && f.ArgIndex < args.Length ? args[f.ArgIndex] : null, f.MemberPath);
                    break;
                default: // Argument
                    raw = f.ArgIndex >= 0 && f.ArgIndex < args.Length ? args[f.ArgIndex] : null;
                    break;
            }
            row[f.Name] = Coerce(f.Type, raw);
        }
        return row;
    }
}
```

安装期解析 + 校验（替换 `HookTable.Install()` 里 `AccessTools.Method(type, spec.Method)` 那一段）：

```csharp
internal List<string> Install()
{
    var failures = new List<string>();
    foreach (var spec in OpsManifest.Hooks)
    {
        var type = ResolveType(spec.Target);
        if (type == null) { failures.Add($"{spec.Timing}: type {spec.Target} not found"); continue; }

        // ① 用"参数名集合"唯一定位重载（顺便把同名重载的歧义打死）
        var declared = HookPayloadMap.Params.TryGetValue(spec.Timing, out var names) ? names : null;
        MethodBase method = HookBindings.Resolve(type, spec.Method, declared);
        if (method == null)
        { failures.Add($"{spec.Timing}: {spec.Target}.{spec.Method}({string.Join(",", declared ?? Array.Empty<string>())}) not found"); continue; }

        // ② 把 payload 字段解析成参数下标；解析不了就是契约错误，不是运行时惊喜
        if (!HookBindings.Bind(HookPayloadMap.Fields[spec.Timing], method.GetParameters(), out var error))
        { failures.Add($"{spec.Timing}: {error}"); continue; }

        // ③ 按 kind 装补丁（postfix / prefix / prefix_result）
        var patch = HookBindings.MakePatch(spec.Kind);
        _harmony.Patch(method, prefix: spec.Kind == "postfix" ? null : patch,
                              postfix: spec.Kind == "postfix" ? patch : null);
        _shapers[method] = new PayloadShaper(HookPayloadMap.Fields[spec.Timing]);
        _timingByMethod[method] = spec.Timing;
        Installed.Add(spec.Timing);
    }
    return failures;
}
```

### 6.4 op 注册：可以 100% 生成（toml 不需要改）

`api.toml` 里每个 op **已经有** `class = "UnitOps"` 和 `method = "info"`，所以注册表可以完全机械地生成。新增产物 `kernel/LimbusPy.Ops/OpsRegistry.g.cs`：

```csharp
// <auto-generated>由 tools/gen_contract.py 从 ops/spec/api.toml 生成 —— 不要手改。</auto-generated>
namespace LimbusPy.Ops
{
    public sealed partial class OpsEntry
    {
        /// <summary>契约清单 → 原生实现。全生成，手写注册表已删除。</summary>
        private void RegisterAll()
        {
            Register("unit.info",            UnitOps.Info);
            Register("unit.buff_stacks",     UnitOps.BuffStacks);
            Register("battle.damage",        BattleOps.Damage);
            Register("unit.state",           UnitOps.State);
            // ... 每个 [[op]] 一行
        }
    }
}
```

配套：`OpsEntry` 把 `UnitOps` / `BattleOps` 的实例方法暴露成**静态委托**（或在生成器里生成 `UnitOps.Info` 形式的 `static` 包装），这是唯一需要一次性手写的粘合层；之后加 op 只改 toml + 写实现方法。

### 6.5 生成器无法表达、必须手写的部分（明确分界）

| 类别 | 归属 | 说明 |
|---|---|---|
| op 的**注册** | **生成** | toml 已有 `class`/`method` |
| op 的**参数解析**（`OpsArgs.Unit/Int/EnumValue`） | **手写一次**（已有） | 类型词汇表稳定，不需要生成 |
| op 的**实现体** | **手写**（必须） | 涉及游戏类型/枚举/私有成员/out 参数 |
| hook 的**挂点 + payload 形状** | **生成**（本次改造目标） | 新增 `arg`/`member`/`static`/`const` |
| hook 的**取字段逻辑** | **生成表 + 手写通用 shaper** | shaper 与具体时点无关 |
| hook 的**跨调用状态**（例：`ClashStarted` 需要"这是本回合第几次拼点"） | **手写 `IHookScope`**（约 10% 的时点） | 声明式拿不到"上一帧"的信息；用 `scope:` 来源 + 每个时点一个小实现，注册在生成表旁边 |
| **可干预型**（改返回值 / 改参数） | **生成 `kind` + 手写 `mutate` 列表** | 生成器产出 prefix 的形状，但"改哪个 out 参数"必须声明 |

→ 结论：**70–75% 的新时点可以变成"只改 toml"**，剩下 25% 需要手写一个 5–20 行的 scope/mutate 适配器。

---

## 7. 实现方案

### 7.1 `ops/spec/api.toml` 的三个完整扩展示例

**例 1：一个时点（声明式 payload + 嵌套成员）**

```toml
[[hook]]
timing = "BuffGained"
summary = "单位获得 Buff（层数/回合/生效回合/来源/给予者都已预置）。"
doc = """
高频、且作者 90% 的写法都是"拿到关键字+层数就判条件"，所以 payload 直接给全，
不需要在回调里再读一次 Buff 列表。
"""
csharp = { target = "BattleUnitModel", method = "RightAfterGetAnyBuff", kind = "postfix" }
# 目标方法的参数名表：安装期用它唯一定位重载 + 校验 arg 名没错字
params = ["keyword", "stack", "turn", "activeRound", "srcType", "timing",
          "giverOrNull", "actionOrNull", "overStack", "overTurn"]
payload = [
  { name = "unit",         type = "unit", arg = "self" },
  { name = "keyword",      type = "str",  arg = "keyword", doc = "Buff 主关键字" },
  { name = "stacks",       type = "int",  arg = "stack" },
  { name = "turns",        type = "int",  arg = "turn" },
  { name = "active_round", type = "int",  arg = "activeRound", doc = "0=本回合 1=下回合" },
  { name = "src_type",     type = "str",  arg = "srcType", doc = "ABILITY_SOURCE_TYPE 名" },
  { name = "giver",        type = "unit", arg = "giverOrNull", optional = true },
  { name = "over_stacks",  type = "int",  arg = "overStack", doc = "溢出层数（用于"满层浪费"统计）" },
  { name = "timing",       type = "str",  arg = "timing" },
]

# 第二个时点：嵌套成员 + 静态来源
[[hook]]
timing = "BuffLost"
summary = "Buff 被消耗或移除（keyword 从 BuffInfo 里取）。"
csharp = { target = "BattleUnitModel", method = "RightAfterLosingBuff", kind = "postfix" }
params = ["loseStack", "loseTurn", "loseBuffInfo", "timing"]
payload = [
  { name = "unit",    type = "unit", arg = "self" },
  { name = "stacks",  type = "int",  arg = "loseStack" },
  { name = "turns",   type = "int",  arg = "loseTurn" },
  { name = "keyword", type = "str",  arg = "loseBuffInfo.keyword", doc = "BuffInfo 上的成员" },
  { name = "timing",  type = "str",  arg = "timing" },
]
```

**例 2：一个动作（op，带批量选择器）**

```toml
[[op]]
name = "battle.damage"
class = "BattleOps"
method = "damage"
summary = "对一组单位造成伤害（选择器在 C# 侧解析，一次往返打 N 个目标）。"
doc = """
**批量的样板**。`targets` 用选择器字符串（和 Modular 的 Multi-Target 同一套词汇）：

  "AllEnemy" / "LowestHPRatioEnemy" / "HighestHPRatioAlly" / "RandomEnemy" /
  "ExceptSelf" / "id1234" / "inst5678"

C# 侧在一个主线程时间片里：解析选择器 → 逐个 `ChangeHp` → 返回每个目标伤害后的状态。
如果拆成 Python 的 N 次 `unit.damage`，会跨 N 次帧边界，中途目标可能已经死了/换位了。
"""
args = [
  { name = "targets", type = "selector", doc = "选择器字符串，见 doc" },
  { name = "amount",  type = "int",      doc = "每个目标的伤害点数" },
  { name = "source",  type = "source",   default = "\"PASSIVE\"" },
  { name = "timing",  type = "timing",   default = "\"ON_SUCCESS_ATTACK\"" },
]
returns = "List[UnitInfo]"
returns_doc = "每个实际受影响目标的伤害后状态（applied=False 的会保留，便于作者判断）"
stability = "experimental"
```

**例 3：一个属性组**

```toml
[[op]]
name = "unit.state"
class = "UnitOps"
method = "state"
summary = "单位状态组：一次往返拿 16 个高频字段（替代 16 次单读）。"
doc = """
`unit.info` 的超集。字段只增不改，所以老的 `unit.info` 继续可用；
新代码应该优先用它 —— 差的 6 个字段（speed/shield/break_level/panic_level/is_actionable/level）
恰好是"判断要不要动手"最常需要的。

返回的 `UnitState` 是 frozen dataclass：`hp_ratio` / `missing_hp` / `is_critical_hp` 都是派生属性，0 次往返。
"""
group = true
args = [
  { name = "unit", type = "unit", doc = "目标单位" },
]
returns = "UnitState"
returns_doc = "hp/max_hp/sp/max_sp/level/speed/shield/break_level/panic_level/position/participate_order/unit_id/attribute/is_actionable/is_retreated/is_player"
```

### 7.2 `tools/gen_contract.py` 需要增强什么

| # | 增强 | 影响文件 | 必要性 |
|---|---|---|---|
| G1 | **正向：`RegisterAll()` 生成**（读 `class`/`method` → 产出 `OpsRegistry.g.cs`） | `gen_contract.py` + 新产物 | P0 前置（零 toml 变更即可落地） |
| G2 | **`HookFieldBinding` 表生成**（`arg`/`member`/`static`/`const` → `HookPayloadMap.g.cs`），同时把 `params` 名字表写进 `HookSpec` | `gen_contract.py` + `OpsSpec`/`HookSpec` + 新产物 | P0 前置 |
| G3 | **`kind` 扩展**：`postfix`（默认）/ `prefix` / `prefix_result`；`mutate = ["isCritical"]`（可写回的**参数**名） | `HookSpec.Kind` 现有字段够用；新增 `Mutate` 数组 | P0-3 |
| G4 | **`selector` 参数类型**：`ARG_TYPES` 加 `"selector": "str"`，Python 侧生成 `Sequence[UnitRefLike] | str` 的重载壳（纯 Python 转换，C# 侧只认字符串） | `ARG_TYPES` + `emit_ops` | P1 |
| G5 | **新返回类型**：`UnitState` / `List[UnitState]` / `SkillState` / `BattleState` / `ResistInfo` / `List[ActionInfo]` 等，加进 `RETURN_TYPES` + `api/types.py` 手写 dataclass | `RETURN_TYPES` + `types.py` | P1（每个新类型一次） |
| G6 | **`--lint` 门禁**：解析 `dump.cs` / `il2csharp\out\limbus\` 的方法签名，校验每个 `[[hook]].params` 与真实参数名**逐个一致**、`payload[].arg` 确实存在 | 新增 `tools/check_hook_bindings.py` | P0 前置（防"人肉数参数"，见 §9 R1） |
| G7 | **`--audit`**：对每个 op 校验"清单有、C# 有、MockOps 有、conformance 有"四件套齐全 | `gen_contract.py` | P1 |

**G6 是这次改造的安全网**：不加它，改 toml 的人依然要靠眼睛核对签名；加了之后，"签名漂移"在 CI 就红了。

### 7.3 C# 侧文件布局

| 文件 | 职责 | 变化 |
|---|---|---|
| `kernel/LimbusPy.Ops/OpsEntry.cs` | 注册表 + 自检 + 时机安装 | **删掉手写 `Register` 块**，改调生成 partial 的 `RegisterAll()` |
| `kernel/LimbusPy.Ops/OpsManifest.g.cs` | 契约清单（生成） | `HookSpec` 增 `Params` 字段 |
| `kernel/LimbusPy.Ops/HookPayloadMap.g.cs` | **新增（生成）** 字段绑定表 | — |
| `kernel/LimbusPy.Ops/OpsRegistry.g.cs` | **新增（生成）** op 注册 | — |
| `kernel/LimbusPy.Ops/HookTable.cs` | 安装/采集/帧末推送 | **删 `Shape()` switch**；`Install()` 改为解析式；新增 `HookBindings`（重载定位 + 下标解析） |
| `kernel/LimbusPy.Ops/PayloadShaper.cs` | **新增（手写一次）** 通用 payload 装配 | 约 70 行，永不增长 |
| `kernel/LimbusPy.Ops/HookStatics.cs` | **新增（手写）** `static:` 来源的读取器（`StageController.GetCurrentRound` 等） | 每个静态来源一行 |
| `kernel/LimbusPy.Ops/HookScopes.cs` | **新增（手写）** 约 10% 时点的跨调用状态适配器 | 每个时点 5–20 行 |
| `kernel/LimbusPy.Ops/UnitOps.cs` | 单位级 op | 按表 2 增加方法（分成 `UnitOps.Buffs.cs` / `UnitOps.Skills.cs` 等 partial，避免单文件 3000 行） |
| `kernel/LimbusPy.Ops/BattleOps.cs` | 战斗级 op | 同上 |
| `kernel/LimbusPy.Ops/Selector.cs` | **新增（手写）** 目标选择器解析（`AllEnemy` / `LowestHPRatioEnemy` / ...） | 移植 `ModularScripts.cs:960-1034` 的词汇表 |
| `kernel/LimbusPy.Ops/Args/EnumMaps.cs` | **新增（手写）** 中文/字符串枚举名 → 游戏枚举（现在靠 `Enum.TryParse` 大小写不敏感） | 给 `BUFF_CATEGORY_KEYWORD`/`ATTRIBUTE_TYPE` 等补别名 |

**Harmony 补丁点**：不需要新的 Harmony 实例，`HookTable` 的 `_harmony`（`"LimbusPy.Curated"`）继续用；新增的是 `kind` 维度（prefix/postfix）与"按参数名定位重载"。**注意**：可干预型时点（`SkillPowerRolled` 等）建议单独一个 Harmony id（`"LimbusPy.Curated.Mutate"`），便于出问题时一键只停干预、保留观测（`Shutdown()` 已经是 `_harmony.UnpatchSelf()`，分实例后就变成"分级降压"）。

### 7.4 分阶段

#### P0 — 扩展底座 + 作者 80% 需求（必须先做，约等于现在的 3× 覆盖）

**P0-0（前置，先做，不含功能）**
1. G1：生成式 `RegisterAll()` → 删 `OpsEntry` 手写注册。
2. G2 + G6：`arg`/`member`/`static`/`const` + `HookPayloadMap.g.cs` + `PayloadShaper` + `check_hook_bindings.py` lint。
3. G3：`kind = prefix` / `prefix_result` + `mutate`。

> 验收：现有 5 个 hook 在改造后**行为完全不变**（用 `test_curated_api.py` + conformance 的现有断言）；改造后再加第 6 个时点**只改 toml**（用一个 throwaway 时点验证，验证完删掉）。

**P0-1 时点 5 → 27**（表 1 的 P0 行，共 27 个，含已有 5）：
`DamageDealt*` `HpDamageTaken` `MpDamaged` `MpChanged` `AttackSucceeded*` `CoinStarted` `CoinTossed` `ClashStarted` `ClashWon` `ClashLost` `SkillUsed` `SkillEnded` `SkillPowerRolled` `CoinPowerRolled` `TurnStarted*` `TurnEnded` `RoundStarted` `RoundEnded` `CommandCompleted` `ActionsSorted` `UnitDied*` `UnitBroken*` `EnemyKilled` `BuffGained` `BuffLost`
（`*` = 已有）

**P0-2 函数 13 → 28**（= 表 2 的 P0 行 23 + 表 3 的 P0 属性组 5；其中 9 个是现有 op 或其超集演进版，净新增 19）：
`unit.damage*` `battle.damage` `unit.damage_ext` `unit.heal*` `unit.set_hp*` `unit.set_sp` `unit.heal_sp` `unit.add_shield` `unit.set_speed` `unit.add_buff*` `unit.remove_buff` `unit.add_buff_stack` `unit.set_buff_stack` `unit.set_buff_turn` `unit.burst_by_buff*` `unit.add_passive*` `unit.break` `battle.units*` `battle.round*` `battle.action_order` `unit.state` `unit.buffs*` `battle.skill_state` `battle.state` `battle.resources` `rng.int` `rng.choice` `battle.add_sin_resource`

**P0 验收标准（全部可测）**
1. `python tools/gen_contract.py --check` 通过；`OpsManifest` 自检 `problems == []`。
2. `tools/check_hook_bindings.py` 对 27 个时点全部 PASS（参数名逐字匹配 `dump.cs`/il2csharp）。
3. `python -m pytest python/tests/test_curated_api.py` 全绿（含新 op 的 MockOps 覆盖断言）。
4. conformance 的 live 段新增"27 个 hook 全部 `installed=true`"一项检查。
5. 示例 mod 达到 **≤6 次往返/回合**（§5.3 的算例），并在 `run_curated_api.py` 里断言计数。
6. 真机：进战斗，27 个时点各至少触发一次（日志计数），`unit.state` 的 16 字段与游戏 UI 对上。

#### P1 — 长尾（覆盖 Modular/ModularCustomA 最常用的 90% → 98%）

* 时点 +31（表 1 的 P1 行共 31 个）：`Healed` `ZeroHp` `BeforeGiveDamage` `AttackConfirmed` `BeforeAttack` `AttackEnded` `CriticalActivated` `CoinEnded` `ParryWon` `ParryLost` `ParryResolved` `SkillBeforeBehaviour` `AttackWeightRolled` `TurnStartBeforeLog` `RoundStartBefore` `BattleStarted` `BattleEnded` `WaveStarted` `StageStarted` `BreakRecovered` `Panicked` `LowMorale` `UnitRetreated` `PartDestroyed` `BuffUsed` `BuffDestroyed` `BuffGivenBySkill` `ShieldAdded` `ShieldReduced` `SinBuffActivated` `EquipDefense` `SpeedRefreshed`
* 函数 +27（表 2 的 P1 行共 27 个）：`unit.set_max_hp` `unit.set_level` `unit.get_shield` `unit.set_defense` `unit.set_speed_limit` `unit.refresh_speed` `battle.remove_all_actions` `unit.buff_edit_max` `unit.destroy_buffs_by_type` `unit.remove_passive` `unit.add_skill` `unit.replace_dashboard_skill` `unit.break_recover` `unit.deactivate_break_bar` `unit.instant_kill` `unit.set_panic_level` `unit.set_sin_resist` `unit.set_atk_resist` `battle.set_sin_resource` `battle.sin_resonance` `battle.wave` `battle.slot_info` `battle.skill_send` `unit.retreat` `battle.end_win` `rng.heads_chance` `fx.log`
* 属性组补全：`unit.keywords` `unit.passives` `unit.skills` `unit.resistances` `unit.break_info`
* 验收：新增 op 的 `--audit` 四件套齐全；P1 时点里"可干预型"（`AttackWeightRolled`）在真机上验证能改到值。

#### P2 — 表现层 / 边缘

* 时点 +18（表 1 的 P2 行共 18 个）：闪避/取消/无对峙/威力/最大层数/护盾破碎/EGO 被动/振动/强制击溃/精神崩溃/返场/不死/波次结束/关卡结束/等待指令/信息面板，以及 8 个"可改值但收益低"的点。
* 函数 +22（表 2 的 P2 行共 22 个）：硬币增删/技能 ability/SP 消耗/EGO 被动/恐慌类型/break 加条/护盾清空/行动插队/拼点/召唤/生成/结算/表现层 5 个。
* **明确不做**：`BattleUnitView` 级视觉钩（交给 B 层 + `bindings.py`）、迷宫/大厅/编队、静态数据表。

### 7.5 每个新增 op/hook 必须配套什么测试（对齐现有模式）

| 层 | 测试 | 位置 | 现有样板 |
|---|---|---|---|
| 契约 | `gen_contract.py --check`（产物最新）+ 新增 `--audit`（四件套） | `tools/gen_contract.py` | 现有 `--check` |
| 契约 ↔ Python 壳 | 参数名/默认值/docstring 必须含 op 名 | `test_curated_api.py::test_generated_wrapper_matches_manifest` | **已有，自动覆盖新 op** |
| 契约 ↔ 参考实现 | `MockOps.op_<name>` 必须存在且 docstring 非空 | `test_curated_api.py::test_mock_ops_covers_manifest` | **已有，自动覆盖新 op**（所以每个新 op 都要在 `limbuspy/_mock.py` 加一个 `op_xxx`） |
| 契约 ↔ payload | dataclass 字段顺序 == manifest payload 顺序 | `test_curated_api.py::test_hooks_payloads_match_manifest` | **已有，自动覆盖新 hook** |
| hook **参数名** | `check_hook_bindings.py`：`params` 与真实签名逐字一致 | 新增 lint（G6） | — |
| hook **安装** | conformance live：manifest 里每个 hook `installed == true` | `tools/conformance.py::run_live` | 现有 reflect/BATCH 检查的写法 |
| op **行为** | 每个新 op 加一条 golden check（入参 → 期望 wire 形状） | `tools/conformance.py::run_golden` | 现有 `golden` 段 |
| op **批量语义** | `battle.damage` 一次调用 N 目标 → 返回 N 条 `UnitInfo`，`applied` 正确 | `test_curated_api.py` 新增 | — |
| 属性组 **等价性** | `unit.state` 的 16 字段 ⊇ `unit.info` 的 10 字段且值相同 | 新增 `test_types.py` | 不需要游戏（MockOps） |
| 真机 | 每个 op/hook 的"最小可观测证据"写进 P0/P1 验收清单 | `docs/plans/03-coverage.md` §7.4 | `docs/05-typed-api.md` §5 的真机清单 |

---

## 8. 受风险影响的取舍（写明的"不做"）

| 决策 | 理由 |
|---|---|
| 不做 189 个 TimingID 的 1:1 映射 | Modular 的 timing 是它自己 `timingDict` 的键，很多是同一游戏方法的多别名（`OnBreak`/`OnOtherBreak`、`OnDie`/`OnOtherDie`）。A 层按**游戏方法**建时点，"自己/他人"用 payload 里的 `unit == self` 判断即可。 |
| 不做 `getskilldata` 的 30 个 DataType | 归并成 `battle.skill_state` 的 11 个高频字段；剩余走 B 层。 |
| 不做比较/算术类 op（`AcquirerMath`/`Absolute`/`HpCheck`） | 纯 Python 侧计算，跨进程毫无收益。 |
| 不做 `setworldpos`/`playmotion`/`changeanimspeed` 等纯表现 | B 层 + `bindings.py` 足够；且高频高风险。 |
| 不把"批量"做成 Python 侧循环 | 循环会跨帧边界（目标可能已死/换位），语义错误 —— 必须 C# 侧一个时间片做完，这也是 `burst_by_buff` 存在的理由。 |

---

## 9. 风险

### R1 三份静态产物不是同一个游戏版本（**已核实，最高风险**）

| 产物 | 时间戳 | 说明 |
|---|---|---|
| `Il2CppDumper-release/dump.cs` | 2026-10-23 | 扁平全量，**同名方法/类型极多**（`OnChangeHp` 在 dump.cs 里有 30+ 处，分属不同程序集与类型） |
| `Il2CppDumper-release/script.json` | 2026-10-29 | Il2CppDumper 原始符号（无参数名） |
| `il2csharp/out/limbus/cw4d/` | 2026-11-02 00:42 | **带方法体、最新**，本计划的签名以此为准 |
| `il2csharp/out/limbus/full0/` | 2026-11-02 07:20 | 同版本更晚的全量导出（内容一致，已抽查 `OnSucceedAttack`/`OnChangeHp`） |

**具体证据**：`dump.cs` 里第一个匹配 `OnSucceedAttack(` 的是
`OnSucceedAttack(BattleActionModel action, CoinModel coin, BattleUnitModel target, int finalDmg, int realDmg, bool isCritical, Nullable<bool> isWinDuel, BATTLE_EVENT_TIMING timing, BuffInfo info)`（**9 参、无 `realHpDmg`**），
而 `BattleUnitModel` 的真实签名（il2csharp）是
`OnSucceedAttack(BattleActionModel action, CoinModel coin, BattleUnitModel target, int finalDmg, int realDmg, **int realHpDmg**, bool isCritical, Nullable<bool> isWinDuel, BATTLE_EVENT_TIMING timing)`。
→ 靠"在 dump.cs 里搜方法名"来核对参数会**直接核错**。**这正是 §7.2 G6 lint 必须按类型限定 + 按参数名匹配的原因。**

### R2 位置下标脆弱（**已存在，改造即修**）

`HookTable.Shape()` 用 `Arg(args, 4)` 这类下标；`AccessTools.Method(type, name)` 不传参数类型时遇到同名重载会歧义。改造后（按参数名解析 + 解析失败即 `problems`）两个问题一起消失。

### R3 IL2CPP 下不可靠的游戏方法

| 类别 | 具体 | 处置 |
|---|---|---|
| **泛型** | `Singleton<T>.Instance`、`SingletonBehavior<T>`、`List<T>` 的 IL2CPP 实例化共享 | 不放进 A 层（§5.5）；已有的 `BattleOps.StaticInstance` 靠逐名尝试，属已知妥协 |
| **异步 / 协程** | `StageController.StartRound`（private，`IEnumerator` 驱动）、所有 `UniTask` 方法 | 只挂**同步入口**；协程内部时序不挂 |
| **属性 setter / 内联** | `ObscuredInt`（`Level`/`_currentRound`）在 IL2CPP 下是结构体包装；小方法可能被 Burst/内联 | 读用现有隐式转换 + `Convert` 兜底；写不要直接改 `ObscuredInt` 字段 |
| **private / protected** | `OnSucceedEvade`/`OnFailedEvade`/`OnAttackCanceledByAbility`（private）、`OnPanic`/`OnLowMorale`/`OnRecoverBreak`（protected） | Harmony 可以挂（`AccessTools` 含 nonpublic），但**优先级压到 P2 并标 `?`**；真机验证后再升 |
| **同签名多处** | `OnChangeHp` 在 30+ 类型上重名 | 必须 `target` 写全限定类型名 + 参数名集合定位（G6） |

### R4 补丁点冲突（ModularLimbis / Lethe 已占用）

* ModularLimbis 已挂：`BattleUnitModel.OnChangeHp`、`OnSucceedAttack`、`OnBreak`、`OnStartTurn_AfterLog`（**与现有 5 个时点完全重叠**）、`RightAfterGetAnyBuff`、`RightAfterGiveBuffBySkill`、`SkillModel.GetCoinScaleAdder`、`BattleActionModelManager.SortActions` 等 **164** 处。
* ModularCustomA 已挂：`BattleUnitModel.RightAfterGetAnyBuff`、`RightAfterLosingBuff`、`OnUseBuff`、`OnChangeMp`、`OnTakeMpDamage`、`OnRecoverBreak`、`PassiveDetail.OnRoundStart_Before`、`GateSP` 等 **68** 处。
* Lethe 在战斗链上占：`BattleUnitView.StartBehaviourAction`、`PassiveDetail.OnPartBreaked`、`BattleObjectManager.OnRoundStart_Model`、`StageController.EndStage`、`StageModel.Init`、`BattleUnitView.StartCoinToss`。

**结论**：观测型时点（postfix、不改返回值）多个 mod 叠加是安全的（Harmony 按优先序全跑）；**真正危险的是 prefix + 跳过原方法**。所以本计划把"可干预型时点"单独放在 `"LimbusPy.Curated.Mutate"` 这个 Harmony id 下、默认**不**跳过原方法（只改返回值/只改 out 参数），并在文档里明确告警"装了 Modular 时慎用 prefix 跳过"。

### R5 op 数量膨胀的维护成本

风险：68 op + 66 hook ×（toml + C# + Mock + golden + 文档）≈ 400 个必须同步的位置。

控制手段：
1. **单一真源 + 生成**：`api.toml` → 4 个产物（现有）+ 2 个新产物（`OpsRegistry.g.cs`、`HookPayloadMap.g.cs`），手写面只剩"实现方法体"。
2. **`--audit` 四件套门禁**：缺 MockOps / 缺 golden / 缺 docstring → CI 红。
3. **文档自动生成**：表 1/2/3 的"名称/参数/返回/优先级/现状"直接从 `api.toml` + manifest 产出 markdown 片段，避免本文档与代码漂移（本文档只保留"语义一句话"和"游戏方法"这类人工判断列）。
4. **稳定性分级**：新 op 一律 `stability = "experimental"`，真机验证后才升 `stable`；`experimental` 的失败模式固定为"返回 False/None 不抛错"（沿用 `unit.add_buff` 的做法）。
5. **软上限**：A 层 op 总数超过 **80** 时，新需求优先做成属性组字段或 Python 侧组合，不再新增顶层 op。

### R6 需要真机验证的条目（汇总）

**共 53 条**（表 1：**12** / 表 2：**32** / 表 3：**9**），按"为什么不确定"分三类（同一行可能落在两类里，按主因归类）：

* **签名/可访问性未核实（表 1 全部 12 条 + 表 2 的 11 条）**：`EvadeSucceeded` `EvadeFailed` `AttackCanceled`（private）、`BreakRecovered` `Panicked` `LowMorale`（protected）、`ParryResolved` `UnopposedAttack` `ActionsSorted` `BuffLost`(keyword 只能从 `BuffInfo` 取)、`BuffDestroyed` `PartDestroyed`、`WaitCommand` `InfoOpened` `SpeedRefreshed`；以及 `unit.remove_passive` `unit.add_skill` `unit.remove_skill` `unit.replace_dashboard_skill` `unit.clear_temp_skill_abilities` `unit.set_skill_sp_usage` `unit.activate_ego_passive` `unit.remove_coin` `unit.add_coin` `battle.slot_info` `battle.add_duel` 的目标成员是否公认可写。
* **游戏方法语义没把握（表 2 的 15 条 + 表 1 的 `BuffDestroyed`）**：`unit.damage_ext`（`ignoreShield`/`ignorePassedBreakSection` 的真实行为）、`unit.set_defense`（`_overridedDef` vs adder 链）、`unit.set_speed_limit`、`unit.buff_edit_max`、`unit.add_keyword`/`remove_keyword`、`unit.break_add_bar`、`battle.remove_all_actions`、`battle.set_action_index`、`battle.skill_send`、`battle.summon`/`spawn_enemy`、`unit.retreat`、`unit.set_panic_type`、`fx.*`（5 个）。
* **属性来源不确定（表 3 的 9 条）**：`participate_order`、`character_id`、`attribute`、`appearance_id`、`defense_level`、`attack`/`defense`、`current_skill_slot`、`remaining_actions`、`ego_resource`。

> 这 53 条**在实现时必须先做"只读探针"再写 op**：先用 B 层反射读一次确认成员存在且取值合理，再落成 A 层原生实现。**不要凭 dump 猜签名**（见 R1）。

**验证方式（10 分钟）**：装 `LimbusPy.Ops.dll` → 进战斗 → REPL 里 `session.ops.unit.state(u)` 等逐个调用并和游戏 UI 对照；时点用"日志计数"确认触发（现有 `damage_ledger` 示例的模式）。

---

## 10. 附：与 `MTCSLuaDef.lua` 的逐条覆盖对照（抽样，证明没有漏大类）

| `MTCSLuaDef.lua` 函数（举例） | 本计划落点 |
|---|---|
| `changehp` / `changesp` / `setmaxhp` / `setlevel` / `setspeed` / `setspeedlimit` / `refreshspeed` | 表 2 A/C 组 |
| `instantdeath` / `lbreak` / `deactivebreak` / `changepaniclevel` / `changepanictype` | 表 2 F 组 |
| `destroybuff` / `bufcategory` / `editbuffmax` / `getbuffhascount` / `listbuffs` / `listrelatedkeywords` / `getbufflocaledata` | 表 2 D 组 + 表 3 `unit.buffs` |
| `addskill` / `removeskill` / `hasskill` / `getskilldata` / `addcoin` / `removecoin` / `addskillability` / `addcoinability` / `clearskillabilities` / `clearcoinabilities` / `setspusage` / `replaceskillondashboard` / `upgradeskillondashboard` | 表 2 E 组 + 表 3 `battle.skill_state` |
| `changehp` 之外的伤害族 `ovwatkres` / `ovwsinres` / `setchange…` | 表 2 G 组 |
| `unitfaction` / `getatkres` / `getsinres` / `getdefaultmaxhp` / `gethpincrement` / `getuptielevel` / `getbreaklevel` / `isactionable` / `useddefaction` / `issinnerfielded` / `getappearanceid` | 表 3 单读 + `unit.state` |
| `getmang` / `getsepiralevel` / `getkeyinput` / `getworldpos` / `setworldpos` / `playmotion` / `changeanimspeed` / `applyskilleffect` / `hasskilleffect` / `allowrefreshrenderer` / `allowopeninfo` / `modifyrichpresence` / `dynamiclocale_*` | **明确不做**（§8）或 P2 表现层 |
| `jsontolua` / `getgdata` / `setgdata` / `clearallgdata` / `readfile` / `listdirectories` | 不做（Python 侧原生具备，无跨进程必要） |
| `gettimingid` / `gbstack` / `gbturn` / `gbkeyword` / `gbsource` / `gbactiveround` / `saslotindex` | **由时点 payload 取代**：这些是 Modular 因"没有类型化 payload"才需要的补丁期全局查询；A 层直接把值放进 payload |
| 189 个 `TimingID` | 归一成 76 个游戏方法级时点（§8 第一条） |
