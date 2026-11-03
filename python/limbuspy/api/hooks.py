"""由 tools/gen_contract.py 从 ops/spec/api.toml 生成 —— 不要手改。"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, overload

from ..mod.decorators import HookSpec
from .types import UnitRef


@dataclass(frozen=True)
class DamageDealtPayload:
    """DamageDealt：任意来源的 HP 变化（含非本 mod 造成的）。

    IL2CPP 挂点: ``BattleUnitModel.OnChangeHp``（postfix）
    """

    unit: Optional[UnitRef] = None
    attacker: Optional[UnitRef] = None
    old_hp: int = 0
    new_hp: int = 0
    source: str = ""
    timing: str = ""
    keyword: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "DamageDealtPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), attacker=UnitRef.from_wire(raw.get('attacker')), old_hp=int(raw.get('old_hp') or 0), new_hp=int(raw.get('new_hp') or 0), source=str(raw.get('source') or ""), timing=str(raw.get('timing') or ""), keyword=str(raw.get('keyword') or ""))


@dataclass(frozen=True)
class AttackSucceededPayload:
    """AttackSucceeded：一次攻击命中结算完成（带伤害分解）。

    IL2CPP 挂点: ``BattleUnitModel.OnSucceedAttack``（postfix）
    """

    unit: Optional[UnitRef] = None
    target: Optional[UnitRef] = None
    final_damage: int = 0
    real_damage: int = 0
    hp_damage: int = 0
    is_critical: bool = False
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "AttackSucceededPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), target=UnitRef.from_wire(raw.get('target')), final_damage=int(raw.get('final_damage') or 0), real_damage=int(raw.get('real_damage') or 0), hp_damage=int(raw.get('hp_damage') or 0), is_critical=bool(raw.get('is_critical')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class UnitDiedPayload:
    """UnitDied：单位死亡。

    IL2CPP 挂点: ``BattleUnitModel.OnDie``（postfix）
    """

    unit: Optional[UnitRef] = None
    killer: Optional[UnitRef] = None
    source: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "UnitDiedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), killer=UnitRef.from_wire(raw.get('killer')), source=str(raw.get('source') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class UnitBrokenPayload:
    """UnitBroken：单位进入崩溃（break）状态。

    IL2CPP 挂点: ``BattleUnitModel.OnBreak``（postfix）
    """

    unit: Optional[UnitRef] = None
    attacker: Optional[UnitRef] = None
    is_forced: bool = False
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "UnitBrokenPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), attacker=UnitRef.from_wire(raw.get('attacker')), is_forced=bool(raw.get('is_forced')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class TurnStartedPayload:
    """TurnStarted：单位回合开始（日志之后）。

    IL2CPP 挂点: ``BattleUnitModel.OnStartTurn_AfterLog``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "TurnStartedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class HpDamageTakenPayload:
    """HpDamageTaken：单位受到 HP 伤害（带来源、关联单位与 Buff 关键字）。

    IL2CPP 挂点: ``BattleUnitModel.OnTakeHpDamage``（postfix）
    """

    unit: Optional[UnitRef] = None
    attacker: Optional[UnitRef] = None
    final_damage: int = 0
    hp_damage: int = 0
    source: str = ""
    keyword: str = ""
    attribute: str = ""
    atk_type: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "HpDamageTakenPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), attacker=UnitRef.from_wire(raw.get('attacker')), final_damage=int(raw.get('final_damage') or 0), hp_damage=int(raw.get('hp_damage') or 0), source=str(raw.get('source') or ""), keyword=str(raw.get('keyword') or ""), attribute=str(raw.get('attribute') or ""), atk_type=str(raw.get('atk_type') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class MpDamagedPayload:
    """MpDamaged：单位受到理智（MP/SP）伤害。

    IL2CPP 挂点: ``BattleUnitModel.OnTakeMpDamage``（postfix）
    """

    unit: Optional[UnitRef] = None
    attacker: Optional[UnitRef] = None
    value: int = 0
    source: str = ""
    keyword: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "MpDamagedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), attacker=UnitRef.from_wire(raw.get('attacker')), value=int(raw.get('value') or 0), source=str(raw.get('source') or ""), keyword=str(raw.get('keyword') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class MpChangedPayload:
    """MpChanged：单位理智值发生变化（任何来源，含回复）。

    IL2CPP 挂点: ``BattleUnitModel.OnChangeMp``（postfix）
    """

    unit: Optional[UnitRef] = None
    old_mp: int = 0
    new_mp: int = 0

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "MpChangedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), old_mp=int(raw.get('old_mp') or 0), new_mp=int(raw.get('new_mp') or 0))


@dataclass(frozen=True)
class CoinStartedPayload:
    """CoinStarted：一枚硬币开始结算。

    IL2CPP 挂点: ``BattleUnitModel.OnStartCoin``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "CoinStartedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class CoinRolledPayload:
    """CoinRolled：一枚硬币掷出结果（攻击后）。

    IL2CPP 挂点: ``BattleUnitModel.OnRollOneCoin_AfterAttack``（postfix）
    """

    unit: Optional[UnitRef] = None
    value: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "CoinRolledPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), value=int(raw.get('value') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ClashStartedPayload:
    """ClashStarted：拼点（duel）开始。

    IL2CPP 挂点: ``BattleUnitModel.OnStartDuel``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ClashStartedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ClashWonPayload:
    """ClashWon：拼点胜利。

    IL2CPP 挂点: ``BattleUnitModel.OnWinDuel``（postfix）
    """

    unit: Optional[UnitRef] = None
    parrying_count: int = 0
    remained_coin: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ClashWonPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), parrying_count=int(raw.get('parrying_count') or 0), remained_coin=int(raw.get('remained_coin') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ClashLostPayload:
    """ClashLost：拼点失败。

    IL2CPP 挂点: ``BattleUnitModel.OnLoseDuel``（postfix）
    """

    unit: Optional[UnitRef] = None
    parrying_count: int = 0
    remained_coin: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ClashLostPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), parrying_count=int(raw.get('parrying_count') or 0), remained_coin=int(raw.get('remained_coin') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class SkillStartedPayload:
    """SkillStarted：单位开始一个行为（技能/防御/闪避）。

    IL2CPP 挂点: ``BattleUnitModel.OnStartBehaviour``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "SkillStartedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class SkillEndedPayload:
    """SkillEnded：单位的行为结束。

    IL2CPP 挂点: ``BattleUnitModel.OnEndBehaviour``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "SkillEndedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class AttackConfirmedPayload:
    """AttackConfirmed：攻击命中被确认（伤害结算之前）。

    IL2CPP 挂点: ``BattleUnitModel.OnAttackConfirmed``（postfix）
    """

    unit: Optional[UnitRef] = None
    target: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "AttackConfirmedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), target=UnitRef.from_wire(raw.get('target')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class AttackEndedPayload:
    """AttackEnded：一次攻击结束（带累计伤害）。

    IL2CPP 挂点: ``BattleUnitModel.OnEndAttack``（postfix）
    """

    unit: Optional[UnitRef] = None
    is_win_duel: bool = False
    total_damage: int = 0
    total_hp_damage: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "AttackEndedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), is_win_duel=bool(raw.get('is_win_duel')), total_damage=int(raw.get('total_damage') or 0), total_hp_damage=int(raw.get('total_hp_damage') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class TurnStartingPayload:
    """TurnStarting：单位回合开始（日志之前）。

    IL2CPP 挂点: ``BattleUnitModel.OnStartTurn_BeforeLog``（postfix）
    """

    unit: Optional[UnitRef] = None
    is_duel_expected: bool = False
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "TurnStartingPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), is_duel_expected=bool(raw.get('is_duel_expected')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class TurnEndedPayload:
    """TurnEnded：单位回合结束。

    IL2CPP 挂点: ``BattleUnitModel.OnEndTurn``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "TurnEndedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class RoundStartedPayload:
    """RoundStarted：回合（round）开始。

    IL2CPP 挂点: ``BattleUnitModel.OnRoundStart_After_Event``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "RoundStartedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class RoundEndedPayload:
    """RoundEnded：回合（round）结束。

    IL2CPP 挂点: ``BattleUnitModel.OnRoundEnd``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "RoundEndedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class CommandCompletedPayload:
    """CommandCompleted：指令阶段完成（本回合的行动已全部排定）。

    IL2CPP 挂点: ``BattleUnitModel.OnCompleteCommand``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "CommandCompletedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class BattleStartedPayload:
    """BattleStarted：战斗开始。

    IL2CPP 挂点: ``BattleUnitModel.OnBattleStart``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "BattleStartedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class BattleEndedPayload:
    """BattleEnded：战斗结束。

    IL2CPP 挂点: ``BattleUnitModel.OnBattleEnd``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "BattleEndedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class WaveStartedPayload:
    """WaveStarted：波次开始。

    IL2CPP 挂点: ``BattleUnitModel.OnWaveStart``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "WaveStartedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class StageStartedPayload:
    """StageStarted：关卡开始。

    IL2CPP 挂点: ``BattleUnitModel.OnStageStart``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "StageStartedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class EnemyKilledPayload:
    """EnemyKilled：击杀目标（击杀者视角）。

    IL2CPP 挂点: ``BattleUnitModel.OnKillTarget``（postfix）
    """

    unit: Optional[UnitRef] = None
    target: Optional[UnitRef] = None
    source: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "EnemyKilledPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), target=UnitRef.from_wire(raw.get('target')), source=str(raw.get('source') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ZeroHpPayload:
    """ZeroHp：单位血量归零（死亡结算之前）。

    IL2CPP 挂点: ``BattleUnitModel.OnZeroHp``（postfix）
    """

    unit: Optional[UnitRef] = None

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ZeroHpPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')))


@dataclass(frozen=True)
class UnitRetreatedPayload:
    """UnitRetreated：单位退场。

    IL2CPP 挂点: ``BattleUnitModel.OnRetreat``（postfix）
    """

    unit: Optional[UnitRef] = None
    trigger: Optional[UnitRef] = None
    keyword: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "UnitRetreatedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), trigger=UnitRef.from_wire(raw.get('trigger')), keyword=str(raw.get('keyword') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class BuffUsedPayload:
    """BuffUsed：一个 Buff 被消耗/结算。

    IL2CPP 挂点: ``BattleUnitModel.OnUseBuff``（postfix）
    """

    unit: Optional[UnitRef] = None
    keyword: str = ""
    stack: int = 0
    turn: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "BuffUsedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), keyword=str(raw.get('keyword') or ""), stack=int(raw.get('stack') or 0), turn=int(raw.get('turn') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class BuffLostPayload:
    """BuffLost：单位失去一个罪孽系 Buff 的层数。

    IL2CPP 挂点: ``BattleUnitModel.OnLoseSinBuff``（postfix）
    """

    unit: Optional[UnitRef] = None
    keyword: str = ""
    stack: int = 0
    turn: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "BuffLostPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), keyword=str(raw.get('keyword') or ""), stack=int(raw.get('stack') or 0), turn=int(raw.get('turn') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ShieldAddedPayload:
    """ShieldAdded：单位获得护盾。

    IL2CPP 挂点: ``BattleUnitModel.OnAddShield``（postfix）
    """

    unit: Optional[UnitRef] = None
    value: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ShieldAddedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), value=int(raw.get('value') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ParryWonPayload:
    """ParryWon：对拼（parrying）获胜。

    IL2CPP 挂点: ``BattleUnitModel.OnWinParrying``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ParryWonPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ParryLostPayload:
    """ParryLost：对拼（parrying）失败。

    IL2CPP 挂点: ``BattleUnitModel.OnLoseParrying``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ParryLostPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class CoinEndedPayload:
    """CoinEnded：一枚硬币结算结束。

    IL2CPP 挂点: ``BattleUnitModel.OnEndCoin_AfterLog``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "CoinEndedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class CoinCheckedPayload:
    """CoinChecked：硬币结果判定完成。

    IL2CPP 挂点: ``BattleUnitModel.OnResult_AfterCheckCoin``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "CoinCheckedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class BeforeAttackPayload:
    """BeforeAttack：攻击结算之前。

    IL2CPP 挂点: ``BattleUnitModel.OnBeforeAttack``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "BeforeAttackPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class BreakForcedPayload:
    """BreakForced：被强制击溃（不经过普通崩溃流程）。

    IL2CPP 挂点: ``BattleUnitModel.OnBreakForcely``（postfix）
    """

    unit: Optional[UnitRef] = None
    attacker: Optional[UnitRef] = None
    source: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "BreakForcedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), attacker=UnitRef.from_wire(raw.get('attacker')), source=str(raw.get('source') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ShieldReducedPayload:
    """ShieldReduced：护盾被削减。

    IL2CPP 挂点: ``BattleUnitModel.OnReduceShield``（postfix）
    """

    unit: Optional[UnitRef] = None
    old: int = 0
    value: int = 0
    attacker: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ShieldReducedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), old=int(raw.get('old') or 0), value=int(raw.get('value') or 0), attacker=UnitRef.from_wire(raw.get('attacker')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ShieldDestroyedPayload:
    """ShieldDestroyed：护盾被打破。

    IL2CPP 挂点: ``BattleUnitModel.OnDestroyShield``（postfix）
    """

    unit: Optional[UnitRef] = None
    attacker: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ShieldDestroyedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), attacker=UnitRef.from_wire(raw.get('attacker')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class MentalCollapsePayload:
    """MentalCollapse：精神崩溃。

    IL2CPP 挂点: ``BattleUnitModel.OnMentalCollapse``（postfix）
    """

    unit: Optional[UnitRef] = None

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "MentalCollapsePayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')))


@dataclass(frozen=True)
class FakeDiePayload:
    """FakeDie：假死（免死效果触发）。

    IL2CPP 挂点: ``BattleUnitModel.OnFakeDie``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "FakeDiePayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class HpHealedPayload:
    """HpHealed：HP 回复结算完成。

    IL2CPP 挂点: ``BattleUnitModel.OnAfterTryTakeHpHeal``（postfix）
    """

    unit: Optional[UnitRef] = None
    healer: Optional[UnitRef] = None
    try_heal: int = 0
    result_heal: int = 0
    source: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "HpHealedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), healer=UnitRef.from_wire(raw.get('healer')), try_heal=int(raw.get('try_heal') or 0), result_heal=int(raw.get('result_heal') or 0), source=str(raw.get('source') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class MpHealedPayload:
    """MpHealed：理智（MP/SP）回复结算完成。

    IL2CPP 挂点: ``BattleUnitModel.OnAfterTryTakeMpHeal``（postfix）
    """

    unit: Optional[UnitRef] = None
    healer: Optional[UnitRef] = None
    try_heal: int = 0
    result_heal: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "MpHealedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), healer=UnitRef.from_wire(raw.get('healer')), try_heal=int(raw.get('try_heal') or 0), result_heal=int(raw.get('result_heal') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class SineBuffActivatedPayload:
    """SineBuffActivated：罪孽系 Buff 效果被激活。

    IL2CPP 挂点: ``BattleUnitModel.OnActivateSinBuffEffect``（postfix）
    """

    unit: Optional[UnitRef] = None
    keyword: str = ""
    stack: int = 0
    turn: int = 0
    take_damage: int = 0
    giver: Optional[UnitRef] = None
    is_forced: bool = False
    is_critical: bool = False
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "SineBuffActivatedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), keyword=str(raw.get('keyword') or ""), stack=int(raw.get('stack') or 0), turn=int(raw.get('turn') or 0), take_damage=int(raw.get('take_damage') or 0), giver=UnitRef.from_wire(raw.get('giver')), is_forced=bool(raw.get('is_forced')), is_critical=bool(raw.get('is_critical')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class BuffBlockedPayload:
    """BuffBlocked：一次 Buff 施加被拦截。

    IL2CPP 挂点: ``BattleUnitModel.OnBlockGivingBuff``（postfix）
    """

    unit: Optional[UnitRef] = None
    target: Optional[UnitRef] = None
    keyword: str = ""
    is_critical: bool = False
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "BuffBlockedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), target=UnitRef.from_wire(raw.get('target')), keyword=str(raw.get('keyword') or ""), is_critical=bool(raw.get('is_critical')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class BuffFailedToGivePayload:
    """BuffFailedToGive：Buff 施加失败。

    IL2CPP 挂点: ``BattleUnitModel.OnFailedToGiveBuff``（postfix）
    """

    unit: Optional[UnitRef] = None
    target: Optional[UnitRef] = None
    keyword: str = ""
    stack: int = 0
    turn: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "BuffFailedToGivePayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), target=UnitRef.from_wire(raw.get('target')), keyword=str(raw.get('keyword') or ""), stack=int(raw.get('stack') or 0), turn=int(raw.get('turn') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class CoinConsumedPayload:
    """CoinConsumed：硬币被消耗并挂上 Buff。

    IL2CPP 挂点: ``BattleUnitModel.OnUseCoinConsume``（postfix）
    """

    unit: Optional[UnitRef] = None
    keyword: str = ""
    stack: int = 0
    turn: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "CoinConsumedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), keyword=str(raw.get('keyword') or ""), stack=int(raw.get('stack') or 0), turn=int(raw.get('turn') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class BuffStackBySkillPayload:
    """BuffStackBySkill：技能消耗了某个 Buff 的层数。

    IL2CPP 挂点: ``BattleUnitModel.OnUseBuffStackBySkill``（postfix）
    """

    unit: Optional[UnitRef] = None
    keyword: str = ""
    stack: int = 0
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "BuffStackBySkillPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), keyword=str(raw.get('keyword') or ""), stack=int(raw.get('stack') or 0), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class UnitAddedPayload:
    """UnitAdded：有新单位加入战斗（召唤/增援）。

    IL2CPP 挂点: ``BattleUnitModel.OnAddUnit``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "UnitAddedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class UnitReturnedPayload:
    """UnitReturned：退场的单位返回战场。

    IL2CPP 挂点: ``BattleUnitModel.OnReturnToField``（postfix）
    """

    unit: Optional[UnitRef] = None
    retreat_turn: int = 0
    trigger: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "UnitReturnedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), retreat_turn=int(raw.get('retreat_turn') or 0), trigger=UnitRef.from_wire(raw.get('trigger')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class EnemyBrokenByAttackerPayload:
    """EnemyBrokenByAttacker：把敌人打出崩溃（攻击者视角，带本次扣血）。

    IL2CPP 挂点: ``BattleUnitModel.OnEnemyBrokenByAttacker``（postfix）
    """

    unit: Optional[UnitRef] = None
    target: Optional[UnitRef] = None
    hp_damage: int = 0
    source: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "EnemyBrokenByAttackerPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), target=UnitRef.from_wire(raw.get('target')), hp_damage=int(raw.get('hp_damage') or 0), source=str(raw.get('source') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class TurnStartBeforePayload:
    """TurnStartBefore：单位回合开始之前（日志之前）。

    IL2CPP 挂点: ``BattleUnitModel.OnBeforeTurn``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "TurnStartBeforePayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ParryResolvedPayload:
    """ParryResolved：一次对拼结算完成。

    IL2CPP 挂点: ``BattleUnitModel.OnAfterParryingOnce_AfterLog``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ParryResolvedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class DuelEndedPayload:
    """DuelEnded：拼点（duel）结束。

    IL2CPP 挂点: ``BattleUnitModel.OnDuelAfter_AfterLog``（postfix）
    """

    unit: Optional[UnitRef] = None
    self_coin: int = 0
    oppo_coin: int = 0
    result: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "DuelEndedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), self_coin=int(raw.get('self_coin') or 0), oppo_coin=int(raw.get('oppo_coin') or 0), result=str(raw.get('result') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class CoinBeforeStartPayload:
    """CoinBeforeStart：一枚硬币即将开始结算（可在改值泳道里干预）。

    IL2CPP 挂点: ``BattleUnitModel.OnBeforeStartCoin``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "CoinBeforeStartPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class AttackOnOtherEndedPayload:
    """AttackOnOtherEnded：某单位对另一单位的攻击结束。

    IL2CPP 挂点: ``BattleUnitModel.OnEndAttackOtherUnit``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "AttackOnOtherEndedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class EnemyPartDestroyedPayload:
    """EnemyPartDestroyed：打掉了敌人的某个部位。

    IL2CPP 挂点: ``BattleUnitModel.OnEnemyPartDestroyedByAttacker``（postfix）
    """

    unit: Optional[UnitRef] = None
    target: Optional[UnitRef] = None
    source: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "EnemyPartDestroyedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), target=UnitRef.from_wire(raw.get('target')), source=str(raw.get('source') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class UnitAddedAfterPayload:
    """UnitAddedAfter：新单位加入战斗之后（全部初始化完成）。

    IL2CPP 挂点: ``BattleUnitModel.OnAddUnit_After``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "UnitAddedAfterPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class RoundStartBeforePayload:
    """RoundStartBefore：回合（round）开始之前。

    IL2CPP 挂点: ``BattleUnitModel.OnRoundStart_Before``（postfix）
    """

    unit: Optional[UnitRef] = None

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "RoundStartBeforePayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')))


@dataclass(frozen=True)
class RoundStartSlotStatePayload:
    """RoundStartSlotState：回合开始时槽位状态已就绪。

    IL2CPP 挂点: ``BattleUnitModel.OnRoundStart_After_RelatedSlotState``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "RoundStartSlotStatePayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class RoundEndDeadPayload:
    """RoundEndDead：回合结束时的阵亡结算。

    IL2CPP 挂点: ``BattleUnitModel.OnRoundEnd_Dead``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "RoundEndDeadPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class WaveEndedPayload:
    """WaveEnded：波次结束。

    IL2CPP 挂点: ``BattleUnitModel.OnWaveEnd``（postfix）
    """

    unit: Optional[UnitRef] = None

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "WaveEndedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')))


@dataclass(frozen=True)
class StageEndedPayload:
    """StageEnded：关卡结束。

    IL2CPP 挂点: ``BattleUnitModel.OnStageEnd``（postfix）
    """

    unit: Optional[UnitRef] = None
    cleared: bool = False

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "StageEndedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), cleared=bool(raw.get('cleared')))


@dataclass(frozen=True)
class ShieldResetPayload:
    """ShieldReset：护盾被重置。

    IL2CPP 挂点: ``BattleUnitModel.OnResetShield``（postfix）
    """

    unit: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ShieldResetPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ImmortalityActivatedPayload:
    """ImmortalityActivated：免死效果被触发。

    IL2CPP 挂点: ``BattleUnitModel.OnActivateImmortality``（postfix）
    """

    unit: Optional[UnitRef] = None
    attacker: Optional[UnitRef] = None
    activator: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ImmortalityActivatedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), attacker=UnitRef.from_wire(raw.get('attacker')), activator=UnitRef.from_wire(raw.get('activator')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class ImmortalStateGivenPayload:
    """ImmortalStateGiven：给单位挂上不死状态。

    IL2CPP 挂点: ``BattleUnitModel.OnGiveImmortalState``（postfix）
    """

    unit: Optional[UnitRef] = None
    taker: Optional[UnitRef] = None
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "ImmortalStateGivenPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), taker=UnitRef.from_wire(raw.get('taker')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class MpDamageSinBuffPayload:
    """MpDamageSinBuff：罪孽系 Buff 造成的理智伤害。

    IL2CPP 挂点: ``BattleUnitModel.OnTakeMpDmg_SinBuff``（postfix）
    """

    unit: Optional[UnitRef] = None
    value: int = 0
    keyword: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "MpDamageSinBuffPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), value=int(raw.get('value') or 0), keyword=str(raw.get('keyword') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class KilledTargetByAllyPayload:
    """KilledTargetByAlly：队友击杀了目标（旁观者视角）。

    IL2CPP 挂点: ``BattleUnitModel.OnKillTargetByOtherAlly``（postfix）
    """

    unit: Optional[UnitRef] = None
    target: Optional[UnitRef] = None
    source: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "KilledTargetByAllyPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), target=UnitRef.from_wire(raw.get('target')), source=str(raw.get('source') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class OtherUnitDiedPayload:
    """OtherUnitDied：别的单位死亡（旁观者视角）。

    IL2CPP 挂点: ``BattleUnitModel.OnDieOtherUnit``（postfix）
    """

    unit: Optional[UnitRef] = None
    dead: Optional[UnitRef] = None
    killer: Optional[UnitRef] = None
    source: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "OtherUnitDiedPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), dead=UnitRef.from_wire(raw.get('dead')), killer=UnitRef.from_wire(raw.get('killer')), source=str(raw.get('source') or ""), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class OtherUnitBrokenPayload:
    """OtherUnitBroken：别的单位崩溃（旁观者视角）。

    IL2CPP 挂点: ``BattleUnitModel.OnBreakOtherUnit``（postfix）
    """

    unit: Optional[UnitRef] = None
    broken: Optional[UnitRef] = None
    is_forced: bool = False
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "OtherUnitBrokenPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), broken=UnitRef.from_wire(raw.get('broken')), is_forced=bool(raw.get('is_forced')), timing=str(raw.get('timing') or ""))


@dataclass(frozen=True)
class VibrationExplosionPayload:
    """VibrationExplosion：振动（Vibration）爆炸结算。

    IL2CPP 挂点: ``BattleUnitModel.OnVibrationExplosion``（postfix）
    """

    unit: Optional[UnitRef] = None
    giver: Optional[UnitRef] = None
    ability_source: str = ""
    timing: str = ""

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "VibrationExplosionPayload":
        raw = raw or {}
        return cls(unit=UnitRef.from_wire(raw.get('unit')), giver=UnitRef.from_wire(raw.get('giver')), ability_source=str(raw.get('ability_source') or ""), timing=str(raw.get('timing') or ""))


#: 时机名 → payload 类型
PAYLOAD_BY_TIMING: Dict[str, Any] = {
    'DamageDealt': DamageDealtPayload,
    'AttackSucceeded': AttackSucceededPayload,
    'UnitDied': UnitDiedPayload,
    'UnitBroken': UnitBrokenPayload,
    'TurnStarted': TurnStartedPayload,
    'HpDamageTaken': HpDamageTakenPayload,
    'MpDamaged': MpDamagedPayload,
    'MpChanged': MpChangedPayload,
    'CoinStarted': CoinStartedPayload,
    'CoinRolled': CoinRolledPayload,
    'ClashStarted': ClashStartedPayload,
    'ClashWon': ClashWonPayload,
    'ClashLost': ClashLostPayload,
    'SkillStarted': SkillStartedPayload,
    'SkillEnded': SkillEndedPayload,
    'AttackConfirmed': AttackConfirmedPayload,
    'AttackEnded': AttackEndedPayload,
    'TurnStarting': TurnStartingPayload,
    'TurnEnded': TurnEndedPayload,
    'RoundStarted': RoundStartedPayload,
    'RoundEnded': RoundEndedPayload,
    'CommandCompleted': CommandCompletedPayload,
    'BattleStarted': BattleStartedPayload,
    'BattleEnded': BattleEndedPayload,
    'WaveStarted': WaveStartedPayload,
    'StageStarted': StageStartedPayload,
    'EnemyKilled': EnemyKilledPayload,
    'ZeroHp': ZeroHpPayload,
    'UnitRetreated': UnitRetreatedPayload,
    'BuffUsed': BuffUsedPayload,
    'BuffLost': BuffLostPayload,
    'ShieldAdded': ShieldAddedPayload,
    'ParryWon': ParryWonPayload,
    'ParryLost': ParryLostPayload,
    'CoinEnded': CoinEndedPayload,
    'CoinChecked': CoinCheckedPayload,
    'BeforeAttack': BeforeAttackPayload,
    'BreakForced': BreakForcedPayload,
    'ShieldReduced': ShieldReducedPayload,
    'ShieldDestroyed': ShieldDestroyedPayload,
    'MentalCollapse': MentalCollapsePayload,
    'FakeDie': FakeDiePayload,
    'HpHealed': HpHealedPayload,
    'MpHealed': MpHealedPayload,
    'SineBuffActivated': SineBuffActivatedPayload,
    'BuffBlocked': BuffBlockedPayload,
    'BuffFailedToGive': BuffFailedToGivePayload,
    'CoinConsumed': CoinConsumedPayload,
    'BuffStackBySkill': BuffStackBySkillPayload,
    'UnitAdded': UnitAddedPayload,
    'UnitReturned': UnitReturnedPayload,
    'EnemyBrokenByAttacker': EnemyBrokenByAttackerPayload,
    'TurnStartBefore': TurnStartBeforePayload,
    'ParryResolved': ParryResolvedPayload,
    'DuelEnded': DuelEndedPayload,
    'CoinBeforeStart': CoinBeforeStartPayload,
    'AttackOnOtherEnded': AttackOnOtherEndedPayload,
    'EnemyPartDestroyed': EnemyPartDestroyedPayload,
    'UnitAddedAfter': UnitAddedAfterPayload,
    'RoundStartBefore': RoundStartBeforePayload,
    'RoundStartSlotState': RoundStartSlotStatePayload,
    'RoundEndDead': RoundEndDeadPayload,
    'WaveEnded': WaveEndedPayload,
    'StageEnded': StageEndedPayload,
    'ShieldReset': ShieldResetPayload,
    'ImmortalityActivated': ImmortalityActivatedPayload,
    'ImmortalStateGiven': ImmortalStateGivenPayload,
    'MpDamageSinBuff': MpDamageSinBuffPayload,
    'KilledTargetByAlly': KilledTargetByAllyPayload,
    'OtherUnitDied': OtherUnitDiedPayload,
    'OtherUnitBroken': OtherUnitBrokenPayload,
    'VibrationExplosion': VibrationExplosionPayload,
}

#: 全部 A 层时机（可用于校验/文档）
TIMINGS: List[str] = ['DamageDealt', 'AttackSucceeded', 'UnitDied', 'UnitBroken', 'TurnStarted', 'HpDamageTaken', 'MpDamaged', 'MpChanged', 'CoinStarted', 'CoinRolled', 'ClashStarted', 'ClashWon', 'ClashLost', 'SkillStarted', 'SkillEnded', 'AttackConfirmed', 'AttackEnded', 'TurnStarting', 'TurnEnded', 'RoundStarted', 'RoundEnded', 'CommandCompleted', 'BattleStarted', 'BattleEnded', 'WaveStarted', 'StageStarted', 'EnemyKilled', 'ZeroHp', 'UnitRetreated', 'BuffUsed', 'BuffLost', 'ShieldAdded', 'ParryWon', 'ParryLost', 'CoinEnded', 'CoinChecked', 'BeforeAttack', 'BreakForced', 'ShieldReduced', 'ShieldDestroyed', 'MentalCollapse', 'FakeDie', 'HpHealed', 'MpHealed', 'SineBuffActivated', 'BuffBlocked', 'BuffFailedToGive', 'CoinConsumed', 'BuffStackBySkill', 'UnitAdded', 'UnitReturned', 'EnemyBrokenByAttacker', 'TurnStartBefore', 'ParryResolved', 'DuelEnded', 'CoinBeforeStart', 'AttackOnOtherEnded', 'EnemyPartDestroyed', 'UnitAddedAfter', 'RoundStartBefore', 'RoundStartSlotState', 'RoundEndDead', 'WaveEnded', 'StageEnded', 'ShieldReset', 'ImmortalityActivated', 'ImmortalStateGiven', 'MpDamageSinBuff', 'KilledTargetByAlly', 'OtherUnitDied', 'OtherUnitBroken', 'VibrationExplosion']

# -- 静态推导：@on("Timing") 的 payload 类型 ------------------------------------

@overload
def on(timing: Literal["DamageDealt"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["AttackSucceeded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["UnitDied"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["UnitBroken"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["TurnStarted"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["HpDamageTaken"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["MpDamaged"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["MpChanged"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["CoinStarted"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["CoinRolled"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ClashStarted"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ClashWon"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ClashLost"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["SkillStarted"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["SkillEnded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["AttackConfirmed"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["AttackEnded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["TurnStarting"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["TurnEnded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["RoundStarted"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["RoundEnded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["CommandCompleted"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["BattleStarted"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["BattleEnded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["WaveStarted"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["StageStarted"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["EnemyKilled"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ZeroHp"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["UnitRetreated"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["BuffUsed"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["BuffLost"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ShieldAdded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ParryWon"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ParryLost"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["CoinEnded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["CoinChecked"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["BeforeAttack"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["BreakForced"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ShieldReduced"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ShieldDestroyed"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["MentalCollapse"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["FakeDie"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["HpHealed"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["MpHealed"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["SineBuffActivated"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["BuffBlocked"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["BuffFailedToGive"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["CoinConsumed"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["BuffStackBySkill"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["UnitAdded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["UnitReturned"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["EnemyBrokenByAttacker"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["TurnStartBefore"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ParryResolved"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["DuelEnded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["CoinBeforeStart"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["AttackOnOtherEnded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["EnemyPartDestroyed"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["UnitAddedAfter"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["RoundStartBefore"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["RoundStartSlotState"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["RoundEndDead"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["WaveEnded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["StageEnded"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ShieldReset"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ImmortalityActivated"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["ImmortalStateGiven"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["MpDamageSinBuff"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["KilledTargetByAlly"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["OtherUnitDied"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["OtherUnitBroken"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...
@overload
def on(timing: Literal["VibrationExplosion"], when: Any = ..., allow_rpc: bool = ..., **filters: Any) -> Callable[[Callable[..., Any]], Any]: ...



# --------------------------------------------------------------------------------------
# 运行时：`@on("Timing")` 注册成普通 hook，回调里把 ctx.payload 换成类型化对象
# --------------------------------------------------------------------------------------


def wants_context(fn: Any) -> bool:
    """回调是否接受第三个参数（``ctx``）。约定：``def f(self, ev, ctx)``。"""
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = [p for p in parameters
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    has_varargs = any(p.kind == p.VAR_POSITIONAL for p in parameters)
    return has_varargs or len(positional) >= 3


def _arity(fn: Any) -> int:
    """位置参数个数；``*args`` 或签名读不到时返回 ``-1``（表示"愿意全收"）。"""
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return -1
    if any(p.kind == p.VAR_POSITIONAL for p in parameters):
        return -1
    return sum(1 for p in parameters
               if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))


def on(timing: str, when: Any = None, allow_rpc: bool = False, **filters: Any):
    """声明一个 A 层时机回调。

    **三种签名都可以**（装饰器按位置参数个数自己判断，作者不用记规则）::

        @on("DamageDealt")                     # 类体：只要 payload
        def on_damage(self, ev: DamageDealtPayload) -> None: ...

        @on("DamageDealt")                     # 类体：还要 ctx（ops/session）
        def on_damage(self, ev: DamageDealtPayload, ctx) -> None: ...

        @on("DamageDealt")                     # 模块顶层：没有 self
        def on_damage(ev: DamageDealtPayload) -> None: ...

    过滤条件用 ``when=``（声明式谓词，注册期编译、运行期零往返）::

        @on("AttackSucceeded", when=victim.has(Drowning))
        def drown(self, ev: AttackSucceededPayload) -> None: ...

    与高级层 ``@hook`` 的区别：payload 是**归一化的类型化对象**（C# 侧已经拆好参数），
    不需要知道游戏方法的顺序。
    """

    def decorate(fn):
        payload_cls = PAYLOAD_BY_TIMING.get(timing)
        priority = int(filters.get("priority") or 0)
        spec = HookSpec(timing, dict(filters), priority, False, None,
                        when=when, allow_rpc=bool(allow_rpc))
        arity = _arity(fn)

        def wrapper(*args: Any):
            # 内核/运行时永远只传一个 ctx；类体里由 Python 描述符补上 self，
            # 所以这里看到 1 个参数 = 模块级函数，2 个 = 绑定的方法。
            if len(args) == 1:
                bound, owner, ctx = False, None, args[0]
            elif len(args) == 2:
                bound, owner, ctx = True, args[0], args[1]
            else:  # pragma: no cover - 运行时不会这么调
                raise TypeError("@on 回调只接受 (ctx) 或 (self, ctx)，收到 %d 个参数" % (len(args),))

            payload = ctx.payload
            if payload_cls is not None:
                if isinstance(payload, dict):
                    payload = payload_cls.from_wire(payload)
                elif payload is None:
                    payload = payload_cls.from_wire({})

            # 去掉 self 之后还剩几个位置参数，决定作者要不要 ctx。
            slots = arity - 1 if bound else arity
            if arity < 0 or slots >= 2:
                return fn(owner, payload, ctx) if bound else fn(payload, ctx)
            return fn(owner, payload) if bound else fn(payload)

        wrapper.__name__ = getattr(fn, "__name__", "on_" + timing.lower())
        wrapper.__doc__ = fn.__doc__
        wrapper.__limbuspy_hook__ = spec
        return wrapper

    return decorate
