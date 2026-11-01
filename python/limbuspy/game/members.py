"""Member-name tables for the game objects LimbusPy wraps.

Every name here was verified against a real ``dump.cs`` (see
``tools/requirements/limbuspy.json``); ``limbuspy doctor`` re-checks them on every new build. Nothing
in :mod:`limbuspy.game` may use a member name that is not in one of these tables - that rule is what
keeps the facade honest instead of "looks plausible in Python, dies on the first call".
"""

from __future__ import annotations

from typing import Dict, List, Optional

__all__ = ["Members", "UNIT", "BUFF", "SKILL", "COIN", "ACTION", "MANAGER", "validate_against_index"]


class Members:
    """A named table of IL2CPP member names for one type."""

    __slots__ = ("type_name", "names")

    def __init__(self, type_name: str, **names: str) -> None:
        self.type_name = type_name
        self.names = dict(names)

    def __getattr__(self, key: str) -> str:
        # NOTE: __slots__ means there is no __dict__; going through it here would recurse into
        # __getattr__ forever. Slots are read with object.__getattribute__ instead.
        try:
            names = object.__getattribute__(self, "names")
        except AttributeError:
            raise AttributeError(key) from None
        try:
            return names[key]
        except KeyError:
            type_name = object.__getattribute__(self, "type_name")
            raise AttributeError("%s has no member table entry %r" % (type_name, key)) from None

    def items(self):
        return self.names.items()

    def name_for(self, alias: str) -> str:
        """The IL2CPP member name, with the '()' call marker stripped."""
        return getattr(self, alias).removesuffix("()")

    def is_call(self, alias: str) -> bool:
        """True when the member is a method that must be invoked rather than read."""
        return getattr(self, alias).endswith("()")

    def __repr__(self) -> str:  # pragma: no cover
        return "<Members %s: %d>" % (self.type_name, len(self.names))


#: BattleUnitModel (verified against dump.cs, build dc33561883ca)
UNIT = Members(
    "BattleUnitModel",
    instance_id="InstanceID",
    origin_id="GetOriginUnitID()",
    hp="Hp",
    max_hp="MaxHp",
    mp="Mp",
    level="Level",
    position="Position",
    is_dead="IsDead()",
    is_actionable="IsActionable()",
    is_break="IsBreak()",
    faction="GetOpponentFaction()",
    is_ally="IsAlly",
    is_opponent="IsOpponent",
    has_keyword="HasUnitKeyword",
    add_passive="AddPassive",
    buffs="GetActivatedBuffModels",
    buff_detail="_buffDetail",
    passive_detail="_passiveDetail",
    change_hp="ChangeHp",
    get_activated_stack="GetActivatedBuffStack",
    get_activated_turn="GetActivatedBuffTurn",
)

#: BuffModel
BUFF = Members(
    "BuffModel",
    stack="GetCurrentStack()",
    max_stack="GetMaxStack()",
    turn="GetCurrentTurn()",
    max_turn="GetMaxTurn()",
    total_stack="GetTotalStack()",
    keyword="GetMainKeyword()",
    keywords="GetKeywordList()",
    is_keyword="IsKeyword",
    name="GetName()",
    buff_type="BuffType",
    vanilla_max_stack="VanilaMaxStack",
    vanilla_max_turn="VanilaMaxTurn",
    set_max_stack_and_turn="SetMaxStackAndTurn",
)

#: SkillModel
SKILL = Members(
    "SkillModel",
    id="GetID()",
    name="GetSkillName()",
    skill_data="skillData",
    coins="CoinList",
    abilities="SkillAbilityList",
    is_attack="IsAttack",
    is_defense="IsDefense",
    coin_count="GetCoinNum()",
    alive_coins="GetAliveCoins",
    coin_by_index="GetCoinByIndex",
    make_coins_dirty="MakeCoinListCacheDirty",
)

#: CoinModel
COIN = Members(
    "CoinModel",
    result="GetCoinResult",
    scale="GetScale()",
    prob="GetProb()",
    is_head="IsHead",
    is_tail="IsTail",
    is_destroyed="IsDestroyed",
    is_success="IsSuccessAttack",
    abilities="CoinAbilityList",
)

#: BattleActionModel
ACTION = Members(
    "BattleActionModel",
    skill="Skill",
    sin="Sin",
    sin_action="SinAction",
    skill_id="GetSkillID()",
    main_target="GetMainTarget",
    targets="GetTargetUnitModelList",
    alive_targets="GetAliveTargetUnitModelList",
)

#: BattleObjectManager
MANAGER = Members(
    "BattleObjectManager",
    all_units="_allUnitDictionary",
    get_model="GetModel",
)

ALL_TABLES = (UNIT, BUFF, SKILL, COIN, ACTION, MANAGER)


def validate_against_index(index, tables=None) -> Dict[str, List[str]]:
    """Returns ``{type: [missing member names]}`` for every table entry the build lacks.

    ``index`` is a :class:`limbuspy.il2cpp.TypeIndex` (or None, in which case nothing is validated).
    """
    problems: Dict[str, List[str]] = {}
    if index is None:
        return problems

    for table in tables or ALL_TABLES:
        missing = [
            "%s (%s)" % (alias, name.removesuffix("()"))
            for alias, name in table.items()
            if not index.has_member(table.type_name, name.removesuffix("()"))
        ]
        if missing:
            problems[table.type_name] = missing
    return problems
