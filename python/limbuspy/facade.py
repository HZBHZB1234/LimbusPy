"""作者门面：``import limbuspy as lp`` 之后看到的全部东西。

设计公理（见 ``docs/plans/00-master-plan.md`` §3）：

1. **往返发生在「获取」的时候，不发生在「读属性」的时候。**
   ``u = lp.unit(201)`` 是一次往返；之后 ``u.hp`` / ``u.hp_ratio`` / ``u.is_break``
   **全部 0 次往返**。这样 N+1 在形状上就不存在，也不会再有"属性看起来免费"的错觉。
2. **作者视野里没有 session / bridge / handle / ref / ctx。** 只有函数、值对象、属性。
3. **失败一律降级**：读不到返回 ``None``，动作没生效返回 ``ok=False + reason``，
   只有编程错误（参数类型错）才抛异常。
4. **模块级只放函数和类**，值一律挂在对象属性上（``lp.battle().round``，不是 ``lp.round``）：
   模块属性只能靠 PEP 562 ``__getattr__``，而 pyright/mypy 不追踪它 ——
   这与"静态检查器友好"直接冲突。

名字预算：教程级名字 ≤ 15 个（``docs/plans/00-master-plan.md`` §3 的判据）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .api.types import BuffInfo, BurstInfo, UnitInfo, UnitState, to_wire_unit

__all__ = [
    # 教程级（下面 TUTORIAL 里的 15 个 —— 入门文档只准出现这些）
    "status",
    "battle",
    "units",
    "unit",
    "player",
    "round",
    "buff_stacks",
    "damage",
    "heal",
    "set_hp",
    "set_sp",
    "add_shield",
    "set_speed",
    "refresh_speed",
    "break_unit",
    "add_buff",
    "add_passive",
    "burst",
    "on_hit",
    "on_damage",
    "on_damage_taken",
    "on_death",
    "on_break",
    "on_turn_start",
    "on_round_start",
    # 值对象
    "Status",
    "Battle",
    "Unit",
    "Buff",
    "ActionResult",
    "BurstResult",
    "UnitInfo",
    "UnitState",
    "BuffInfo",
    "BurstInfo",
    # 生命周期（框架用，作者不用碰）
    "attach",
    "detach",
]

#: 教程级名字（入门文档里只准出现这 15 个）。
#:
#: 这条约束是 API 设计的守门员：名字一多，作者就要翻文档找"这次该用哪个"，
#: 而那正是我们要消灭的心智负担。新增名字前先问：能不能挂在已有的值对象上？
TUTORIAL = (
    "status", "battle", "unit", "units", "player",
    "damage", "heal", "add_buff", "buff_stacks",
    "on_hit", "on_damage_taken", "on_death", "on_turn_start",
    "burst", "round",
)


# ======================================================================================
# 运行时挂载点：门面自己不管理连接，由 ModRuntime 挂上来
# ======================================================================================

_bridge: Any = None
_session: Any = None


def attach(bridge: Any) -> None:
    """把门面接到一个已连接的 bridge 上（由 ``ModRuntime.start()`` 调用）。

    这是框架内部接口。模组作者**不需要**调用它，也不该持有 bridge ——
    门面存在的意义就是让作者看不见这一层。
    """
    global _bridge, _session
    _bridge = bridge
    _session = None


def detach() -> None:
    """断开（热重载 / 关闭时由运行时调用）。之后所有读操作返回 ``None``。"""
    global _bridge, _session
    _bridge = None
    _session = None


def _api():
    """拿到当前的 A 层入口；不可用时返回 ``None``（所有调用方都必须处理 None）。"""
    global _session
    if _bridge is None:
        return None
    if _session is None:
        try:
            from .game.session import Session

            _session = Session(_bridge, use_cache=False)
        except Exception:  # noqa: BLE001 - 门面绝不能因为降级路径本身而炸
            return None
    return _session


def _call(op: str, args: Dict[str, Any]) -> Any:
    """调一个 op；A 层不可用或调用失败时返回 ``None``（不抛）。"""
    session = _api()
    if session is None:
        return None
    try:
        return session.op(op, args)
    except Exception:  # noqa: BLE001 - 见模块文档第 3 条：失败降级，不炸
        return None


# ======================================================================================
# 状态
# ======================================================================================


@dataclass(frozen=True)
class Status:
    """内核与 A 层是否可用。``lp.status()`` 的返回值。

    作者用它做一次启动自检，然后就可以不管了::

        st = lp.status()
        if not st.ops:
            lp.log_once("LimbusPy.Ops 没加载上，A 层不可用（B 层仍然可用）")
    """

    #: 是否连上了内核
    connected: bool = False
    #: 内核版本
    kernel: str = ""
    #: 游戏的 IL2CPP 构建号
    game_build: str = ""
    #: 契约版本
    contract: str = ""
    #: A 层（curated ops）是否可用
    ops: bool = False
    #: A 层实现名（通常是 ``limbuspy.ops``）
    provider: str = ""
    #: A 层自检发现的问题（空 = 一切正常）
    problems: tuple = ()

    def __bool__(self) -> bool:
        return self.connected


def status() -> Status:
    """读内核与 A 层可用性。**永远不抛** —— 拿不到就返回 ``connected=False``。

    这是唯一不需要关心降级的调用：它本身就是用来判断"要不要降级"的。

    Cost:
        0 次往返（读的是连接时缓存的握手信息）。
    """
    bridge = _bridge
    if bridge is None:
        return Status()
    try:
        hello = getattr(bridge, "hello", None) or {}
        manifest = {}
        getter = getattr(bridge, "ops_manifest", None)
        if callable(getter):
            manifest = getter() or {}
        elif isinstance(hello.get("ops"), dict):
            manifest = hello["ops"]
        problems = manifest.get("problems") or []
        return Status(
            connected=True,
            kernel=str(hello.get("kernel") or ""),
            game_build=str(hello.get("game_build") or ""),
            contract=str(hello.get("contract") or ""),
            ops=bool(manifest.get("available")),
            provider=str(manifest.get("provider") or ""),
            problems=tuple(problems),
        )
    except Exception:  # noqa: BLE001
        return Status()


# ======================================================================================
# 值对象
# ======================================================================================


@dataclass(frozen=True)
class Buff:
    """一个 Buff 的**快照**（层数/回合/关键字/名字）。

    它是值不是句柄：拿到手之后数值不会自己变。要最新值就重新 ``u.buffs``。
    """

    keyword: str = ""
    """英文关键字（``"SINKING"``）—— 写逻辑用这个。"""

    name: str = ""
    """本地化名字（``"沉没"``）—— 给玩家看用这个。"""

    stacks: int = 0
    """当前层数。"""

    turns: int = 0
    """剩余回合。"""

    max_stacks: int = 0
    max_turns: int = 0

    @classmethod
    def from_info(cls, info: BuffInfo) -> "Buff":
        return cls(
            keyword=info.keyword,
            name=info.name,
            stacks=info.stacks,
            turns=info.turns,
            max_stacks=info.max_stacks,
            max_turns=info.max_turns,
        )

    @property
    def stack_ratio(self) -> float:
        """层数占比 0..1（上限未知时返回 0.0）。"""
        return (self.stacks / self.max_stacks) if self.max_stacks else 0.0

    def is_keyword(self, keyword: str) -> bool:
        """这个 Buff 的主关键字是不是 ``keyword``（大小写不敏感）。"""
        return str(self.keyword).upper() == str(keyword).upper()

    def __str__(self) -> str:
        return "%s x%d (%d 回合)" % (self.keyword or "?", self.stacks, self.turns)


class Unit:
    """一个战斗单位。

    **它是一个快照，不是活句柄。** 构造时（``lp.unit(...)`` / ``lp.units()`` /
    ``lp.battle()``）就用一次往返把所有常用字段读回来了，之后每个属性都是内存读 ——
    0 次往返。

    代价是「快照会过期」：血量在两次读之间被别的效果改了，你手上的 ``u.hp`` 还是旧值。
    要最新状态就用 ``u.refresh()`` 或重新 ``lp.unit(id)``。

    Example::

        u = lp.unit(201)          # 1 次往返
        print(u.hp, u.hp_ratio)   # 0 次
        if u.is_break:            # 0 次
            lp.damage(u, 12)      # 1 次（写操作必然是一次往返）
    """

    __slots__ = ("_info", "_buffs", "_wire")

    def __init__(self, info: Optional[UnitInfo] = None, buffs: Optional[List[Buff]] = None,
                 wire: Optional[Dict[str, Any]] = None) -> None:
        self._info: UnitInfo = info or UnitInfo()
        self._buffs: Optional[List[Buff]] = buffs
        if wire:
            self._wire: Optional[Dict[str, Any]] = dict(wire)
        else:
            self._wire = (
                {"instance_id": self._info.instance_id} if self._info.instance_id else None
            )

    @property
    def wire(self) -> Dict[str, Any]:
        """内核认的单位引用（门面内部/op 调用用；作者不用碰）。"""
        return self._ref()

    # -- 身份 ---------------------------------------------------------------------------

    @property
    def instance_id(self) -> int:
        """战斗内稳定编号（跨回合不变，适合记账）。"""
        return self._info.instance_id

    @property
    def origin_id(self) -> int:
        """人格/角色的静态 ID（例如浮士德魁首 = 10212）。"""
        return self._info.origin_id

    @property
    def faction(self) -> str:
        """阵营枚举名。"""
        return self._info.faction

    @property
    def is_player(self) -> bool:
        """是否玩家方单位。"""
        return self._info.is_player

    @property
    def position(self) -> str:
        """站位枚举名。"""
        return self._info.position

    @property
    def level(self) -> int:
        """等级（``unit.state`` 才带；``unit.info`` 拿到的快照是 0）。"""
        return getattr(self._info, "level", 0)

    # -- 数值 ---------------------------------------------------------------------------

    @property
    def hp(self) -> int:
        """当前血量。"""
        return self._info.hp

    @property
    def max_hp(self) -> int:
        """最大血量。"""
        return self._info.max_hp

    @property
    def mp(self) -> int:
        """理智（负数表示恐慌计数）。"""
        return self._info.mp

    @property
    def hp_ratio(self) -> float:
        """血量比例 0..1。"""
        return self._info.hp_ratio

    @property
    def missing_hp(self) -> int:
        """还差多少血才满。"""
        return self._info.missing_hp

    # -- 状态 ---------------------------------------------------------------------------

    @property
    def is_dead(self) -> bool:
        """是否已阵亡。"""
        return self._info.is_dead

    @property
    def is_break(self) -> bool:
        """是否处于崩溃（break）状态。"""
        return self._info.is_break

    @property
    def alive(self) -> bool:
        """还活着（``is_dead`` 的反面，读起来更顺）。"""
        return not self._info.is_dead

    @property
    def buff_count(self) -> int:
        """身上有几个 Buff。"""
        if self._buffs is not None:
            return len(self._buffs)
        return int(getattr(self._info, "buff_count", 0) or 0)

    # -- Buff ---------------------------------------------------------------------------

    @property
    def buffs(self) -> List[Buff]:
        """身上的全部 Buff（**惰性读**：第一次访问会花 1 次往返，之后缓存）。

        遍历过程中不会自动刷新 —— 这是刻意的：一次遍历里数值跳来跳去更让人困惑。
        """
        if self._buffs is None:
            raw = _call("unit.buffs", {"unit": self._ref()})
            self._buffs = [Buff.from_info(BuffInfo.from_wire(row)) for row in (raw or [])]
        return list(self._buffs)

    def buff(self, keyword: str) -> Optional[Buff]:
        """按关键字找 Buff，没有就返回 ``None``。

        Example::

            b = u.buff("SINKING")
            if b is not None:
                print(b.stacks, b.turns)
        """
        wanted = str(keyword).upper()
        for buff in self.buffs:
            if buff.is_keyword(wanted):
                return buff
        return None

    def buff_stack(self, keyword: str) -> int:
        """按关键字读层数，没有返回 0。

        与 ``u.buff(k).stacks`` 的区别：这里**只花一次往返**（``unit.buff_stacks``），
        而后者要把整张 buff 表拉回来。
        """
        value = _call("unit.buff_stacks", {"unit": self._ref(), "keyword": keyword})
        return int(value or 0)

    def has_buff(self, keyword: str) -> bool:
        """身上有没有某个 Buff。"""
        return self.buff_stack(keyword) > 0

    # -- 刷新 / 逃生口 ------------------------------------------------------------------

    def refresh(self) -> "Unit":
        """重新读一次快照并原地更新（返回 ``self`` 方便链式）。

        Example::

            u.refresh()
            print(u.hp)
        """
        raw = _call("unit.state", {"unit": self._ref()})
        if isinstance(raw, dict):
            self._info = UnitState.from_wire(raw)
        self._buffs = None
        return self

    def state(self) -> UnitState:
        """重新读一次富状态（``unit.state``：多带 ``buff_count`` 与 ``level``）。

        Cost: 1 次往返。
        """
        raw = _call("unit.state", {"unit": self._ref()})
        if isinstance(raw, dict):
            return UnitState.from_wire(raw)
        return UnitState()

    def _ref(self) -> Dict[str, Any]:
        return self._wire or {"instance_id": self.instance_id}

    @property
    def raw(self) -> "RawUnit":
        """逃生口：直接走 B 层反射读任意成员（教程里一个字都不提）。

        Example::

            u.raw.get("Hp")                       # 读一个生成绑定没覆盖的成员
            u.raw.call("RefreshSpeed")            # 调一个方法
        """
        return RawUnit(self)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Unit) and other.instance_id == self.instance_id

    def __hash__(self) -> int:
        return hash(self.instance_id)

    def __str__(self) -> str:
        return "Unit(#%d %s %d/%d%s)" % (
            self.instance_id, "PLAYER" if self.is_player else "ENEMY",
            self.hp, self.max_hp, " DEAD" if self.is_dead else "",
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return self.__str__()


class RawUnit:
    """``u.raw``：唯一的反射逃生口。

    这里出现的名字就是 IL2CPP 成员名，没有类型信息，拼错在运行期才报错 ——
    所以它不该出现在正常代码路径上。
    """

    __slots__ = ("_unit",)

    def __init__(self, unit: Unit) -> None:
        self._unit = unit

    def get(self, member: str) -> Any:
        """读一个成员（属性/字段/无参方法）。"""
        session = _api()
        if session is None:
            return None
        try:
            return session.read(self._unit._ref(), member)
        except Exception:  # noqa: BLE001
            return None

    def set(self, member: str, value: Any) -> bool:
        """写一个成员。"""
        session = _api()
        if session is None:
            return False
        try:
            session.write(self._unit._ref(), member, value)
            return True
        except Exception:  # noqa: BLE001
            return False

    def call(self, member: str, *args: Any) -> Any:
        """调一个方法。"""
        session = _api()
        if session is None:
            return None
        try:
            return session.invoke(self._unit._ref(), member, *args)
        except Exception:  # noqa: BLE001
            return None


class Battle:
    """一个时点的战场快照：回合数 + 全场单位。

    一次 ``lp.battle()`` 只花 1 次往返（``battle.units``）就能拿回全场单位，
    之后 ``b.round`` / ``b.players`` / ``b.enemies`` 全部 0 次。
    """

    __slots__ = ("_round", "_units")

    def __init__(self, round_: Optional[int] = None, units: Optional[List[Unit]] = None) -> None:
        self._round = round_
        self._units: List[Unit] = list(units or [])

    @property
    def round(self) -> Optional[int]:
        """当前回合数；拿不到时 ``None``（不是 0 —— 0 会被误当成"第一回合"）。"""
        return self._round

    @property
    def units(self) -> List[Unit]:
        """全场单位（含已阵亡）。"""
        return list(self._units)

    @property
    def players(self) -> List[Unit]:
        """我方单位。"""
        return [u for u in self._units if u.is_player]

    @property
    def enemies(self) -> List[Unit]:
        """敌方单位。"""
        return [u for u in self._units if not u.is_player]

    @property
    def alive(self) -> List[Unit]:
        """还活着的单位。"""
        return [u for u in self._units if not u.is_dead]

    def unit(self, instance_id: int) -> Optional[Unit]:
        """按 instance_id 找单位。"""
        wanted = int(instance_id)
        for item in self._units:
            if item.instance_id == wanted:
                return item
        return None

    def of_origin(self, origin_id: int) -> List[Unit]:
        """按人格/角色 ID 找单位（同一角色可能有分身，所以返回列表）。"""
        wanted = int(origin_id)
        return [item for item in self._units if item.origin_id == wanted]

    def __len__(self) -> int:
        return len(self._units)

    def __iter__(self):
        return iter(self._units)

    def __str__(self) -> str:
        return "Battle(round=%s, %d 单位 / %d 存活)" % (
            self._round, len(self._units), len(self.alive))


@dataclass(frozen=True)
class ActionResult:
    """一次写操作的结果。

    ``ok=False`` 不是异常：可能是目标已死、A 层不可用、或游戏拒绝了这次改动。
    ``reason`` 说明原因（给日志用），``state`` 是改动之后的状态（可能为 ``None``）。

    Example::

        r = lp.damage(u, 18)
        if not r.ok:
            print("没打中:", r.reason)
    """

    ok: bool = False
    """是否真的改动了游戏状态。"""

    reason: str = ""
    """``ok=False`` 时的原因。"""

    state: Optional[UnitInfo] = None
    """改动之后的单位状态（``ok=False`` 时可能为 ``None``）。"""


@dataclass(frozen=True)
class BurstResult(ActionResult):
    """``lp.burst()`` 的结算明细（是 ``ActionResult`` 的子类，所以用法完全兼容）。

    Example::

        r = lp.burst(enemy, "SINKING", per_stack=3, max_stacks=6)
        if r.ok:
            print("%d 层 → %d 伤害（HP %d → %d）" % (r.stacks, r.damage, r.hp_before, r.hp_after))
    """

    stacks: int = 0
    """参与结算的层数（已被 ``max_stacks`` 夹过）。"""

    damage: int = 0
    """实际打出的伤害。"""

    hp_before: int = 0
    hp_after: int = 0


# ======================================================================================
# 读
# ======================================================================================


def _to_unit(info: Optional[UnitInfo]) -> Optional[Unit]:
    if info is None:
        return None
    return Unit(info)


def units() -> List[Unit]:
    """全场单位（一次往返）。

    等价于 ``lp.battle().units``。要同时用回合数就用 ``lp.battle()``，别调两次。

    Cost: 1 次往返。

    Returns:
        单位列表（可能为空列表 —— 不在战斗里时就是空的，不是 ``None``）。
    """
    # 走 battle.states 而不是 battle.units：同样一次往返，但每个 Unit 天然带
    # level / buff_count（UnitState 是 UnitInfo 的超集），于是 u.level 不是假值。
    raw = _call("battle.states", {})
    return [_to_unit(UnitState.from_wire(row)) for row in (raw or [])]


def battle() -> Battle:
    """战场快照：回合数 + 全场单位。

    这是"一次拿全部"的首选入口 —— 逐单位调 ``lp.unit()`` 是 N 次往返，
    ``lp.battle()`` 是 1 次。

    Cost: 1～2 次往返（单位 1 次，回合数 1 次；两者都拿不到时是 0 次）。

    Returns:
        ``Battle``（永远不是 ``None``；不在战斗里时 ``round is None``、``units`` 为空）。
    """
    round_ = _call("battle.round", {})
    raw = _call("battle.states", {})
    return Battle(
        round_=int(round_) if isinstance(round_, int) else None,
        units=[_to_unit(UnitState.from_wire(row)) for row in (raw or [])],
    )


def unit(target: Any, *, buffs: bool = False) -> Optional[Unit]:
    """取一个单位的快照（一次往返）。

    这是**唯一**的取快照入口，``target`` 接受作者手上会有的任何一种写法::

        lp.unit(201)            # 战斗内稳定编号（自己记账时最常用）
        lp.unit(ev.target)      # 时点 payload 里的单位引用 —— 事件回调里最常用
        lp.unit(some_unit)      # 已经是 Unit 就原样返回

    Args:
        target: ``instance_id`` / 时点 payload 里的单位引用 / 已有的 ``Unit``。
        buffs: 为 ``True`` 时同时把 Buff 表拉回来（多 1 次往返），
            让 ``u.buffs`` 之后完全免费。

    Cost: 1 次往返（``buffs=True`` 时 2 次）。

    Returns:
        单位快照；找不到（句柄过期 / 没有这个 id）时 ``None``。

    Note:
        时点 payload 里的引用只带句柄（``{"t":"unit","h":n}``），**没有** instance_id ——
        所以 ``lp.unit(ev.target)`` 是回调里拿快照的正确写法，别去拼 instance_id。
    """
    if isinstance(target, Unit):
        if buffs:
            _ = target.buffs
        return target
    try:
        wire = to_wire_unit(target)
    except TypeError:
        return None
    raw = _call("unit.state", {"unit": wire})
    if not isinstance(raw, dict):
        return None
    info = UnitState.from_wire(raw)
    if not info.instance_id:
        return None
    result = Unit(info, wire=wire)
    if buffs:
        _ = result.buffs
    return result


def player() -> Optional[Unit]:
    """我方第一个还活着的单位（方便单角色场景）。

    Cost: 1 次往返。

    Returns:
        单位；不在战斗里时 ``None``。
    """
    for item in units():
        if item.is_player and not item.is_dead:
            return item
    return None


def round() -> Optional[int]:
    """当前回合数。

    Cost: 1 次往返。

    Returns:
        回合数；拿不到时 ``None``（不是 0）。

    Note:
        模块级只放函数，所以这里是 ``lp.round()`` 而不是 ``lp.round`` ——
        属性形态请用 ``lp.battle().round``（那次往返本来就要付）。
    """
    value = _call("battle.round", {})
    return int(value) if isinstance(value, int) else None


def buff_stacks(keyword: str) -> Dict[int, int]:
    """全场某 Buff 的层数表（``{instance_id: stacks}``）。

    比"遍历单位逐个查"省 N-1 次往返 —— 这是批量 getter 的存在意义。

    Cost: 1 次往返 + 每个单位若干次（取决于内核是否支持批量，未来会压到 1 次）。

    Args:
        keyword: Buff 关键字名。

    Returns:
        只包含"有该 Buff 的单位"。
    """
    result: Dict[int, int] = {}
    for item in units():
        stacks = item.buff_stack(keyword)
        if stacks:
            result[item.instance_id] = stacks
    return result


# ======================================================================================
# 写
# ======================================================================================


def _action(op: str, args: Dict[str, Any]) -> ActionResult:
    raw = _call(op, args)
    if raw is None:
        return ActionResult(ok=False, reason="A 层不可用（LimbusPy.Ops 未加载或内核未连接）")
    if isinstance(raw, dict):
        info = UnitInfo.from_wire(raw)
        return ActionResult(ok=bool(info.applied), reason="" if info.applied else "没有实际改动", state=info)
    if isinstance(raw, bool):
        return ActionResult(ok=raw, reason="" if raw else "游戏拒绝了这次改动")
    return ActionResult(ok=True)


def damage(target: Any, amount: int, *, source: str = "PASSIVE",
           timing: str = "ON_SUCCESS_ATTACK") -> ActionResult:
    """对单位造成伤害。

    **这是打伤害的唯一推荐入口。** 游戏底层的 ``ChangeHp`` 要的是「目标 HP 值」而不是
    增量、还要补齐枚举与哨兵值 —— 那些坑都在 C# 侧封掉了，这里只接受"扣多少"。

    Args:
        target: 目标单位（``Unit`` / ``instance_id`` / 内核引用）。
        amount: 伤害点数（正数）；``<= 0`` 时不做任何事。
        source: 伤害来源枚举名（``"PASSIVE"`` / ``"BUFF"`` / ``"SKILL"`` …）。
        timing: 事件时机枚举名。

    Cost: 1 次往返（返回伤害后的状态，省掉一次读）。

    Returns:
        ``ActionResult``；``ok=False`` 表示目标已死或没有实际改动。

    Example::

        r = lp.damage(u, 12)
        print(r.state.hp)
    """
    return _action("unit.damage", {
        "unit": to_wire_unit(target), "amount": int(amount),
        "source": source, "timing": timing,
    })


def heal(target: Any, amount: int, *, timing: str = "ON_SUCCESS_ATTACK") -> ActionResult:
    """治疗单位（走游戏自己的回复管线，不会超上限）。

    Args:
        target: 目标单位。
        amount: 治疗点数（正数）。
        timing: 事件时机枚举名。

    Cost: 1 次往返。
    """
    return _action("unit.heal", {
        "unit": to_wire_unit(target), "amount": int(amount), "timing": timing,
    })


def set_hp(target: Any, hp: int, *, source: str = "PASSIVE",
           timing: str = "ON_SUCCESS_ATTACK") -> ActionResult:
    """把血量直接设成某个值（会走游戏的 ``OnChangeHp`` 通知）。

    Args:
        target: 目标单位。
        hp: **目标血量值**（不是增量）。会被夹到 ``0..max_hp``。

    Cost: 1 次往返。

    Note:
        扣血优先用 :func:`damage` —— 它更不容易写错方向。
    """
    return _action("unit.set_hp", {
        "unit": to_wire_unit(target), "hp": int(hp), "source": source, "timing": timing,
    })


def set_sp(target: Any, sp: int) -> ActionResult:
    """把理智（SP/MP）设成某个值。

    Args:
        target: 目标单位。
        sp: 目标理智值；**负数表示恐慌计数**，游戏接受负值。

    Cost: 1 次往返。
    """
    return _action("unit.set_sp", {"unit": to_wire_unit(target), "sp": int(sp)})


def add_shield(target: Any, value: int, *, timing: str = "ON_SUCCESS_ATTACK") -> ActionResult:
    """给单位加护盾。

    Args:
        target: 目标单位。
        value: 护盾值（正数，累加）。

    Cost: 1 次往返。
    """
    return _action("unit.add_shield", {
        "unit": to_wire_unit(target), "value": int(value), "timing": timing,
    })


def set_speed(target: Any, value: int) -> ActionResult:
    """改速度并**立即重算行动顺序**。

    只改值不刷新的话，新速度要等下一回合才影响出手顺序 —— 这个坑在 C# 侧封掉了。

    Args:
        target: 目标单位。
        value: 新的速度值。

    Cost: 1 次往返。
    """
    return _action("unit.set_speed", {"unit": to_wire_unit(target), "value": int(value)})


def refresh_speed(target: Any) -> ActionResult:
    """只重算速度与行动顺序，不改值。

    Cost: 1 次往返。
    """
    return _action("unit.refresh_speed", {"unit": to_wire_unit(target)})


def break_unit(target: Any, *, timing: str = "ON_SUCCESS_ATTACK") -> ActionResult:
    """强制让单位进入崩溃（break）。

    叫 ``break_unit`` 而不是 ``break`` 是因为 ``break`` 是 Python 关键字；
    门面层不为此加下划线后缀，读起来更像一句话。

    Cost: 1 次往返。
    """
    return _action("unit.break", {"unit": to_wire_unit(target), "timing": timing})


def add_buff(target: Any, keyword: str, *, stacks: int = 1, turns: int = 1,
             timing: str = "ON_SUCCESS_ATTACK") -> ActionResult:
    """给单位加一个 Buff。

    Args:
        target: 目标单位。
        keyword: Buff 关键字名。
        stacks: 层数。
        turns: 回合数。

    Cost: 1 次往返。

    Warning:
        **实验性**：虚机时序（activeRound / history 列表）还没在真机验证完；
        失败时返回 ``ok=False`` 而不是抛错。
    """
    return _action("unit.add_buff", {
        "unit": to_wire_unit(target), "keyword": keyword,
        "stacks": int(stacks), "turns": int(turns), "timing": timing,
    })


def add_passive(target: Any, passive_id: int) -> ActionResult:
    """按被动 id 施加一个被动（最安全的"施加效果"入口）。

    Args:
        target: 目标单位。
        passive_id: ``PassiveData`` 的 id。

    Cost: 1 次往返。
    """
    raw = _call("unit.add_passive", {"unit": to_wire_unit(target), "passive_id": int(passive_id)})
    if raw is None:
        return ActionResult(ok=False, reason="A 层不可用")
    return ActionResult(ok=bool(raw), reason="" if raw else "游戏拒绝了这次施加")


def burst(target: Any, keyword: str, *, per_stack: int = 3, max_stacks: int = 6,
          source: str = "PASSIVE") -> BurstResult:
    """把某关键字的叠层换成一笔伤害。

    「读层数 → 夹上限 → 算目标 HP → 改血」全部发生在**同一个主线程时间片**里。
    如果拆成 Python 的多步，中间会被帧边界插进来，读到旧层数、打到旧血量 ——
    这就是复合 op 存在的理由。

    Args:
        target: 目标单位。
        keyword: Buff 关键字名。
        per_stack: 每层换多少伤害。
        max_stacks: 参与结算的最大层数。
        source: 伤害来源枚举名。

    Cost: 1 次往返。

    Returns:
        ``BurstResult``（``ActionResult`` 的子类，多带 ``stacks`` / ``damage`` /
        ``hp_before`` / ``hp_after``）。``ok=False`` 表示没有层数或没有实际改动。
    """
    raw = _call("unit.burst_by_buff", {
        "unit": to_wire_unit(target), "keyword": keyword,
        "per_stack": int(per_stack), "max_stacks": int(max_stacks), "source": source,
    })
    if not isinstance(raw, dict):
        return BurstResult(ok=False, reason="A 层不可用")
    info = BurstInfo.from_wire(raw)
    return BurstResult(
        ok=bool(info.applied),
        reason="" if info.applied else ("没有层数" if not info.stacks else "没有实际改动"),
        stacks=info.stacks,
        damage=info.damage,
        hp_before=info.hp_before,
        hp_after=info.hp_after,
    )


# ======================================================================================
# 事件
# ======================================================================================
#
# 全部转发到 api/hooks.py 的 ``on()``，所以类和模块两种写法都成立：
#
#     @lp.on_hit                       # 模块顶层 / 类体都能用
#     def heal_after(ev): ...
#
#     lp.on_hit(heal_after)            # 直接注册（等价写法）
#
# 返回 None 表示"不改"（观测型时点），返回数值表示"改"（改值型时点）。


def _event(timing: str, fn: Optional[Callable] = None, filters: Optional[Dict[str, Any]] = None):
    """把 ``on_hit`` 这类语法糖收敛成一个实现。"""
    from .api.hooks import on as _on

    decorator = _on(timing, **(filters or {}))
    if fn is None:
        return decorator
    return decorator(fn)


def on_hit(fn: Optional[Callable] = None, **filters: Any):
    """攻击命中结算完成时回调（``AttackSucceeded``）。

    带伤害分解：``ev.final_damage`` / ``ev.real_damage`` / ``ev.hp_damage`` /
    ``ev.is_critical``，以及 ``ev.unit``（攻击方）与 ``ev.target``（受击方）。

    Example::

        @lp.on_hit
        def log_hit(ev):
            print(ev.unit, "→", ev.target, ev.hp_damage)

    Cost: 0 次往返（payload 由 C# 在补丁回调里装好）。
    """
    return _event("AttackSucceeded", fn, filters)


def on_damage(fn: Optional[Callable] = None, **filters: Any):
    """任何来源的 HP 变化都会回调（``DamageDealt``）。

    这是最"全"的时点：治疗、Buff 掉血、被动掉血都走它。要"只看挨打"请用
    :func:`on_damage_taken`。

    Cost: 0 次往返。
    """
    return _event("DamageDealt", fn, filters)


def on_damage_taken(fn: Optional[Callable] = None, **filters: Any):
    """单位受到 HP 伤害时回调（``HpDamageTaken``），带来源与伤害属性。

    Example::

        @lp.on_damage_taken
        def on_taken(ev):
            if ev.hp_damage > 20:
                print("重击!", ev.unit, ev.source, ev.attribute)

    Cost: 0 次往返。
    """
    return _event("HpDamageTaken", fn, filters)


def on_death(fn: Optional[Callable] = None, **filters: Any):
    """单位死亡时回调（``UnitDied``）。

    Cost: 0 次往返。
    """
    return _event("UnitDied", fn, filters)


def on_break(fn: Optional[Callable] = None, **filters: Any):
    """单位崩溃（break）时回调（``UnitBroken``）。

    Cost: 0 次往返。
    """
    return _event("UnitBroken", fn, filters)


def on_turn_start(fn: Optional[Callable] = None, **filters: Any):
    """单位回合开始时回调（``TurnStarted``，日志之后）。

    Cost: 0 次往返。
    """
    return _event("TurnStarted", fn, filters)


def on_round_start(fn: Optional[Callable] = None, **filters: Any):
    """回合（round）开始时回调（``RoundStarted``）。

    Cost: 0 次往返。
    """
    return _event("RoundStarted", fn, filters)
