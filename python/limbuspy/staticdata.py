"""静态数据行生成：把注册的内容变成游戏静态数据表认得的一行。

**为什么需要这一层**：注册一个 Buff 不只是"给它一个名字"。游戏的 ``BuffStaticData``
有 31 个字段（id / 图标 / 分类 / 属抗 / 层数上限 / 各种销毁条件 / 重要度 ……），
少填一个有意义的字段，轻则表现不对，重则进战斗就崩。

**字段名从哪来**：全部取自最新全量导出
``il2csharp/out/limbus/full0/Assembly-CSharp/{Buff,Passive,Skill,Ego,Personality}StaticData.cs``
的私有字段表（``// 0xNN`` 偏移行）。**不猜**：写这个模块时每一个键都能在导出里逐字查到。

两层能力：

1. **完整生成**（``buff_row`` / ``passive_row``）—— 这两张表的字段我们都建模了，
   生成的行可以直接进内容包。
2. **模板继承**（``skill_template`` / ``ego_template`` / ``personality_template``）——
   技能/EGO/人格的数据面（硬币列表、ability 链、立绘）远超我们建模的范围，
   所以只产出「以某个官方 id 为模板 + 覆盖哪些字段」的声明，
   真正的行由加载器在运行时用模板行 + 覆盖值拼出来。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "TABLE_NAMES",
    "buff_row",
    "passive_row",
    "skill_template",
    "ego_template",
    "personality_template",
    "collect",
]

#: 内容种类 → 游戏静态数据表名。键与 ``Content.kind`` 对齐。
TABLE_NAMES = {
    "buff": "BuffData",
    "passive": "PassiveData",
    "skill": "SkillData",
    "ego": "EgoData",
    "identity": "PersonalityData",
}

#: ``BuffStaticData`` 的各销毁条件默认值。
#:
#: 依据：``BuffStaticData.cs`` 的 6 个 ``destroyable*`` 布尔。默认按"最常见的普通 Buff"
#: 取 —— 层数归零就消失、回合归零也消失、单位死亡/退场也消失。
_BUFF_DESTROY_DEFAULTS = {
    "destroyableOnZero": True,
    "destroyableOnZeroTurn": True,
    "destroyableOnBothZero": False,
    "destroyableOnDie": True,
    "destroyableOnRetreat": True,
}


def buff_row(content: Any) -> Dict[str, Any]:
    """把一个 ``Buff`` 内容类生成成 ``BuffStaticData`` 的一行。

    Args:
        content: 已注册的 ``Buff`` 子类（需要 ``owner`` / ``bit`` 已回填）。

    Returns:
        键名与 ``BuffStaticData`` 的字段一致的字典。**可以只填一部分** ——
        游戏的加载器对缺键会保留默认值，但填全更安全。

    Example::

        class Drowning(lp.ContentBuff):
            local_id, key, name = "drowning", "DROWNING", "溺水"
            max_stacks, max_turns = 10, 5

        row = lm_buff_row(Drowning)
        row["id"]              # "DROWNING"
        row["maxStack"]        # 10
    """
    keyword = str(content.keyword())
    icon = str(getattr(content, "icon", "") or "")
    row: Dict[str, Any] = {
        # 字符串 id 就是枚举名；游戏用它反查 BUFF_UNIQUE_KEYWORD
        "id": keyword,
        "subIdList": list(getattr(content, "sub_ids", ()) or ()),
        "iconId": icon or keyword.lower(),
        "typoIconId": str(getattr(content, "typo_icon", "") or icon or keyword.lower()),
        # buffClass / buffType / attributeType 三个字符串会被游戏各自的 _xxx 字段接手解析
        "buffClass": str(getattr(content, "buff_class", "Neutral") or "Neutral"),
        "buffType": str(getattr(content, "buff_type", "Neutral") or "Neutral"),
        "attributeType": str(getattr(content, "attribute", "NONE") or "NONE"),
        "totalStack": int(getattr(content, "total_stack", 0) or 0),
        "stackLimitPerTurn": int(getattr(content, "stack_limit_per_turn", 0) or 0),
        "maxStack": int(getattr(content, "max_stacks", 99) or 99),
        "maxTurn": int(getattr(content, "max_turns", 99) or 99),
        "canBeDespelled": bool(getattr(content, "can_be_despelled", True)),
        "canBeDuplicated": bool(getattr(content, "can_be_duplicated", False)),
        "useTrigger": bool(getattr(content, "use_trigger", False)),
        "categoryKeywordList": list(getattr(content, "categories", ()) or ()),
        "playerImportanceLevel": int(getattr(content, "player_importance", 0) or 0),
        "enemyImportanceLevel": int(getattr(content, "enemy_importance", 0) or 0),
        "syncOnBossRaid": bool(getattr(content, "sync_on_boss_raid", False)),
        "canIgnoreGiveAdder": bool(getattr(content, "can_ignore_give_adder", False)),
        # 能力列表留空：BuffAbilityStaticData 的结构我们没建模，留空等于"没有额外能力"
        "list": list(getattr(content, "abilities", ()) or ()),
    }

    for key, fallback in _BUFF_DESTROY_DEFAULTS.items():
        row[key] = bool(getattr(content, key.lower(), fallback))

    return row


def passive_row(content: Any) -> Dict[str, Any]:
    """把一个 ``Passive`` 内容类生成成 ``PassiveStaticData`` 的一行。

    ``PassiveStaticData`` 一共只有 7 个字段，且核心就是那个整数 ``id`` ——
    所以被动是**最容易落地**的一类内容（配合 ``unit.add_passive`` 直接生效）。

    Args:
        content: 已注册的 ``Passive`` 子类。

    Returns:
        键名与 ``PassiveStaticData`` 的字段一致的字典。

    Raises:
        ValueError: 没有填 ``passive_id``（我们无法凭空发明一个合法的被动 id）。
    """
    passive_id = int(getattr(content, "passive_id", 0) or 0)
    if passive_id <= 0:
        raise ValueError(
            "%s: 生成被动行需要 passive_id（官方 PassiveData 的 id）。"
            "我们不发明 id —— 那个空间由游戏独占。" % getattr(content, "__name__", content))

    return {
        "id": passive_id,
        "requireIDList": list(getattr(content, "require_ids", ()) or ()),
        "importanceLevel": int(getattr(content, "importance_level", 0) or 0),
        "controlValueList": list(getattr(content, "control_values", ()) or ()),
        "useUnlock": bool(getattr(content, "use_unlock", False)),
        "attributeResonanceCondition": list(getattr(content, "resonance_conditions", ()) or ()),
        "attributeStockCondition": list(getattr(content, "stock_conditions", ()) or ()),
    }


def skill_template(content: Any) -> Dict[str, Any]:
    """把一个 ``Skill`` 内容类生成成「模板继承」声明。

    技能的数据面（硬币列表、命中效果、ability 链）远超我们建模的范围，
    而且**从零构造一个技能在游戏里必然出问题**。所以这里只产出声明：

    ``{"table": "SkillData", "base": <模板 id>, "overrides": {...}}``

    加载器会用模板行合并 ``overrides`` 得到真正的行。

    Args:
        content: ``Skill`` 子类（``base_game_id`` 必填）。

    Returns:
        模板继承声明。

    Raises:
        ValueError: 缺 ``base_game_id``。
    """
    base = int(getattr(content, "base_game_id", 0) or 0)
    if base <= 0:
        raise ValueError(
            "%s: 生成技能需要 base_game_id（模板技能 id）。"
            "不支持从零造技能 —— 漏一个字段就等于造了个坏技能。" % getattr(content, "__name__", content))

    overrides: Dict[str, Any] = {}
    # 只覆盖我们敢覆盖的字段（其余一律沿用模板）
    for key, attr in (
        ("skillTier", "skill_tier"),
        ("skillType", "skill_type"),
        ("canReuse", "can_reuse"),
        ("isSupportSkill", "is_support_skill"),
        ("isHiddenForUI", "hidden"),
    ):
        value = getattr(content, attr, None)
        if value is not None:
            overrides[key] = value

    return {"table": TABLE_NAMES["skill"], "base": base, "overrides": overrides}


def ego_template(content: Any) -> Dict[str, Any]:
    """把一个 ``Ego`` 内容类生成成模板继承声明（同 :func:`skill_template`）。

    EGO 有 26 个字段（属抗列表、需求列表、腐蚀段、觉醒/装备被动、技能 id ……），
    没有模板就不可能有合法的行。
    """
    base = int(getattr(content, "base_game_id", 0) or 0)
    if base <= 0:
        raise ValueError(
            "%s: 生成 EGO 需要 base_game_id（模板 EGO id）。" % getattr(content, "__name__", content))

    overrides: Dict[str, Any] = {}
    for key, attr in (
        ("characterId", "character_id"),
        ("egoType", "ego_type"),
        ("season", "season"),
        ("maxEgoSyncLevel", "max_sync_level"),
    ):
        value = getattr(content, attr, None)
        if value is not None:
            overrides[key] = value

    return {"table": TABLE_NAMES["ego"], "base": base, "overrides": overrides}


def personality_template(content: Any) -> Dict[str, Any]:
    """把一个 ``Identity``（人格）内容类生成成模板继承声明。

    人格的 ``PersonalityStaticData`` 只有 12 个字段，但它指向的立绘/技能表都在别处，
    所以同样是「以官方人格为模板 + 覆盖字段」。
    """
    base = int(getattr(content, "base_game_id", 0) or 0)
    if base <= 0:
        raise ValueError(
            "%s: 生成人格需要 base_game_id（模板人格 id）。"
            "不支持从零造人格 —— 立绘/技能表/数值曲线缺一项就会崩选人界面。"
            % getattr(content, "__name__", content))

    overrides: Dict[str, Any] = {}
    for key, attr in (
        ("characterId", "character_id"),
        ("season", "season"),
        ("rank", "rank"),
        ("uniqueAttribute", "unique_attribute"),
        ("isPlusCoinUnit", "is_plus_coin_unit"),
    ):
        value = getattr(content, attr, None)
        if value is not None:
            overrides[key] = value

    return {"table": TABLE_NAMES["identity"], "base": base, "overrides": overrides}


def collect(contents: List[Any]) -> Dict[str, List[Dict[str, Any]]]:
    """把一个 mod 的全部内容按表名分组，产出可直接写盘的内容包。

    Args:
        contents: 已注册的 ``Content`` 子类列表。

    Returns:
        ``{"BuffData": [行...], "PassiveData": [...], "SkillData": [...], ...}``

    Note:
        Buff/Passive 是**完整行**；Skill/Ego/Personality 是**模板继承声明**
        （见各 ``*_template`` 的说明）。两者混在同一张表里，加载器按有没有
        ``"base"`` 键来区分。
    """
    tables: Dict[str, List[Dict[str, Any]]] = {}
    for content in contents:
        kind = str(getattr(content, "kind", ""))
        try:
            if kind == "buff":
                row: Optional[Dict[str, Any]] = buff_row(content)
            elif kind == "passive":
                row = passive_row(content)
            elif kind == "skill":
                row = skill_template(content)
            elif kind == "ego":
                row = ego_template(content)
            elif kind == "identity":
                row = personality_template(content)
            else:
                row = None
        except ValueError:
            # 模板缺失这类问题是**配置错误**，由 validate() 在更早的地方报过了；
            # 这里静默跳过，免得一个写错的内容毁掉整个内容包。
            continue

        if row is None:
            continue
        tables.setdefault(TABLE_NAMES.get(kind, kind), []).append(row)

    return tables
