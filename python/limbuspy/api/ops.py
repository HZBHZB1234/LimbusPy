"""由 tools/gen_contract.py 从 ops/spec/api.toml 生成 —— 不要手改。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .types import BuffInfo, BurstInfo, UnitInfo, UnitRefLike, UnitState, to_wire_unit as _wire_unit

__contract_version__ = '1.0'


class BattleOps:
    """battle 封装（A 层）：一次调用 = 一次 crossing，语义在 C# 侧。"""

    __slots__ = ("_session",)

    def __init__(self, session) -> None:
        self._session = session

    def damage_many(self, units: List[Dict[str, Any]], amount: int, source: str = "PASSIVE", timing: str = "ON_SUCCESS_ATTACK") -> List[UnitInfo]:
        """对一组单位同时造成伤害（N 个目标 1 次往返）。

        逐目标调 `unit.damage` 是 N 次往返；这个 op 是 **1 次**。
        单个目标解析失败不影响其它目标（返回列表里那一项 `applied=False`）。

        op: ``battle.damage``（stability=stable）
        :param units: 目标单位引用列表
        :param amount: 伤害点数（正数）
        :param source: 伤害来源枚举名
        :param timing: 事件时机枚举名
        """
        return [UnitInfo.from_wire(row) for row in (self._session.op('battle.damage', {'units': units, 'amount': amount, 'source': source, 'timing': timing}) or [])]

    def enemies(self) -> List[UnitInfo]:
        """敌方单位的扁平快照。

        op: ``battle.enemies``（stability=stable）
        """
        return [UnitInfo.from_wire(row) for row in (self._session.op('battle.enemies', {}) or [])]

    def heal_many(self, units: List[Dict[str, Any]], amount: int, timing: str = "ON_SUCCESS_ATTACK") -> List[UnitInfo]:
        """对一组单位同时治疗（N 个目标 1 次往返）。

        op: ``battle.heal``（stability=stable）
        :param units: 目标单位引用列表
        :param amount: 治疗点数（正数）
        :param timing: 事件时机枚举名
        """
        return [UnitInfo.from_wire(row) for row in (self._session.op('battle.heal', {'units': units, 'amount': amount, 'timing': timing}) or [])]

    def players(self) -> List[UnitInfo]:
        """我方单位的扁平快照。

        op: ``battle.players``（stability=stable）
        """
        return [UnitInfo.from_wire(row) for row in (self._session.op('battle.players', {}) or [])]

    def round(self) -> Optional[int]:
        """当前回合数；拿不到时返回 None。

        op: ``battle.round``（stability=stable）
        """
        return self._session.op('battle.round', {})

    def states(self) -> List[UnitState]:
        """全场单位的富状态一次拿全（unit.state 的批版）。

        **属性组**的落地：遍历一次管理器字典，每个单位多读 buff 数量与等级，全部在同一个
        主线程时间片里完成。全场 11 个单位也只要 **1 次**跨进程往返，而逐个 `unit.state` 是 11 次。

        这是「批量 getter」相对「逐单位读」的核心价值 —— 后者是最容易写出来的 N+1。

        op: ``battle.states``（stability=stable）
        :returns: 每个单位的 UnitState（按 instance_id 升序）
        """
        return [UnitState.from_wire(row) for row in (self._session.op('battle.states', {}) or [])]

    def units(self) -> List[UnitInfo]:
        """全场单位（扁平快照），按 instance_id 升序。

        一次调用拿全场：`[UnitInfo, ...]`。要“敌人/血量最低”这类筛选，在 Python 侧对返回值做即可，
        不必再逐单位往返。

        op: ``battle.units``（stability=stable）
        """
        return [UnitInfo.from_wire(row) for row in (self._session.op('battle.units', {}) or [])]


class ContentOps:
    """content 封装（A 层）：一次调用 = 一次 crossing，语义在 C# 侧。"""

    __slots__ = ("_session",)

    def __init__(self, session) -> None:
        self._session = session

    def plan(self, mod: str, watch: List[str], hooks: List[Dict[str, Any]]) -> bool:
        """把一个 mod 关心的 buff 位图计划告诉内核。

        注册层用它把「这个 mod 的哪些 buff 要在时点回调里判断」告诉 C#。之后补丁回调里
        C# 会按同一份顺序算出 `mask_victim` / `mask_self` 塞进 payload ——
        于是 `victim.has(Buff)` 在 Python 侧只是一次整数按位与，**0 次跨进程往返**。

        `watch` 的下标就是位序号：`watch[i]` 存在 → 第 i 位为 1。
        老内核没有这个 op 时 Python 侧静默跳过，谓词退化为恒假（不报错、不崩）。

        op: ``content.plan``（stability=stable）
        :param mod: 模组名（诊断用）
        :param watch: 关心的 Buff 关键字，下标即位序号
        :param hooks: 每个时点要哪些位：{timing, victim:[位], self:[位]}
        :returns: true = 计划已接受
        """
        return self._session.op('content.plan', {'mod': mod, 'watch': watch, 'hooks': hooks})

    def register(self, keywords: List[str], icons: List[str], icon_root: str = "") -> bool:
        """把自定义 Buff 的关键字真注入游戏枚举，并登记图标。

        这是自定义 Buff 的入口。它不是"一次 API 调用"就完事 —— 自定义 Buff 是
        **三件事的组合**：枚举注入（本 op）、图标补丁（本 op 顺带登记）、本地化（暂未做）。

        id 从 **24576（0x6000）** 起、步长 1。为什么不是 Lethe 的 `4097 + 2k`：原版
        `BUFF_UNIQUE_KEYWORD` 共 2124 个成员、最大 2123 且就是 `None` 哨兵，1000→2123 密排；
        24576 有约 2.2 万个空号，天然跨过哨兵，也能和 Lethe 共存。

        幂等；失败返回 false 而**不抛** —— 注入不可用不该让 mod 起不来。

        op: ``content.register``（stability=stable）
        :param keywords: 要注入的 Buff 关键字（会做大写规范化）
        :param icons: 与 keywords 同序的图标文件名（可空字符串）
        :param icon_root: 图标根目录；空则按相对路径解析
        :returns: true = 注入成功
        """
        return self._session.op('content.register', {'keywords': keywords, 'icons': icons, 'icon_root': icon_root})


class RngOps:
    """rng 封装（A 层）：一次调用 = 一次 crossing，语义在 C# 侧。"""

    __slots__ = ("_session",)

    def __init__(self, session) -> None:
        self._session = session

    def chance(self, permille: int) -> bool:
        """千分位概率判定（permille=300 即 30%）。

        op: ``rng.chance``（stability=stable）
        :param permille: 千分位概率 0..1000
        """
        return self._session.op('rng.chance', {'permille': permille})

    def int(self, min: int = 0, max: int = 100) -> int:
        """闭区间整数随机数（独立随机源，不污染游戏 RNG）。

        op: ``rng.int``（stability=stable）
        :param min: 下界（含）
        :param max: 上界（含）
        """
        return self._session.op('rng.int', {'min': min, 'max': max})

    def pick(self, items: List[str]) -> str:
        """从字符串列表里随机取一个（空列表返回空串）。

        op: ``rng.pick``（stability=stable）
        :param items: 候选列表
        """
        return self._session.op('rng.pick', {'items': items})


class UnitOps:
    """unit 封装（A 层）：一次调用 = 一次 crossing，语义在 C# 侧。"""

    __slots__ = ("_session",)

    def __init__(self, session) -> None:
        self._session = session

    def add_buff(self, unit: UnitRefLike, keyword: str, stacks: int = 1, turns: int = 1, timing: str = "ON_SUCCESS_ATTACK") -> bool:
        """给单位加一个 Buff（实验性：依赖 BuffDetail.AddBuff 的时序语义）。

        走游戏自己的 Buff 施加管线（`BuffDetail.AddBuff`），参数按「层数/回合」的直觉给。

        **实验性**：真实时序（activeRound、history 列表）在真机上需要验证；失败时返回 False 而不是抛错。

        op: ``unit.add_buff``（stability=experimental）
        :param unit: 目标单位
        :param keyword: Buff 关键字
        :param stacks: 层数
        :param turns: 回合数
        :param timing: 事件时机枚举名
        :returns: true = 已施加
        """
        return self._session.op('unit.add_buff', {'unit': _wire_unit(unit), 'keyword': keyword, 'stacks': stacks, 'turns': turns, 'timing': timing})

    def add_passive(self, unit: UnitRefLike, passive_id: int) -> bool:
        """按被动 id 施加一个被动（最安全的“施加效果”入口）。

        op: ``unit.add_passive``（stability=stable）
        :param unit: 目标单位
        :param passive_id: PassiveData 的 id
        :returns: true = 已施加
        """
        return self._session.op('unit.add_passive', {'unit': _wire_unit(unit), 'passive_id': passive_id})

    def add_shield(self, unit: UnitRefLike, value: int, timing: str = "ON_SUCCESS_ATTACK") -> UnitInfo:
        """给单位加护盾（AddShield）。

        op: ``unit.add_shield``（stability=stable）
        :param unit: 目标单位
        :param value: 护盾值（正数）
        :param timing: 事件时机枚举名
        :returns: 加完之后的状态
        """
        return UnitInfo.from_wire(self._session.op('unit.add_shield', {'unit': _wire_unit(unit), 'value': value, 'timing': timing}))

    def break_(self, unit: UnitRefLike, timing: str = "ON_SUCCESS_ATTACK") -> UnitInfo:
        """强制让单位进入崩溃（break）状态。

        `method = "break_"` 是因为 `break` 是 Python 关键字；C# 侧的方法名由生成器还原成 `Break`。

        op: ``unit.break``（stability=stable）
        :param unit: 目标单位
        :param timing: 事件时机枚举名
        :returns: 崩溃之后的状态
        """
        return UnitInfo.from_wire(self._session.op('unit.break', {'unit': _wire_unit(unit), 'timing': timing}))

    def buff_stacks(self, unit: UnitRefLike, keyword: str) -> int:
        """读某关键字 Buff 的当前层数。

        按**主关键字**匹配（等价 Modular 的 `getbuffstack`）。没有该 buff 时返回 0，不报错。

        op: ``unit.buff_stacks``（stability=stable）
        :param unit: 目标单位
        :param keyword: Buff 关键字，如 "SINKING"
        :returns: 层数（0 = 没有）
        """
        return self._session.op('unit.buff_stacks', {'unit': _wire_unit(unit), 'keyword': keyword})

    def buff_turns(self, unit: UnitRefLike, keyword: str) -> int:
        """读某关键字 Buff 的剩余回合。

        op: ``unit.buff_turns``（stability=stable）
        :param unit: 目标单位
        :param keyword: Buff 关键字
        :returns: 剩余回合（0 = 没有）
        """
        return self._session.op('unit.buff_turns', {'unit': _wire_unit(unit), 'keyword': keyword})

    def buffs(self, unit: UnitRefLike) -> List[BuffInfo]:
        """列出单位身上的全部 Buff（扁平，无引用）。

        op: ``unit.buffs``（stability=stable）
        :param unit: 目标单位
        :returns: 每项：keyword/stacks/turns/max_stacks/max_turns/name
        """
        return [BuffInfo.from_wire(row) for row in (self._session.op('unit.buffs', {'unit': _wire_unit(unit)}) or [])]

    def burst_by_buff(self, unit: UnitRefLike, keyword: str, per_stack: int = 3, max_stacks: int = 6, source: str = "PASSIVE") -> BurstInfo:
        """把某关键字的叠层换成一笔伤害（读层数→算伤害→改血，全在一个时间片里完成）。

        **复合 op 的样板**：Python 侧一次调用，C# 侧完成「读层数 → 夹上限 → 算目标 HP → ChangeHp」。
        如果拆成 Python 的多步，中间会被帧边界/其它事件插进来，读到的是旧层数、打到的是旧血量。

        返回 `BurstInfo`：`{stacks, damage, hp_before, hp_after, applied}`。

        op: ``unit.burst_by_buff``（stability=stable）
        :param unit: 目标单位
        :param keyword: Buff 关键字，如 "SINKING"
        :param per_stack: 每层换多少伤害
        :param max_stacks: 参与结算的最大层数
        :param source: 伤害来源枚举名
        :returns: stacks/damage/hp_before/hp_after/applied
        """
        return BurstInfo.from_wire(self._session.op('unit.burst_by_buff', {'unit': _wire_unit(unit), 'keyword': keyword, 'per_stack': per_stack, 'max_stacks': max_stacks, 'source': source}))

    def damage(self, unit: UnitRefLike, amount: int, source: str = "PASSIVE", timing: str = "ON_SUCCESS_ATTACK") -> UnitInfo:
        """对单位造成伤害（内部处理 ChangeHp 的语义与枚举）。

        **这是打伤害的唯一推荐入口。** `ChangeHp` 的 `value` 是「目标 HP 值」而不是增量、还要补齐
        枚举与哨兵值——这些坑都在 C# 侧封掉了。

        返回伤害后的 `UnitInfo`（省一次读）；`applied=False` 表示没有实际改动（已死/无效目标）。

        op: ``unit.damage``（stability=stable）
        :param unit: 目标单位
        :param amount: 伤害点数（正数）
        :param source: 伤害来源枚举名
        :param timing: 事件时机枚举名
        :returns: 伤害后的状态
        """
        return UnitInfo.from_wire(self._session.op('unit.damage', {'unit': _wire_unit(unit), 'amount': amount, 'source': source, 'timing': timing}))

    def has_keyword(self, unit: UnitRefLike, keyword: str) -> bool:
        """单位是否带某个单位关键字（UNIT_KEYWORD）。

        op: ``unit.has_keyword``（stability=stable）
        :param unit: 目标单位
        :param keyword: 单位关键字名，如 "SINKING"
        """
        return self._session.op('unit.has_keyword', {'unit': _wire_unit(unit), 'keyword': keyword})

    def heal(self, unit: UnitRefLike, amount: int, timing: str = "ON_SUCCESS_ATTACK") -> UnitInfo:
        """治疗单位（走游戏自己的回复管线，不超上限）。

        op: ``unit.heal``（stability=stable）
        :param unit: 目标单位
        :param amount: 治疗点数（正数）
        :param timing: 事件时机枚举名
        :returns: 治疗后的状态
        """
        return UnitInfo.from_wire(self._session.op('unit.heal', {'unit': _wire_unit(unit), 'amount': amount, 'timing': timing}))

    def info(self, unit: UnitRefLike) -> UnitInfo:
        """读单位的基础状态，一次 crossing 拿全。

        返回扁平 dict（`UnitInfo`），不含任何引用：适合记账、判断、日志。

        这是读状态的**首选**：比逐个读 `hp`/`max_hp`/`is_dead` 省 N-1 次往返。

        op: ``unit.info``（stability=stable）
        :param unit: 目标单位
        :returns: hp/max_hp/mp/position/is_dead/is_break/faction/instance_id/origin_id
        """
        return UnitInfo.from_wire(self._session.op('unit.info', {'unit': _wire_unit(unit)}))

    def refresh_speed(self, unit: UnitRefLike) -> UnitInfo:
        """只重算速度与行动顺序，不改值。

        op: ``unit.refresh_speed``（stability=stable）
        :param unit: 目标单位
        """
        return UnitInfo.from_wire(self._session.op('unit.refresh_speed', {'unit': _wire_unit(unit)}))

    def set_hp(self, unit: UnitRefLike, hp: int, source: str = "PASSIVE", timing: str = "ON_SUCCESS_ATTACK") -> UnitInfo:
        """把单位 HP 直接设为某个值（会走 OnChangeHp 通知）。

        等价 `ChangeHp(target_hp, ...)`。扣血请优先用 `damage()`，它更不容易写错。

        op: ``unit.set_hp``（stability=stable）
        :param unit: 目标单位
        :param hp: 目标 HP 值（不是增量）
        :param source: 伤害来源枚举名
        :param timing: 事件时机枚举名
        """
        return UnitInfo.from_wire(self._session.op('unit.set_hp', {'unit': _wire_unit(unit), 'hp': hp, 'source': source, 'timing': timing}))

    def set_sp(self, unit: UnitRefLike, sp: int) -> UnitInfo:
        """把单位理智（SP/MP）设为某个值，走游戏自己的 ChangeMp。

        返回改完之后的 `unit.state`（省一次读）；失败时 `applied=False`。

        负数理智表示恐慌计数，`ChangeMp` 接受负值。

        op: ``unit.set_sp``（stability=stable）
        :param unit: 目标单位
        :param sp: 目标理智值（可负）
        :returns: 改完之后的状态
        """
        return UnitInfo.from_wire(self._session.op('unit.set_sp', {'unit': _wire_unit(unit), 'sp': sp}))

    def set_speed(self, unit: UnitRefLike, value: int) -> UnitInfo:
        """改单位速度并重算行动顺序（SetSpeed + RefreshSpeed）。

        只改值不刷新的话，行动顺序要等下一回合才生效 —— 这个坑在 C# 侧封掉了。

        op: ``unit.set_speed``（stability=stable）
        :param unit: 目标单位
        :param value: 新的速度值
        :returns: 改完之后的状态
        """
        return UnitInfo.from_wire(self._session.op('unit.set_speed', {'unit': _wire_unit(unit), 'value': value}))

    def state(self, unit: UnitRefLike) -> UnitState:
        """读单位状态（info 的超集：多带 buff 数量与等级），一次 crossing。

        `unit.info` 的加料版：多返回 `buff_count` 与 `level`。仍然是**一次**往返。

        为什么不把所有属性都塞进来：每多读一个字段就多一次游戏侧调用。要全景请用 B 层，
        这里只收"判断时几乎一定要看"的那几个。

        op: ``unit.state``（stability=stable）
        :param unit: 目标单位
        :returns: info 的全部字段 + buff_count / level
        """
        return UnitState.from_wire(self._session.op('unit.state', {'unit': _wire_unit(unit)}))



class Ops:
    """A 层入口：``ops.unit.damage(...)`` / ``ops.battle.units()``。"""

    __slots__ = ("battle", "content", "rng", "unit")

    def __init__(self, session) -> None:
        self.battle = BattleOps(session)
        self.content = ContentOps(session)
        self.rng = RngOps(session)
        self.unit = UnitOps(session)


def ops(session) -> Ops:
    """给一个 session 造 A 层入口（通常用 ``ctx.ops`` 就够了）。"""
    return Ops(session)
