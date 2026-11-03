"""A 层（封装 API）的公共数据类型：单位引用、扁平状态、时机 payload。

为什么这些是 dataclass 而不是裸 dict：A 层的卖点之一是「静态可检查 + 有补全」。
``UnitInfo.hp`` 能被 mypy 推导、IDE 能补全；``info["hp"]`` 不能。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

__all__ = [
    "UnitRef",
    "UnitRefLike",
    "UnitInfo",
    "UnitState",
    "BuffInfo",
    "BurstInfo",
    "to_wire_unit",
]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------------------
# 单位引用
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitRef:
    """一个战斗单位的引用。

    优先用 ``instance_id``（跨回合稳定、适合记账）；``handle`` 只在需要直接寻址时用。
    A 层的 op 两种都接受，也接受裸 ``int``（当 instance_id）和 ``{"h":n}``。
    """

    handle: int = 0
    instance_id: int = 0
    is_player: bool = False

    # -- 构造 ---------------------------------------------------------------------------

    @classmethod
    def from_wire(cls, raw: Any) -> Optional["UnitRef"]:
        if raw is None:
            return None
        if isinstance(raw, UnitRef):
            return raw
        if isinstance(raw, dict):
            return cls(
                handle=_as_int(raw.get("h")),
                instance_id=_as_int(raw.get("instance_id")),
                is_player=bool(raw.get("is_player")),
            )
        return cls(instance_id=_as_int(raw))

    @property
    def key(self) -> str:
        """记账用的稳定键：优先 instance_id。"""
        return str(self.instance_id) if self.instance_id else "h:%d" % self.handle

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return self.key


#: A 层 op 接受的所有"单位引用"写法。
UnitRefLike = Union[UnitRef, int, Dict[str, Any], Any]


def to_wire_unit(value: Any) -> Dict[str, Any]:
    """把任意单位引用写法归一化成内核认的 wire 形式。"""
    if isinstance(value, UnitRef):
        if value.handle:
            return {"h": value.handle}
        return {"instance_id": value.instance_id}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, bool):
        raise TypeError("单位引用不接受 bool")
    if isinstance(value, int):
        return {"instance_id": int(value)}
    if isinstance(value, str) and ":" in value:
        return {"global": value}
    # 门面对象（``lp.Unit``）用 ``wire`` 属性暴露内核认的引用；老式包装器用 ``wire()`` 方法。
    wire = getattr(value, "wire", None)
    if callable(wire):
        return wire()
    if isinstance(wire, dict):
        return dict(wire)
    raise TypeError("无法解析为单位引用: %r" % (value,))


# --------------------------------------------------------------------------------------
# 扁平状态（A 层读操作的返回值）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitInfo:
    """单位状态快照（扁平，不含引用）。"""

    instance_id: int = 0
    origin_id: int = 0
    hp: int = 0
    max_hp: int = 0
    mp: int = 0
    position: str = ""
    is_dead: bool = False
    is_break: bool = False
    faction: str = ""
    is_player: bool = False
    #: 最近一次 op 是否真的改动了状态（读操作恒为 True）
    applied: bool = True

    @classmethod
    def from_wire(cls, raw: Any) -> "UnitInfo":
        data = raw if isinstance(raw, dict) else {}
        return cls(
            instance_id=_as_int(data.get("instance_id")),
            origin_id=_as_int(data.get("origin_id")),
            hp=_as_int(data.get("hp")),
            max_hp=_as_int(data.get("max_hp")),
            mp=_as_int(data.get("mp")),
            position=str(data.get("position") or ""),
            is_dead=bool(data.get("is_dead")),
            is_break=bool(data.get("is_break")),
            faction=str(data.get("faction") or ""),
            is_player=bool(data.get("is_player")),
            applied=bool(data.get("applied", True)),
        )

    @property
    def hp_ratio(self) -> float:
        return (self.hp / self.max_hp) if self.max_hp else 0.0

    @property
    def missing_hp(self) -> int:
        return max(0, self.max_hp - self.hp)


@dataclass(frozen=True)
class UnitState(UnitInfo):
    """``unit.state`` 的返回值：`UnitInfo` 的超集，多带 buff 数量与等级。

    为什么是独立类型而不是往 `UnitInfo` 上加字段：`UnitInfo` 是「一次 op 一定拿得到」的
    最小集，很多场景（伤害返回、事件 payload）只需要它。把可选的富字段放进来会让
    "这个字段到底有没有值"变成新的心智负担。

    继承 `UnitInfo` 而不是重写，所以：
    - 所有 `UnitInfo` 的用法对它照旧（`hp` / `hp_ratio` / `missing_hp` 都在）；
    - 静态检查器仍然能推导每个字段。
    """

    #: 身上的 Buff 数量（比 ``len(state.buffs)`` 省一次往返）
    buff_count: int = 0
    #: 单位等级
    level: int = 0

    @classmethod
    def from_wire(cls, raw: Any) -> "UnitState":
        data = raw if isinstance(raw, dict) else {}
        base = UnitInfo.from_wire(data)
        return cls(
            instance_id=base.instance_id,
            origin_id=base.origin_id,
            hp=base.hp,
            max_hp=base.max_hp,
            mp=base.mp,
            position=base.position,
            is_dead=base.is_dead,
            is_break=base.is_break,
            faction=base.faction,
            is_player=base.is_player,
            applied=base.applied,
            buff_count=_as_int(data.get("buff_count")),
            level=_as_int(data.get("level")),
        )


@dataclass(frozen=True)
class BuffInfo:
    """一个 Buff 的扁平状态。"""

    keyword: str = ""
    stacks: int = 0
    turns: int = 0
    max_stacks: int = 0
    max_turns: int = 0
    name: str = ""

    @classmethod
    def from_wire(cls, raw: Any) -> "BuffInfo":
        data = raw if isinstance(raw, dict) else {}
        return cls(
            keyword=str(data.get("keyword") or ""),
            stacks=_as_int(data.get("stacks")),
            turns=_as_int(data.get("turns")),
            max_stacks=_as_int(data.get("max_stacks")),
            max_turns=_as_int(data.get("max_turns")),
            name=str(data.get("name") or ""),
        )


@dataclass(frozen=True)
class BurstInfo:
    """``unit.burst_by_buff`` 的结算结果。"""

    stacks: int = 0
    damage: int = 0
    hp_before: int = 0
    hp_after: int = 0
    applied: bool = False

    @classmethod
    def from_wire(cls, raw: Any) -> "BurstInfo":
        data = raw if isinstance(raw, dict) else {}
        return cls(
            stacks=_as_int(data.get("stacks")),
            damage=_as_int(data.get("damage")),
            hp_before=_as_int(data.get("hp_before")),
            hp_after=_as_int(data.get("hp_after")),
            applied=bool(data.get("applied")),
        )
