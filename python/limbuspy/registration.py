"""内容注册层：把「注册一个 Buff / 被动 / 人格」变成写一个类。

它解决三件事（见 ``docs/plans/02-registration-layer.md``）：

1. **内容有身份**。每个注册项都带 ``uid = "<mod>.<kind>.<local_id>"``。
   Lethe 最大的坑就是没有这个概念 —— 它的下游模组得写 369 行去猜"这个 id 是谁注册的"。

2. **条件零往返**。注册一个 Buff 之后，``victim.has(那个Buff)`` 在事件回调里
   只需一次整数按位与 —— 因为 C# 侧在补丁回调里已经把这个 mod 关心的 buff
   算成位图塞进 payload 了。

3. **注册期就把错误挑出来**。需要跨进程往返的谓词会被直接拒绝（``PredicateCostError``），
   而不是等到真机上每帧偷偷多跑几次 IPC。

作者写起来的样子::

    import limbuspy as lp

    class Drowning(lp.Buff):
        key = "DROWNING"
        name = "溺水"
        max_stacks = 10
        max_turns = 5

    class TideCrush(lp.Passive):
        @lp.on_hit(when=lp.victim.has(Drowning))
        def crush(self, ev):
            lp.damage(ev.target, 12)
"""

from __future__ import annotations

import random
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "Content",
    "Buff",
    "Passive",
    "Identity",
    "Skill",
    "Ego",
    "Pred",
    "Role",
    "victim",
    "self_unit",
    "ev",
    "round_",
    "once_per_turn",
    "once_per_round",
    "cooldown",
    "chance",
    "all_of",
    "any_of",
    "none_of",
    "PredicateCostError",
    "RegistrationError",
    "CUSTOM_BUFF_BASE_ID",
    "Cond",
    "compile_predicate",
    "plan_for",
]


# ======================================================================================
# 错误
# ======================================================================================


class RegistrationError(Exception):
    """内容定义不合法（缺字段、id 冲突、类型不对）。"""


class PredicateCostError(RegistrationError):
    """谓词需要跨进程往返，但时点过滤要求零往返。

    这不是"性能建议"，是硬约束：事件回调跑在游戏主线程的补丁里，一次多余的 IPC
    就会挤占 20ms 预算，表现是随机掉帧甚至拼点判定错乱。

    Example:
        错误的写法::

            @lp.on_hit(when=any_enemy.has("SINKING"))     # ← 要遍历全场，1 次往返

        正确的写法::

            # 在 on_battle_start 里读一次存进 self.state，谓词读缓存
            @lp.on_hit(when=lp.self_state("has_sinking"))
    """


#: 自定义 Buff 的 id 基址，**必须**与 C# 侧 `BuffInject.BaseId` 一致。
#:
#: 为什么是 24576 而不是 Lethe 的 4097：原版 `BUFF_UNIQUE_KEYWORD` 共 2124 个成员、
#: 最大 2123 且就是 `None` 哨兵，1000→2123 密排；24576 有约 2.2 万个空号，
#: 天然跨过哨兵，也能和 Lethe（4097 + 2k）共存。
CUSTOM_BUFF_BASE_ID = 24576


# ======================================================================================
# 内容基类
# ======================================================================================


class Content:
    """可注册内容的元数据根。

    子类在**类体**里声明字段（不需要 ``__init__``），loader 收集即注册 ——
    与 ``@hook`` / ``@expose`` 的「无 import 副作用」原则一致。

    每个子类自动获得：``uid``（``"<mod>.<kind>.<local_id>"``）、``kind``、
    以及注册期分配的位序号 ``bit``（谓词用它做零往返判断）。
    """

    #: 内容种类（子类覆盖：``"buff"`` / ``"passive"`` / ...）
    kind: str = "content"

    #: 模组内的本地标识；同 mod 内同类内容不许重名
    local_id: str = ""

    #: 展示名（给玩家看）
    name: str = ""

    #: 对应游戏静态数据的 id；0 表示"只在本 mod 内存在"（P2 才做真注入）
    game_id: int = 0

    #: 注册期回填：所属模组名
    owner: str = ""

    #: 注册期回填：在 mod 位图里的位序号（-1 = 未分配）
    bit: int = -1

    @classmethod
    def uid(cls) -> str:
        """``"<mod>.<kind>.<local_id>"``；未注册时返回本地部分。"""
        local = cls.local_id or cls.__name__.lower()
        return "%s.%s.%s" % (cls.owner, cls.kind, local) if cls.owner else "%s.%s" % (cls.kind, local)

    @classmethod
    def validate(cls) -> List[str]:
        """检查必填字段；返回问题列表（空 = 合法）。

        loader 会把问题记进 ``mod.registration_errors`` 并**只跳过这个内容** ——
        一个写错的内容不该让整个 mod 起不来。
        """
        problems: List[str] = []
        if not cls.local_id:
            problems.append("%s: 缺少 local_id" % cls.__name__)
        if not cls.name:
            problems.append("%s: 缺少 name（展示名）" % cls.__name__)
        return problems

    @classmethod
    def table_name(cls) -> str:
        """这类内容落到哪张游戏静态数据表（例如 ``"BuffData"``）。

        Returns:
            表名；未知种类返回空串。
        """
        from .staticdata import TABLE_NAMES

        return TABLE_NAMES.get(str(getattr(cls, "kind", "")), "")

    @classmethod
    def define(cls) -> Dict[str, Any]:
        """把内容声明编译成静态数据行。

        子类各自实现：Buff / Passive 产出**完整行**，Skill / Ego / Identity 产出
        **模板继承声明**（因为它们的数据面远超我们建模的范围）。

        Returns:
            字典（键名与游戏静态数据类的字段一致）。

        Raises:
            ValueError: 缺少必需的信息（例如人格没给 ``base_game_id``）。
        """
        raise NotImplementedError("%s 没有实现 define()" % (cls.__name__,))

    @classmethod
    def describe(cls) -> str:
        """一行描述（诊断/``dump_plan`` 用）。"""
        return "%s(%s) game_id=%d bit=%d" % (cls.uid(), cls.name, cls.game_id, cls.bit)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<%s>" % (type(self).uid(),)


class Buff(Content):
    """一个可注册的 Buff。

    类体字段：

    * ``key`` —— 英文关键字（写逻辑用）
    * ``name`` —— 本地化名字（给玩家看）
    * ``max_stacks`` / ``max_turns`` —— 上限
    * ``icon`` —— 图标文件名（P2 真注入时用）
    * ``game_id`` —— 复用官方 Buff 时填它的 ``BUFF_UNIQUE_KEYWORD`` 数值

    Example::

        class SinkingTide(lp.Buff):
            key = "SINKING_TIDE"
            name = "沉潮"
            max_stacks = 12
            max_turns = 4
    """

    kind = "buff"

    #: 英文关键字（大小写不敏感）
    key: str = ""

    #: 层数上限
    max_stacks: int = 99

    #: 回合上限
    max_turns: int = 99

    #: 图标资源名（P2 真注入时用；先用官方图标就留空）
    icon: str = ""

    @classmethod
    def validate(cls) -> List[str]:
        problems = super().validate()
        if not cls.key:
            problems.append("%s: 缺少 key（英文关键字）" % cls.__name__)
        if cls.max_stacks <= 0:
            problems.append("%s: max_stacks 必须为正" % cls.__name__)
        return problems

    #: 游戏 ``BUFF_CLASS`` 名（``"Positive"`` / ``"Negative"`` / ``"Neutral"``）
    buff_class: str = "Neutral"

    #: 游戏 ``BUF_TYPE`` 名（决定 UI 上怎么归类）
    buff_type: str = "Neutral"

    #: 罪孽属性（``ATTRIBUTE_TYPE`` 名，例如 ``"WRATH"``；无关时留 ``"NONE"``）
    attribute: str = "NONE"

    #: 层数总量上限（0 = 不限制）
    total_stack: int = 0

    #: 每回合最多叠几层（0 = 不限制）
    stack_limit_per_turn: int = 0

    #: 是否可被驱散
    can_be_despelled: bool = True

    #: 是否允许同名重复
    can_be_duplicated: bool = False

    #: 分类关键字（对应 ``BUFF_CATEGORY_KEYWORD``）
    categories: tuple = ()

    @classmethod
    def keyword(cls) -> str:
        """谓词与 op 使用的关键字（``key`` 的规范形式）。"""
        return str(cls.key or cls.local_id or cls.__name__).upper()

    @classmethod
    def define(cls) -> Dict[str, Any]:
        """生成 ``BuffStaticData`` 的一行（31 个字段，键名逐字取自游戏导出）。

        Returns:
            可直接进内容包的字典。

        Example::

            class Drowning(lp.ContentBuff):
                local_id, key, name = "drowning", "DROWNING", "溺水"

            Drowning.define()["maxStack"]     # 99（默认）
        """
        from .staticdata import buff_row

        return buff_row(cls)


class Passive(Content):
    """一个被动：**一组带条件的时点回调**。

    注意它不是独立的触发机制 —— 被动 =
    「``Content`` 的元数据与生命周期」+「若干个 ``@on_*`` 处理器」。
    这样被动和普通钩子走同一条 dispatch 路径，没有第二套语义。

    Example::

        class TideCrush(lp.Passive):
            name = "潮涌"

            @lp.on_hit(when=lp.victim.has(SinkingTide))
            def crush(self, ev):
                lp.damage(ev.target, 10)
    """

    kind = "passive"

    #: 要施加的官方被动 id（``PassiveData`` 的 id）。
    #:
    #: 填了它，:meth:`attach` 就能把这个被动真的挂到单位身上 ——
    #: 走的是 ``unit.add_passive``，即「按 id 施加已有被动」，不需要任何静态数据注入，
    #: 所以它是**当前唯一可以完整落地**的被动形态。
    passive_id: int = 0

    @classmethod
    def validate(cls) -> List[str]:
        problems = super().validate()
        if int(getattr(cls, "passive_id", 0) or 0) < 0:
            problems.append("%s: passive_id 不能为负" % cls.__name__)
        return problems

    @classmethod
    def define(cls) -> Dict[str, Any]:
        """生成 ``PassiveStaticData`` 的一行（7 个字段）。

        Raises:
            ValueError: 没填 ``passive_id`` —— 我们不发明被动 id，那个空间由游戏独占。
        """
        from .staticdata import passive_row

        return passive_row(cls)

    def attach(self, unit: Any) -> bool:
        """把这个被动挂到单位身上。

        Args:
            unit: 目标单位（``lp.Unit`` / instance_id / 内核引用）。

        Returns:
            ``True`` = 已施加。

        Cost: 1 次往返。

        Note:
            没有填 ``passive_id`` 时返回 ``False`` 并给出原因 —— 那是"声明了但没说要施加
            哪个官方被动"，属于配置问题，不是运行时错误。
        """
        passive_id = int(getattr(type(self), "passive_id", 0) or 0)
        if passive_id <= 0:
            return False
        try:
            from .facade import add_passive

            return bool(add_passive(unit, passive_id).ok)
        except Exception:  # noqa: BLE001 - 门面不可用/降级时保持安静
            return False


class Identity(Content):
    """一个人格（Identity）。

    **必须**填 `base_game_id`：我们不支持"从零造人格"。游戏的人格由静态数据表
    （立绘 / 技能表 / 数值曲线）驱动，缺其中任何一项都会在选人界面炸掉；Lethe 用
    32KB 两个文件也只做到"以某个人格为模板 + 覆盖字段"。所以这里强制模板继承。

    Example::

        class MyFaust(lp.Identity):
            local_id = "my_faust"
            name = "魁首浮士德（改）"
            base_game_id = 10115        # ← 模板人格的静态 id，必填

    当前阶段：声明与校验可用，**实际生成未实现**（见 `define()`）。
    """

    kind = "identity"

    #: 模板人格的静态 id（必填）
    base_game_id: int = 0

    @classmethod
    def validate(cls) -> List[str]:
        problems = super().validate()
        if int(cls.base_game_id or 0) <= 0:
            problems.append(
                "%s: 缺少 base_game_id —— 不支持从零造人格，必须指定一个官方人格作模板"
                % cls.__name__)
        return problems

    @classmethod
    def define(cls) -> Dict[str, Any]:
        """生成「以官方 人格 为模板 + 覆盖字段」的声明。

        不产出完整行：人格 的数据面远超我们建模的范围，从零构造必然出错。
        加载器会用模板行合并 ``overrides`` 得到真正的行。

        Returns:
            ``{"table": ..., "base": <模板 id>, "overrides": {...}}``

        Raises:
            ValueError: 缺 ``base_game_id``。
        """
        from .staticdata import personality_template

        return personality_template(cls)

class Skill(Content):
    """一个技能。

    同样要求 `base_game_id`（模板技能）。技能的数据面（硬币、威力、命中效果、
    ability 列表）远大于 Buff，从零构造必然漏字段。
    """

    kind = "skill"

    #: 模板技能的静态 id（必填）
    base_game_id: int = 0

    @classmethod
    def validate(cls) -> List[str]:
        problems = super().validate()
        if int(cls.base_game_id or 0) <= 0:
            problems.append("%s: 缺少 base_game_id（模板技能 id，必填）" % cls.__name__)
        return problems

    @classmethod
    def define(cls) -> Dict[str, Any]:
        """生成「以官方 技能 为模板 + 覆盖字段」的声明。

        不产出完整行：技能 的数据面远超我们建模的范围，从零构造必然出错。
        加载器会用模板行合并 ``overrides`` 得到真正的行。

        Returns:
            ``{"table": ..., "base": <模板 id>, "overrides": {...}}``

        Raises:
            ValueError: 缺 ``base_game_id``。
        """
        from .staticdata import skill_template

        return skill_template(cls)

class Ego(Content):
    """一个 EGO。要求 `base_game_id`（模板 EGO）。"""

    kind = "ego"

    #: 模板 EGO 的静态 id（必填）
    base_game_id: int = 0

    @classmethod
    def validate(cls) -> List[str]:
        problems = super().validate()
        if int(cls.base_game_id or 0) <= 0:
            problems.append("%s: 缺少 base_game_id（模板 EGO id，必填）" % cls.__name__)
        return problems

    @classmethod
    def define(cls) -> Dict[str, Any]:
        """生成「以官方 EGO 为模板 + 覆盖字段」的声明。

        不产出完整行：EGO 的数据面远超我们建模的范围，从零构造必然出错。
        加载器会用模板行合并 ``overrides`` 得到真正的行。

        Returns:
            ``{"table": ..., "base": <模板 id>, "overrides": {...}}``

        Raises:
            ValueError: 缺 ``base_game_id``。
        """
        from .staticdata import ego_template

        return ego_template(cls)


# ======================================================================================
# 谓词
# ======================================================================================


class Cond:
    """一次事件内、**所有谓词共享**的求值上下文。

    构造一次，N 个谓词复用 —— 这是"多个被动只付一次代价"的落地位置。

    属性（全部已由 C# 在补丁回调里填好，0 往返）：

    * ``payload`` —— 时点 payload（``ev.hp_damage`` 之类）
    * ``mask_victim`` / ``mask_self`` —— 本 mod 关心的 buff 是否存在的位图
    * ``round`` —— 当前回合
    * ``state`` —— 本处理器私有的可变状态（跨热重载保留）
    """

    __slots__ = ("payload", "mask_victim", "mask_self", "round", "state", "rng", "_buffs")

    def __init__(self, payload: Any = None, mask_victim: int = 0, mask_self: int = 0,
                 round_: int = 0, state: Optional[dict] = None) -> None:
        self.payload = payload
        self.mask_victim = int(mask_victim or 0)
        self.mask_self = int(mask_self or 0)
        self.round = int(round_ or 0)
        self.state = state if state is not None else {}
        self.rng = random.Random()

    def mask_of(self, role: str) -> int:
        """某个角色的 buff 位图。"""
        return self.mask_victim if role == "victim" else self.mask_self

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<Cond round=%d victim=0b%b self=0b%b>" % (
            self.round, self.mask_victim, self.mask_self)


class Pred:
    """可组合、可静态检查、**声明了自己要什么**的过滤谓词。

    为什么不是 ``when=lambda c: ...``：

    * lambda 的参数在 mypy/pyright 里永远是 ``Any``，整条谓词失去类型检查；
    * lambda 无法声明 ``rpc``，于是"这条谓词会偷偷跨进程"这件事在注册期发现不了；
    * 谓词的 ``describe()`` 能打出人话，lambda 只能打出内存地址。

    组合用运算符（DSL 要短）::

        when=victim.has(Drowning) & once_per_turn() & chance(0.3)
        when=any_of(victim.has(A), victim.has(B))
    """

    __slots__ = ()

    #: 需要几次跨进程往返（0 = 纯本地）。注册期会检查它。
    rpc: int = 0

    def __call__(self, cond: Cond) -> bool:  # pragma: no cover - 抽象
        raise NotImplementedError

    def describe(self) -> str:
        """人能读懂的描述（错误信息与 ``dump_plan`` 用）。"""
        return type(self).__name__

    def bits(self) -> Iterable[Any]:
        """这个谓词关心哪些 ``(角色, 位序号)``（用于生成 watch 清单）。"""
        return ()

    def walk(self) -> Iterable["Pred"]:
        """遍历自己（以及子谓词）—— 注册期给"字符串形式的 Buff 名"绑定位序号要用。"""
        yield self

    # -- 组合 ---------------------------------------------------------------------------

    def __and__(self, other: "Pred") -> "Pred":
        return AllOf(self, other)

    def __or__(self, other: "Pred") -> "Pred":
        return AnyOf(self, other)

    def __invert__(self) -> "Pred":
        return Not(self)

    def __bool__(self) -> bool:
        raise TypeError(
            "谓词不能直接当布尔用（它是个条件对象）。用 & | ~ 组合，或用 all_of/any_of 包裹。"
            "如果你想要的是「立即求值」，请传进 @on_*(when=...)。")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<%s>" % (self.describe(),)


class AllOf(Pred):
    """全部成立。"""

    __slots__ = ("children", "rpc")

    def __init__(self, *children: Pred) -> None:
        self.children = [child for child in children if child is not None]
        self.rpc = sum(getattr(child, "rpc", 0) for child in self.children)

    def __call__(self, cond: Cond) -> bool:
        return all(child(cond) for child in self.children)

    def describe(self) -> str:
        return " & ".join(child.describe() for child in self.children) or "True"

    def bits(self) -> Iterable[Any]:
        for child in self.children:
            yield from child.bits()

    def walk(self) -> Iterable["Pred"]:
        yield self
        for child in self.children:
            yield from child.walk()


class AnyOf(Pred):
    """任一成立。"""

    __slots__ = ("children", "rpc")

    def __init__(self, *children: Pred) -> None:
        self.children = [child for child in children if child is not None]
        self.rpc = min((getattr(child, "rpc", 0) for child in self.children), default=0)

    def __call__(self, cond: Cond) -> bool:
        return any(child(cond) for child in self.children)

    def describe(self) -> str:
        return " | ".join(child.describe() for child in self.children) or "False"

    def bits(self) -> Iterable[Any]:
        for child in self.children:
            yield from child.bits()

    def walk(self) -> Iterable["Pred"]:
        yield self
        for child in self.children:
            yield from child.walk()


class Not(Pred):
    """取反。"""

    __slots__ = ("child", "rpc")

    def __init__(self, child: Pred) -> None:
        self.child = child
        self.rpc = getattr(child, "rpc", 0)

    def __call__(self, cond: Cond) -> bool:
        return not self.child(cond)

    def describe(self) -> str:
        return "~(%s)" % (self.child.describe(),)

    def bits(self) -> Iterable[Any]:
        return self.child.bits()

    def walk(self) -> Iterable["Pred"]:
        yield self
        yield from self.child.walk()


class Role:
    """谓词里的"角色"占位（``victim`` / ``self_unit`` / ``ev`` 都是它的实例）。

    它存在的唯一理由是让 ``victim.has(Buff)`` 读起来像一句话 ——
    而 ``has()`` 返回的仍然是 ``Pred``，所以静态检查器全程跟得上。
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def has(self, buff: Any) -> Pred:
        """这个角色身上有没有该 Buff（**0 往返**：读 C# 预填的位图）。"""
        return HasBuff(self.name, buff)

    def has_any(self, *buffs: Any) -> Pred:
        """身上有没有其中任意一个 Buff（**0 往返**）。"""
        return AnyOf(*[HasBuff(self.name, buff) for buff in buffs])

    def __getattr__(self, item: str) -> "_Field":
        """``ev.hp_damage`` / ``victim.is_break`` —— 属性即谓词构造器。"""
        return _Field(self.name, item)

    def __repr__(self) -> str:  # pragma: no cover
        return "<role %s>" % (self.name,)


#: 受击者（``AttackSucceeded`` / ``HpDamageTaken`` 等时点的 target）
victim = Role("victim")

#: 施加者 / 被补丁的实例自己
self_unit = Role("self")

#: payload 本身（比较伤害数值、暴击标记等）
ev = Role("ev")


class _Field(Pred):
    """``role.field`` 挂在谓词上时，比较运算构造成 ``_Compare``。

    它同时是 ``Pred`` 的实例，所以 ``when=ev.is_critical`` 这种直接写的用法也成立
    （此时语义是"字段为真"）。
    """

    __slots__ = ("role", "field")

    def __init__(self, role: str, field: str) -> None:
        self.role = role
        self.field = field

    def _value(self, cond: Cond) -> Any:
        payload = cond.payload
        if self.role == "ev":
            return getattr(payload, self.field, None)
        # victim / self 的标量字段直接从 payload 上取（例如 ev.target.is_break）
        subject = getattr(payload, "target" if self.role == "victim" else "unit", None)
        return getattr(subject, self.field, None)

    def __call__(self, cond: Cond) -> bool:
        return bool(self._value(cond))

    def describe(self) -> str:
        return "%s.%s" % (self.role, self.field)

    # 比较运算 → _Compare（让 ev.hp_damage > 100 能写）
    def _compare(self, operator: str, other: Any) -> Pred:
        return _Compare(self, operator, other)

    def __gt__(self, other: Any) -> Pred:
        return self._compare(">", other)

    def __ge__(self, other: Any) -> Pred:
        return self._compare(">=", other)

    def __lt__(self, other: Any) -> Pred:
        return self._compare("<", other)

    def __le__(self, other: Any) -> Pred:
        return self._compare("<=", other)

    def __eq__(self, other: Any) -> Pred:  # type: ignore[override]
        return self._compare("==", other)

    def __ne__(self, other: Any) -> Pred:  # type: ignore[override]
        return self._compare("!=", other)

    def __hash__(self) -> int:  # 与 __eq__ 并存必须给 hash
        return hash((self.role, self.field))


class _Compare(Pred):
    """``role.field <op> value``。"""

    __slots__ = ("field", "operator", "other")

    _OPS = {
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }

    def __init__(self, field: _Field, operator: str, other: Any) -> None:
        self.field = field
        self.operator = operator
        self.other = other

    def __call__(self, cond: Cond) -> bool:
        value = self.field._value(cond)
        # 允许字段比字段（``ev.old_hp > ev.new_hp``）：右侧是 _Field 时同样按本次事件求值。
        # 不这么做的话 ``int > _Field`` 会走反射比较、拿回一个 Pred 对象，静默变成恒假。
        other = self.other._value(cond) if isinstance(self.other, _Field) else self.other
        if value is None or other is None:
            return False
        try:
            return bool(self._OPS[self.operator](value, other))
        except TypeError:
            return False

    def describe(self) -> str:
        right = self.other.describe() if isinstance(self.other, _Field) else repr(self.other)
        return "%s %s %s" % (self.field.describe(), self.operator, right)


class HasBuff(Pred):
    """``victim.has(Buff)`` → 一次整数按位与，**0 往返**。

    两种写法都支持：

    * ``victim.has(Drowning)`` —— 本 mod 注册的 Buff 类（位序号注册期就定了）；
    * ``victim.has("SINKING")`` —— 游戏**原版** Buff 的关键字。位序号在
      :func:`plan_for` 里临时分配（它同样会被写进 ``watch``，所以内核照样算得出位图）。
    """

    __slots__ = ("role", "buff", "keyword", "bit")

    def __init__(self, role: str, buff: Any) -> None:
        self.role = role
        self.buff = buff
        self.keyword = _keyword_of(buff)
        #: 位序号；-1 = 还没分配（字符串写法要等 plan_for）
        self.bit = _bit_of(buff)

    def bind(self, bit: int) -> None:
        """注册期回填位序号（给字符串写法用）。"""
        self.bit = int(bit)

    def __call__(self, cond: Cond) -> bool:
        bit = self.bit
        if bit < 0:
            return False
        return bool(cond.mask_of(self.role) & (1 << bit))

    def describe(self) -> str:
        return "%s.has(%s)" % (self.role, self.keyword)

    def bits(self) -> Iterable[Any]:
        if self.bit >= 0:
            yield (self.role, self.bit)


class Round(Pred):
    """``round_() >= 3`` 这类回合条件（payload 里已有 round，0 往返）。"""

    __slots__ = ("operator", "value")

    def __init__(self, operator: str, value: int) -> None:
        self.operator = operator
        self.value = int(value)

    def __call__(self, cond: Cond) -> bool:
        return bool(_Compare._OPS[self.operator](cond.round, self.value))

    def describe(self) -> str:
        return "round %s %d" % (self.operator, self.value)


class OncePerTurn(Pred):
    """每回合最多触发一次（状态跨热重载保留）。"""

    __slots__ = ()

    rpc = 0

    def __call__(self, cond: Cond) -> bool:
        last = cond.state.get("__turn__")
        if last == cond.round:
            return False
        cond.state["__turn__"] = cond.round
        return True

    def describe(self) -> str:
        return "once_per_turn"


class Cooldown(Pred):
    """``cooldown(n)``：相邻两次触发至少隔 ``n`` 个回合。"""

    __slots__ = ("turns",)

    def __init__(self, turns: int) -> None:
        self.turns = max(1, int(turns))

    def __call__(self, cond: Cond) -> bool:
        last = cond.state.get("__cooldown__")
        if last is not None and cond.round - last < self.turns:
            return False
        cond.state["__cooldown__"] = cond.round
        return True

    def describe(self) -> str:
        return "cooldown(%d)" % (self.turns,)


class Chance(Pred):
    """``chance(0.3)``：按概率放行（用 mod 私有的确定性随机源）。"""

    __slots__ = ("probability",)

    def __init__(self, probability: float) -> None:
        self.probability = float(probability)

    def __call__(self, cond: Cond) -> bool:
        return cond.rng.random() < self.probability

    def describe(self) -> str:
        return "chance(%.2f)" % (self.probability,)


def round_() -> _Field:
    """``round_() >= 3``：回合条件（等价于读 ``Cond.round``，但读起来像一句话）。

    Example::

        @lp.on_hit(when=round_() >= 3)
        def late_game(ev): ...
    """
    return _RoundField()


class _RoundField(Pred):
    """支持 ``round_() >= 3`` 的比较链。"""

    __slots__ = ()

    rpc = 0

    def __call__(self, cond: Cond) -> bool:
        return bool(cond.round)

    def describe(self) -> str:
        return "round"

    def __gt__(self, other: int) -> Pred:
        return Round(">", other)

    def __ge__(self, other: int) -> Pred:
        return Round(">=", other)

    def __lt__(self, other: int) -> Pred:
        return Round("<", other)

    def __le__(self, other: int) -> Pred:
        return Round("<=", other)

    def __eq__(self, other: object) -> Pred:  # type: ignore[override]
        return Round("==", int(other))  # type: ignore[arg-type]

    def __ne__(self, other: object) -> Pred:  # type: ignore[override]
        return Round("!=", int(other))  # type: ignore[arg-type]

    def __hash__(self) -> int:
        return hash("round")


def once_per_turn() -> Pred:
    """每回合最多触发一次。"""
    return OncePerTurn()


def once_per_round() -> Pred:
    """每回合（round）最多触发一次（与 ``once_per_turn`` 同义，名字更贴游戏用语）。"""
    return OncePerTurn()


def cooldown(turns: int) -> Pred:
    """相邻两次触发至少隔 ``turns`` 个回合。"""
    return Cooldown(turns)


def chance(probability: float) -> Pred:
    """按概率放行（0..1）。"""
    return Chance(probability)


def all_of(*predicates: Pred) -> Pred:
    """全部成立（与 ``&`` 等价，具名写法便于从别处迁过来）。"""
    return AllOf(*predicates)


def any_of(*predicates: Pred) -> Pred:
    """任一成立（与 ``|`` 等价）。"""
    return AnyOf(*predicates)


def none_of(*predicates: Pred) -> Pred:
    """全部不成立。"""
    return Not(AnyOf(*predicates))


# ======================================================================================
# 位分配与计划
# ======================================================================================


def _keyword_of(buff: Any) -> str:
    if isinstance(buff, str):
        return buff.upper()
    keyword = getattr(buff, "keyword", None)
    if callable(keyword):
        return str(keyword()).upper()
    return str(getattr(buff, "key", "") or getattr(buff, "local_id", "")).upper()


def _bit_of(buff: Any) -> int:
    """Buff 类/名字 → 位序号（-1 = 未注册，谓词恒假）。

    未注册时**不抛错**：热重载期间、或者作者在 Buff 注册之前就写了谓词，
    都不该让 mod 起不来。只是这条谓词永远不成立，并且 ``dump_plan`` 里能看见。
    """
    if isinstance(buff, str):
        return -1
    return int(getattr(buff, "bit", -1))


def compile_predicate(pred: Optional[Pred], *, allow_rpc: bool = False) -> Callable[[Cond], bool]:
    """把谓词编译成一个普通函数（注册期做一次，运行期零开销）。

    Args:
        pred: 谓词；``None`` 表示"永远成立"。
        allow_rpc: 为 ``True`` 时允许需要往返的谓词（默认拒绝）。

    Returns:
        接受 ``Cond`` 的判定函数。

    Raises:
        PredicateCostError: 谓词需要跨进程往返而 ``allow_rpc=False``。
    """
    if pred is None:
        return lambda cond: True

    cost = getattr(pred, "rpc", 0)
    if cost > 0 and not allow_rpc:
        raise PredicateCostError(
            "谓词 %r 需要 %d 次跨进程调用，但时点过滤要求 rpc == 0。\n"
            "事件回调跑在游戏主线程的补丁里，一次多余的 IPC 就会挤占 20ms 预算。\n"
            "改法：在 on_battle_start / on_round_start 里读一次存进 self.state，"
            "谓词改为读缓存；或显式 when=..., allow_rpc=True 承认这个成本。"
            % (pred.describe(), cost))

    return lambda cond: bool(pred(cond))


def plan_for(hooks: Sequence[Any], buffs: Sequence[type]) -> Dict[str, Any]:
    """把一个 mod 的注册结果编译成给内核的计划。

    Args:
        hooks: 带 ``__limbuspy_hook__`` 的函数（或元数据对象）。
        buffs: 已注册的 ``Buff`` 子类。

    Returns:
        ``{"watch": [关键字...], "hooks": [{"timing": ..., "victim": [位...], "self": [位...]}]}``

    内核按 ``watch`` 计算位图：位 ``i`` 对应 ``watch[i]``。
    这样"受击者有没有某个 buff"在 C# 侧就变成一次按位与，Python 侧 0 往返。

    **字符串写法也能用**：``victim.has("SINKING")`` 里的关键字会在这一步被
    加进 ``watch`` 并把位序号回填到谓词上 —— 原版 Buff 不需要先注册一个包装类。
    """
    watch: List[str] = []
    index: Dict[str, int] = {}

    def intern(keyword: str) -> int:
        if keyword not in index:
            index[keyword] = len(watch)
            watch.append(keyword)
        return index[keyword]

    for buff in buffs:
        # 只有 Buff 种类的内才有"关键字"；Passive 之类不该占位（否则 watch 里会出现
        # 一堆永远为假的空关键字，白白加宽位图）
        if str(getattr(buff, "kind", "")) != "buff":
            continue
        # 位序号以 watch 的下标为准，并回填到内容类上 —— 与 ``collect_contents``
        # 的编号恰好一致（那边也只给 Buff 编号，顺序相同），这里显式对齐一次以防漂移。
        buff.bit = intern(_keyword_of(buff))

    specs: List[Any] = []
    for hook in hooks:
        spec = getattr(hook, "__limbuspy_hook__", hook)
        # 三条来源按优先级：编译后的 HookEntry（.pred）→ 原始 HookSpec（.when）→ 谓词本身
        pred = getattr(spec, "pred", None)
        if pred is None:
            candidate = getattr(spec, "when", None)
            if candidate is not None and hasattr(candidate, "bits"):
                pred = candidate
        if pred is None and hasattr(spec, "bits"):
            pred = spec
        specs.append((spec, pred))

    # 先绑定字符串关键字（要先把它们加进 watch，位序号才存在）
    for _spec, pred in specs:
        if pred is None:
            continue
        for node in getattr(pred, "walk", lambda: iter(()))():
            if isinstance(node, HasBuff) and node.bit < 0:
                node.bind(intern(node.keyword))

    entries: List[Dict[str, Any]] = []
    for spec, pred in specs:
        victim_bits: List[int] = []
        self_bits: List[int] = []
        if pred is not None:
            for role, bit in pred.bits():
                target = victim_bits if role == "victim" else self_bits
                if bit not in target:
                    target.append(bit)
        entries.append({"timing": getattr(spec, "timing", ""),
                        "victim": victim_bits, "self": self_bits})

    return {"watch": watch, "hooks": entries}
