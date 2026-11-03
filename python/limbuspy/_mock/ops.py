"""A 层（封装 op）的 **Python 参考实现** —— 让 example / 测试不需要游戏也能跑。

它与 C# 侧 ``LimbusPy.Ops`` 的契约由 ``api/_contract_manifest.json`` 对齐：
op 名、参数名、返回结构必须一致；语义（例如 ``unit.damage`` 内部算目标 HP）也必须一致。
它不是"游戏模拟器"，只是**契约的参考实现**：足够验证调用方代码，不足以验证游戏行为。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["MockUnit", "MockBattle", "MockOps", "BUFF_DEFAULTS"]

#: 默认的 buff 上限（真实值来自静态表 BuffData；这里只要够示例用）
BUFF_DEFAULTS = {
    "SINKING": (99, 99),
    "CHARGE": (20, 3),
    "RUPTURE": (99, 99),
    "BURN": (99, 99),
    "TREMOR": (99, 99),
}

DAMAGE_SOURCES = {"COMBAT", "BUFF", "PASSIVE", "SKILL", "EVENT", "EGO_GIFT"}
TIMINGS = {"ON_SUCCESS_ATTACK", "ON_START_ROUND", "ON_BATTLE_START", "ON_KILL", "ON_DIE"}


def _enum_name(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text.upper() if text else fallback


@dataclass
class MockUnit:
    """一个战斗单位的模拟状态。"""

    instance_id: int
    origin_id: int = 0
    hp: int = 100
    max_hp: int = 100
    mp: int = 0
    position: str = "MAIN"
    is_player: bool = True
    faction: str = "PLAYER"
    is_dead: bool = False
    is_break: bool = False
    level: int = 1
    shield: int = 0
    speed: int = 0
    #: keyword -> [stacks, turns]
    buffs: Dict[str, List[int]] = field(default_factory=dict)
    passives: List[int] = field(default_factory=list)
    keywords: Tuple[str, ...] = ()

    def to_info(self, applied: bool = True) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "origin_id": self.origin_id,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "mp": self.mp,
            "position": self.position,
            "is_dead": self.is_dead,
            "is_break": self.is_break,
            "faction": self.faction,
            "is_player": self.is_player,
            "applied": applied,
        }

    def to_state(self, applied: bool = True) -> Dict[str, Any]:
        """``unit.state`` 的形态：info + buff 数量 + 等级。"""
        state = self.to_info(applied)
        state["buff_count"] = len(self.buffs)
        state["level"] = self.level
        return state

    def buff_list(self) -> List[Dict[str, Any]]:
        rows = []
        for keyword, values in self.buffs.items():
            # 例子/测试常写 ``{"SINKING": [8, 3]}``（只给层数与回合），
            # 所以上限走兜底值，而不是硬取 values[2] / values[3]。
            stacks = int(values[0])
            turns = int(values[1]) if len(values) > 1 else 1
            max_stacks = int(values[2]) if len(values) > 2 else 99
            max_turns = int(values[3]) if len(values) > 3 else 99
            rows.append({
                "keyword": keyword,
                "stacks": stacks,
                "turns": turns,
                "max_stacks": max_stacks,
                "max_turns": max_turns,
                "name": keyword.title(),
            })
        rows.sort(key=lambda row: row["keyword"])
        return rows


class MockBattle:
    """最小战斗状态 + 句柄表（与真实内核一样用 ``{"t","h"}``）。"""

    def __init__(self, units: Optional[List[MockUnit]] = None, round_: Optional[int] = 1) -> None:
        self.units: List[MockUnit] = list(units or [])
        self.round = round_
        self._handles: Dict[int, MockUnit] = {}
        self._next_handle = 0x1000
        for unit in self.units:
            self.add(unit)

    def add(self, unit: MockUnit) -> MockUnit:
        self.units.append(unit) if unit not in self.units else None
        self._handles[self._next_handle] = unit
        self._next_handle += 1
        return unit

    def handle_of(self, unit: MockUnit) -> int:
        for handle, candidate in self._handles.items():
            if candidate is unit:
                return handle
        raise KeyError(unit)

    def ref(self, unit: MockUnit) -> Dict[str, Any]:
        return {"t": "unit", "h": self.handle_of(unit)}

    def resolve(self, ref: Any) -> MockUnit:
        if isinstance(ref, MockUnit):
            return ref
        if isinstance(ref, dict):
            if "h" in ref:
                unit = self._handles.get(int(ref.get("h") or 0))
                if unit is None:
                    raise KeyError("stale unit handle %r" % (ref,))
                return unit
            instance_id = int(ref.get("instance_id") or 0)
            for unit in self.units:
                if unit.instance_id == instance_id:
                    return unit
            raise KeyError("no unit with instance_id=%r" % (instance_id,))
        raise KeyError("unresolvable unit ref %r" % (ref,))


class MockOps:
    """13 个 A 层 op 的参考实现（语义与 C# 侧一致）。"""

    def __init__(self, battle: MockBattle) -> None:
        self.battle = battle
        #: 由 op 触发的时机事件；harness 负责把它们交给 mod runtime
        self.events: List[Dict[str, Any]] = []
        self.calls: List[str] = []

    # -- 分派 ---------------------------------------------------------------------------

    def dispatch(self, op: str, args: Optional[Dict[str, Any]]) -> Any:
        args = dict(args or {})
        self.calls.append(op)
        handler = getattr(self, "op_" + op.replace(".", "_"), None)
        if handler is None:
            raise KeyError("mock kernel has no op %r" % (op,))
        return handler(args)

    def drain_events(self) -> List[Dict[str, Any]]:
        events, self.events = self.events, []
        return events

    # -- 读 -----------------------------------------------------------------------------

    def op_unit_info(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """读单位扁平状态（hp/max_hp/position/is_dead/faction/...），一次调用拿全。"""
        return self.battle.resolve(args.get("unit")).to_info()

    def op_unit_state(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """读单位状态：info 的超集（多带 buff 数量与等级）。"""
        return self.battle.resolve(args.get("unit")).to_state()

    def op_unit_set_sp(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """设置理智值（ChangeMp 语义：直接设为目标值）。"""
        unit = self.battle.resolve(args.get("unit"))
        if unit.is_dead:
            return unit.to_info(applied=False)
        unit.mp = int(args.get("sp") or 0)
        return unit.to_info()

    def op_unit_add_shield(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """加护盾（AddShield 语义：累加）。"""
        unit = self.battle.resolve(args.get("unit"))
        value = int(args.get("value") or 0)
        if value <= 0 or unit.is_dead:
            return unit.to_info(applied=False)
        unit.shield += value
        return unit.to_info()

    def op_unit_set_speed(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """改速度并重算行动顺序（mock 里速度不影响任何东西，只记账）。"""
        unit = self.battle.resolve(args.get("unit"))
        unit.speed = int(args.get("value") or 0)
        return unit.to_info()

    def op_unit_refresh_speed(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """只重算行动顺序（mock 无操作）。"""
        return self.battle.resolve(args.get("unit")).to_info()

    def op_unit_break(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """强制崩溃（break）：置标记并播 UnitBroken。"""
        unit = self.battle.resolve(args.get("unit"))
        if unit.is_dead:
            return unit.to_info(applied=False)
        if not unit.is_break:
            unit.is_break = True
            self.events.append({
                "timing": "UnitBroken",
                "payload": {
                    "unit": self.battle.ref(unit), "attacker": None,
                    "is_forced": True,
                    "timing": _enum_name(args.get("timing"), "ON_SUCCESS_ATTACK"),
                },
            })
        return unit.to_info()

    def op_unit_buff_stacks(self, args: Dict[str, Any]) -> int:
        """读某关键字的 Buff 层数（按主关键字匹配；没有则 0）。"""
        unit = self.battle.resolve(args.get("unit"))
        entry = unit.buffs.get(_enum_name(args.get("keyword"), ""))
        return int(entry[0]) if entry else 0

    def op_unit_buff_turns(self, args: Dict[str, Any]) -> int:
        """读某关键字的 Buff 剩余回合（没有则 0）。"""
        unit = self.battle.resolve(args.get("unit"))
        entry = unit.buffs.get(_enum_name(args.get("keyword"), ""))
        return int(entry[1]) if entry else 0

    def op_unit_buffs(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """列出单位身上的全部 Buff（扁平，无引用）。"""
        return self.battle.resolve(args.get("unit")).buff_list()

    def op_unit_has_keyword(self, args: Dict[str, Any]) -> bool:
        """单位是否带某个单位关键字（UNIT_KEYWORD 名）。"""
        unit = self.battle.resolve(args.get("unit"))
        wanted = _enum_name(args.get("keyword"), "")
        return wanted in {str(item).upper() for item in unit.keywords}

    def op_battle_units(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """全场单位的扁平快照，按 instance_id 升序。"""
        return [unit.to_info() for unit in sorted(self.battle.units, key=lambda u: u.instance_id)]

    def op_battle_states(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """全场单位的富状态（info + buff 数量 + 等级），按 instance_id 升序。"""
        return [unit.to_state() for unit in sorted(self.battle.units, key=lambda u: u.instance_id)]

    def op_battle_players(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """我方单位的扁平快照。"""
        rows = [u.to_info() for u in self.battle.units if u.is_player]
        rows.sort(key=lambda row: row["instance_id"])
        return rows

    def op_battle_enemies(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """敌方单位的扁平快照。"""
        rows = [u.to_info() for u in self.battle.units if not u.is_player]
        rows.sort(key=lambda row: row["instance_id"])
        return rows

    def op_battle_damage(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """对一组单位同时造成伤害（单目标失败不影响其它）。"""
        return self._apply_many(args, damage=True)

    def op_battle_heal(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """对一组单位同时治疗。"""
        return self._apply_many(args, damage=False)

    def _apply_many(self, args: Dict[str, Any], damage: bool) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        amount = int(args.get("amount") or 0)
        for ref in args.get("units") or []:
            try:
                unit = self.battle.resolve(ref)
            except KeyError:
                continue
            if unit.is_dead or amount <= 0:
                rows.append(unit.to_info(applied=False))
                continue
            before = unit.hp
            if damage:
                self.op_unit_damage({"unit": ref, "amount": amount,
                                     "source": args.get("source"), "timing": args.get("timing")})
            else:
                self.op_unit_heal({"unit": ref, "amount": amount, "timing": args.get("timing")})
            rows.append(unit.to_info(applied=unit.hp != before))
        return rows

    def op_rng_int(self, args: Dict[str, Any]) -> int:
        """闭区间整数随机数。"""
        import random
        low = int(args.get("min") or 0)
        high = int(args.get("max") or 100)
        return low if high < low else random.randint(low, high)

    def op_rng_chance(self, args: Dict[str, Any]) -> bool:
        """千分位概率判定。"""
        permille = int(args.get("permille") or 0)
        if permille <= 0:
            return False
        if permille >= 1000:
            return True
        import random
        return random.randrange(1000) < permille

    def op_rng_pick(self, args: Dict[str, Any]) -> str:
        """从列表里随机取一个。"""
        items = [str(item) for item in (args.get("items") or [])]
        if not items:
            return ""
        import random
        return random.choice(items)

    def op_battle_round(self, args: Dict[str, Any]) -> Optional[int]:
        """当前回合数（拿不到时 None）。"""
        return self.battle.round

    def op_content_plan(self, args: Dict[str, Any]) -> bool:
        """接收内容位图计划（真实内核按它算 mask_victim / mask_self）。

        mock 里只记录下来，并提供 :meth:`mask_for` 让测试能照同一份计划算位图 ——
        这样"零往返 buff 判断"这条链路在无游戏环境下也是可测的。
        """
        self.plan = {
            "mod": args.get("mod"),
            "watch": list(args.get("watch") or []),
            "hooks": list(args.get("hooks") or []),
        }
        return True

    def op_content_register(self, args: Dict[str, Any]) -> bool:
        """登记自定义 Buff（真实内核会把关键字真注入游戏枚举）。"""
        self.injected = list(args.get("keywords") or [])
        self.injected_icons = list(args.get("icons") or [])
        return True

    def mask_for(self, timing: str, unit: "MockUnit", role: str = "victim") -> int:
        """按计划算出某个单位在某个时点的位图（与 C# 的 MaskPlan.Mask 同语义）。"""
        plan = getattr(self, "plan", None)
        if not plan:
            return 0
        watch = plan["watch"]
        mask = 0
        for entry in plan["hooks"]:
            if entry.get("timing") != timing:
                continue
            for bit in entry.get(role) or []:
                if 0 <= int(bit) < len(watch):
                    keyword = str(watch[int(bit)]).upper()
                    if unit.buffs.get(keyword) and int(unit.buffs[keyword][0]) > 0:
                        mask |= 1 << int(bit)
        return mask

    # -- 写 -----------------------------------------------------------------------------

    def op_unit_damage(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """造成伤害：扣血 → 播 DamageDealt；血量归零再播 UnitDied。"""
        unit = self.battle.resolve(args.get("unit"))
        amount = int(args.get("amount") or 0)
        source = _enum_name(args.get("source"), "PASSIVE")
        timing = _enum_name(args.get("timing"), "ON_SUCCESS_ATTACK")
        if amount <= 0 or unit.is_dead:
            return unit.to_info(applied=False)
        old_hp = unit.hp
        new_hp = max(0, old_hp - amount)
        unit.hp = new_hp
        self.events.append({
            "timing": "DamageDealt",
            "payload": {
                "unit": self.battle.ref(unit),
                "attacker": args.get("attacker"),
                "old_hp": old_hp,
                "new_hp": new_hp,
                "source": source,
                "timing": timing,
                "keyword": "",
            },
        })
        if new_hp <= 0 and not unit.is_dead:
            unit.is_dead = True
            self.events.append({
                "timing": "UnitDied",
                "payload": {"unit": self.battle.ref(unit), "killer": args.get("attacker"),
                            "source": source, "timing": timing},
            })
        return unit.to_info()

    def op_unit_heal(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """治疗：不超上限 → 播 DamageDealt（healed 方向）。"""
        unit = self.battle.resolve(args.get("unit"))
        amount = int(args.get("amount") or 0)
        timing = _enum_name(args.get("timing"), "ON_SUCCESS_ATTACK")
        if amount <= 0 or unit.is_dead:
            return unit.to_info(applied=False)
        old_hp = unit.hp
        unit.hp = min(unit.max_hp, old_hp + amount)
        if unit.hp != old_hp:
            self.events.append({
                "timing": "DamageDealt",
                "payload": {
                    "unit": self.battle.ref(unit), "attacker": None,
                    "old_hp": old_hp, "new_hp": unit.hp,
                    "source": "BUFF", "timing": timing, "keyword": "",
                },
            })
        return unit.to_info()

    def op_unit_set_hp(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """把 HP 直接设为目标值（走 OnChangeHp 语义）→ 播 DamageDealt。"""
        unit = self.battle.resolve(args.get("unit"))
        target = max(0, min(int(args.get("hp") or 0), unit.max_hp))
        old_hp = unit.hp
        unit.hp = target
        unit.is_dead = target <= 0
        self.events.append({
            "timing": "DamageDealt",
            "payload": {
                "unit": self.battle.ref(unit), "attacker": None,
                "old_hp": old_hp, "new_hp": target,
                "source": _enum_name(args.get("source"), "PASSIVE"),
                "timing": _enum_name(args.get("timing"), "ON_SUCCESS_ATTACK"),
                "keyword": "",
            },
        })
        return unit.to_info()

    def op_unit_add_passive(self, args: Dict[str, Any]) -> bool:
        """按被动 id 施加被动（重复施加幂等）。"""
        unit = self.battle.resolve(args.get("unit"))
        passive_id = int(args.get("passive_id") or 0)
        if passive_id <= 0:
            return False
        if passive_id not in unit.passives:
            unit.passives.append(passive_id)
        return True

    def op_unit_burst_by_buff(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """叠层换伤害：读层数→夹上限→算目标 HP→改血，全部在一个时间片里。"""
        unit = self.battle.resolve(args.get("unit"))
        keyword = _enum_name(args.get("keyword"), "")
        per_stack = int(args.get("per_stack") or 3)
        max_stacks = int(args.get("max_stacks") or 6)
        entry = unit.buffs.get(keyword)
        stacks = int(entry[0]) if entry else 0
        used = max(0, min(stacks, max_stacks))
        info = {
            "stacks": used,
            "damage": used * per_stack,
            "hp_before": unit.hp,
            "hp_after": unit.hp,
            "applied": False,
        }
        if used <= 0 or unit.is_dead:
            return info
        before = unit.hp
        after = max(0, before - used * per_stack)
        unit.hp = after
        if entry is not None:
            entry[0] = max(0, stacks - used)
        info["hp_after"] = after
        info["applied"] = True
        self.events.append({
            "timing": "DamageDealt",
            "payload": {
                "unit": self.battle.ref(unit), "attacker": None,
                "old_hp": before, "new_hp": after,
                "source": _enum_name(args.get("source"), "PASSIVE"),
                "timing": "ON_SUCCESS_ATTACK", "keyword": keyword,
            },
        })
        if after <= 0 and not unit.is_dead:
            unit.is_dead = True
            self.events.append({
                "timing": "UnitDied",
                "payload": {"unit": self.battle.ref(unit), "killer": None,
                            "source": "PASSIVE", "timing": "ON_SUCCESS_ATTACK"},
            })
        return info

    def op_unit_add_buff(self, args: Dict[str, Any]) -> bool:
        """加 Buff（实验性）：层数/回合按直觉给。"""
        unit = self.battle.resolve(args.get("unit"))
        keyword = _enum_name(args.get("keyword"), "")
        if not keyword or unit.is_dead:
            return False
        stacks = max(0, int(args.get("stacks") or 1))
        turns = max(0, int(args.get("turns") or 1))
        max_stacks, max_turns = BUFF_DEFAULTS.get(keyword, (99, 99))
        entry = unit.buffs.setdefault(keyword, [0, 0, max_stacks, max_turns])
        entry[0] = min(max_stacks, int(entry[0]) + stacks)
        entry[1] = max(int(entry[1]), turns)
        return True
