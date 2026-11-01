"""Façade objects: thin, explicit wrappers over the bridge.

Design rules (they are what make this layer debuggable):

* every member name comes from :mod:`limbuspy.game.members` (verified against a real dump);
* properties prefer a value from the battle snapshot when one is available, and only fall back to
  a round trip when they have to - so reading ``unit.hp`` in a hook is usually free;
* anything not wrapped is still reachable through ``get``/``set``/``call``; the façade never hides
  the reflection layer, it just makes the common 90% pleasant.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .members import ACTION, BUFF, COIN, MANAGER, SKILL, UNIT, Members
from .session import Session, register_wrapper, wrap, wrap_many

__all__ = ["Ref", "Unit", "Skill", "Coin", "Buff", "Action", "Manager"]


class Ref:
    """A reference to a live game object."""

    #: wire kind ("unit", "skill", ...)
    kind: str = "obj"
    #: member-name table for this type
    table: Optional[Members] = None

    def __init__(self, session: Session, wire: Any, data: Optional[dict] = None) -> None:
        self._s = session
        if isinstance(wire, dict):
            self._ref: Any = wire
        elif isinstance(wire, str):
            # "global:Name" / "type:Ns.Type" - resolved by the kernel, no handle involved.
            self._ref = wire
        else:
            self._ref = {"t": self.kind, "h": int(wire or 0)}
        self._data: Dict[str, Any] = dict(data or {})

    # -- identity ----------------------------------------------------------------------

    @property
    def handle(self) -> int:
        return int(self._ref.get("h") or 0) if isinstance(self._ref, dict) else 0

    @property
    def target(self) -> Any:
        """What gets sent to the bridge: a ref map, or a ``global:``/``type:`` spec."""
        return self._ref

    @property
    def wire(self) -> Any:
        return dict(self._ref) if isinstance(self._ref, dict) else self._ref

    @property
    def data(self) -> Dict[str, Any]:
        """The snapshot-provided values for this object (may be empty)."""
        return self._data

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Ref) and other.handle == self.handle and other.kind == self.kind

    def __hash__(self) -> int:
        return hash((self.kind, self.handle))

    def __repr__(self) -> str:
        return "%s(#%d)" % (type(self).__name__, self.handle)

    # -- raw access --------------------------------------------------------------------

    def get(self, member: str, timeout: float = 5.0) -> Any:
        return self._s.get(self._ref, member, timeout=timeout)

    def set(self, member: str, value: Any, timeout: float = 5.0) -> Any:
        if self.table is not None:
            self._s.check_member(self.table.type_name, member)
        return self._s.set(self._ref, member, value, timeout=timeout)

    def call(self, member: str, *args: Any, timeout: float = 5.0) -> Any:
        if self.table is not None:
            self._s.check_member(self.table.type_name, member)
        return self._s.call(self._ref, member, *args, timeout=timeout)

    def _prop(self, alias: str) -> Any:
        """Reads a member: snapshot value when available, else property or parameterless method."""
        if alias in self._data:
            return self._data[alias]
        if self.table is None:
            return self._s.get(self._ref, alias)
        name = self.table.name_for(alias)
        if self.table.is_call(alias):
            return self._s.call(self._ref, name)
        return self._s.get(self._ref, name)

    def _method(self, alias: str, *args: Any) -> Any:
        name = self.table.name_for(alias) if self.table is not None else alias
        return self._s.call(self._ref, name, *args)

    def _typed_prop(self, alias: str, cls) -> Any:
        """Reads a member and coerces the result to ``cls`` (list-aware)."""
        return self._s.coerce(self._prop(alias), cls)

    def _typed_call(self, alias: str, cls, *args: Any) -> Any:
        """Invokes a member and coerces the result to ``cls`` (list-aware)."""
        return self._s.coerce(self._method(alias, *args), cls)


class Buff(Ref):
    kind = "buff"
    table = BUFF

    stack = property(lambda self: self._prop("stack"))
    max_stack = property(lambda self: self._prop("max_stack"))
    turn = property(lambda self: self._prop("turn"))
    max_turn = property(lambda self: self._prop("max_turn"))
    keyword = property(lambda self: self._prop("keyword"))
    name = property(lambda self: self._prop("name"))
    buff_type = property(lambda self: self._prop("buff_type"))

    def is_keyword(self, keyword: str) -> bool:
        return bool(self._method("is_keyword", keyword))


class Coin(Ref):
    kind = "coin"
    table = COIN

    scale = property(lambda self: self._prop("scale"))
    prob = property(lambda self: self._prop("prob"))
    is_destroyed = property(lambda self: self._prop("is_destroyed"))
    is_success = property(lambda self: self._prop("is_success"))

    def is_head(self) -> bool:
        return bool(self._method("is_head"))

    def is_tail(self) -> bool:
        return bool(self._method("is_tail"))


class Skill(Ref):
    kind = "skill"
    table = SKILL

    skill_id = property(lambda self: self._prop("id"))
    name = property(lambda self: self._prop("name"))
    coin_count = property(lambda self: self._prop("coin_count"))

    @property
    def coins(self) -> List[Coin]:
        return self._typed_prop("coins", Coin)

    @property
    def abilities(self) -> List[Ref]:
        return wrap_many(self._s, self._prop("abilities"))

    def is_attack(self) -> bool:
        return bool(self._method("is_attack"))

    def is_defense(self) -> bool:
        return bool(self._method("is_defense"))

    def coin(self, index: int) -> Optional[Coin]:
        return self._typed_call("coin_by_index", Coin, index)


class Action(Ref):
    kind = "action"
    table = ACTION

    skill_id = property(lambda self: self._prop("skill_id"))

    @property
    def skill(self) -> Optional[Skill]:
        return self._typed_prop("skill", Skill)

    @property
    def main_target(self) -> Optional["Unit"]:
        return self._typed_call("main_target", Unit)

    @property
    def targets(self) -> List["Unit"]:
        return self._typed_call("targets", Unit)


class Unit(Ref):
    kind = "unit"
    table = UNIT

    hp = property(lambda self: self._prop("hp"))
    max_hp = property(lambda self: self._prop("max_hp"))
    mp = property(lambda self: self._prop("mp"))
    level = property(lambda self: self._prop("level"))
    position = property(lambda self: self._prop("position"))
    instance_id = property(lambda self: self._prop("instance_id"))
    origin_id = property(lambda self: self._prop("origin_id"))
    faction = property(lambda self: self._prop("faction"))
    is_dead = property(lambda self: self._prop("is_dead"))
    is_break = property(lambda self: self._prop("is_break"))

    @property
    def missing_hp(self) -> int:
        try:
            return max(0, int(self.max_hp) - int(self.hp))
        except (TypeError, ValueError):
            return 0

    @property
    def hp_ratio(self) -> float:
        maximum = int(self.max_hp or 0)
        return (int(self.hp or 0) / maximum) if maximum else 0.0

    @property
    def buffs(self) -> List[Buff]:
        return self._typed_call("buffs", Buff)

    def buff(self, keyword: str) -> Optional[Buff]:
        """First buff whose main keyword matches (case-insensitive, accepts the enum name)."""
        wanted = str(keyword).upper()
        for buff in self.buffs:
            try:
                if str(buff.keyword).upper() == wanted or buff.is_keyword(wanted):
                    return buff
            except Exception:  # noqa: BLE001 - a stale buff must not break the search
                continue
        return None

    def buff_stack(self, keyword: str) -> int:
        buff = self.buff(keyword)
        return int(buff.stack or 0) if buff is not None else 0

    def has_keyword(self, keyword: str) -> bool:
        return bool(self._method("has_keyword", keyword))


class Manager(Ref):
    kind = "model"
    table = MANAGER

    def model(self, instance_id: int) -> Optional[Unit]:
        return self._typed_call("get_model", Unit, instance_id)


# Wire kinds produced by the kernel's classifier. Typed accessors use Session.coerce instead, so
# this map only covers values that arrive without an expected type (snapshot refs, generic results).
register_wrapper("unit", Unit)
register_wrapper("skill", Skill)
register_wrapper("coin", Coin)
register_wrapper("buff", Buff)
register_wrapper("action", Action)
register_wrapper("model", Manager)
register_wrapper("sa", Ref)
register_wrapper("ability", Ref)
register_wrapper("obj", Ref)
register_wrapper("stage", Ref)
register_wrapper("sound", Ref)
register_wrapper("asset", Ref)
