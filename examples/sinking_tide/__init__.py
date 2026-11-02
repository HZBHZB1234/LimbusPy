"""沉溺潮汐 (sinking_tide) —— 一个"较复杂"的 LimbusPy 示例 mod。

它把一件事做完整，而不是把 API 罗列一遍。做的事情是：

    把敌人身上的 SINKING（沉溺）叠层，在合适的时机换成一次真实的 HP 损失，
    并把整场战斗的"谁被打了多少 / 叠层怎么涨 / 谁触发了爆发"记成一份可查询的报告。

这样它必须同时用到 LimbusPy 的六条主要通路（这也是本示例的意义）：

    1. @hook(时机)       —— 命中与吃 buff 的批量异步回调（第 0 条成本）
    2. battle 快照        —— 一次往返看清全场，之后读 hp/instance_id 全部免费
    3. 反射调用           —— ChangeHp / AddPassive（真实方法名 + 真实签名，见 _deal_damage）
    4. Harmony 补丁       —— 监听游戏自己的每一次 HP 变化（含非本 mod 造成的）
    5. 同步泳道           —— ChangeTakeDamage 返回倍率（阻塞，硬预算 20 ms，只读内存）
    6. 静态数据表         —— 用 BuffData/PassiveData 校准叠层上限、解析被动名

再加上 config / on_save / on_restore，因此热重载（改文件即生效）不会丢账本。

成员名全部来自 limbuspy/game/members.py（对着真实 dump 校验过），
不在表里的成员一律走 unit.call(...) 逃生口，并在注释里写明它在 dump 里的真实签名。
"""

from __future__ import annotations

import collections
import os
from typing import Any, Dict, List, Optional

from limbuspy.game import Unit
from limbuspy.game import battle as gb
from limbuspy.game import statics as gs
from limbuspy.mod import Mod, config, expose, hook
from limbuspy.runtime.errors import BridgeError

#: 沉溺的 BuffModel 主关键字（枚举名是 Sinking，比较时大写化，见 Unit.buff_stack）
SINKING = "SINKING"

#: BUFF_UNIQUE_KEYWORD 里 "无关键字" 的哨兵值（dump: ChangeHp 的默认参数 2123）
NO_KEYWORD = 2123

#: OnChangeHp 的参数个数（用于补丁签名消歧）
_ON_CHANGE_HP_SIG = ["int", "int", "DAMAGE_SOURCE_TYPE", "BATTLE_EVENT_TIMING",
                     "BattleUnitModel", "BattleActionModel", "BUFF_UNIQUE_KEYWORD"]

_MOD_DIR = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------------------
# 账本：纯数据结构，不碰桥，因此可以随便单元测试
# --------------------------------------------------------------------------------------
class Ledger:
    """整场战斗的记账。

    按 unit 分桶，键用 ``instance_id``（快照自带，取它不需要往返）；没有 instance_id 时
    退化成 ``h:<handle>``。全部字段都是 JSON 原生类型，所以 ``to_dict()`` 直接可持久化。
    """

    def __init__(self, data: Optional[dict] = None) -> None:
        self.units: Dict[str, dict] = {}
        self.bursts = 0
        self.damage = 0
        self.hp_events = 0
        self.gained = 0
        if data:
            self.load(data)

    # -- 写入 ---------------------------------------------------------------------------
    def hit(self, uid: str, head: bool = False, crit: bool = False) -> dict:
        row = self._row(uid)
        row["hits"] += 1
        row["heads"] += 1 if head else 0
        row["crits"] += 1 if crit else 0
        return row

    def burst(self, uid: str, damage: int, round_: int = 0) -> dict:
        row = self._row(uid)
        row["bursts"] += 1
        row["damage"] += int(damage)
        self.bursts += 1
        self.damage += int(damage)
        if round_:
            row["last_burst_round"] = int(round_)
        return row

    def _row(self, uid: str) -> dict:
        key = str(uid)
        return self.units.setdefault(key, {
            "hits": 0, "heads": 0, "crits": 0, "bursts": 0, "damage": 0, "last_burst_round": 0,
        })

    # -- 读取 ---------------------------------------------------------------------------
    def totals(self) -> dict:
        return {
            "units": len(self.units),
            "hits": sum(row["hits"] for row in self.units.values()),
            "heads": sum(row["heads"] for row in self.units.values()),
            "crits": sum(row["crits"] for row in self.units.values()),
            "bursts": self.bursts,
            "damage": self.damage,
            "hp_events": self.hp_events,
            "sinking_gained": self.gained,
        }

    def top(self, limit: int = 5) -> List[dict]:
        rows = [{"unit": key, **row} for key, row in self.units.items()]
        rows.sort(key=lambda item: (item["damage"], item["hits"]), reverse=True)
        return rows[:limit]

    # -- 持久化 -------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"units": self.units, "bursts": self.bursts, "damage": self.damage,
                "hp_events": self.hp_events, "gained": self.gained}

    def load(self, data: dict) -> None:
        units = (data or {}).get("units")
        if isinstance(units, dict):
            for key, row in units.items():
                if isinstance(row, dict):
                    base = self._row(key)
                    for field, value in row.items():
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            base[field] = int(value)
        for field in ("bursts", "damage", "hp_events", "gained"):
            value = (data or {}).get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(self, field, int(value))


# --------------------------------------------------------------------------------------
# mod 本体
# --------------------------------------------------------------------------------------
class SinkingTide(Mod):
    name = "sinking_tide"
    version = "1.0.0"
    description = "把 SINKING 叠层换成一次真实伤害，并输出战斗报告"

    #: 有默认值、会持久化到 LimbusPy/.state/sinking_tide.json、可从热重载中存活。
    cfg = config(
        threshold=6,            # 触发爆发所需沉溺层数
        damage_per_stack=3,     # 每层换成多少 HP
        hp_ratio_ceiling=0.5,   # 只打血少的：hp/max_hp 上限
        max_damage_per_burst=120,
        once_per_round=True,    # 同一轮只爆发一次
        burst_passive_id=0,     # 额外施加的被动 id（0 = 不施加）
        amplify_take_damage=False,  # 同步钩子：是否放大队友受到的伤害
        watch_hp_changes=True,  # 是否打补丁监听全场的 HP 变化
        dry_run=False,          # 只记账、不动手（排障用）
        statics_dir="",         # 留空 = 用 mod 自带的 data/ 子目录
    )

    def __init__(self) -> None:
        super().__init__()
        self.ledger = Ledger()
        #: 最近的 HP 变化（补丁回调里 append，硬预算内只做 O(1) 操作）
        self.hp_events: collections.deque = collections.deque(maxlen=32)
        self.tide_armed = False
        self.last_burst_round = 0
        self._sinking_max_stack = 99
        self._passive_name = ""
        self._statics_root = ""

    # -- 生命周期 -----------------------------------------------------------------------
    def on_load(self) -> None:
        self._load_statics()

        if self.cfg.watch_hp_changes:
            # 补丁装在这里而不是 __init__ 里：重载前基类会 unpatch_all()，旧代码不会被再调用。
            patch_id = self.patch(
                "BattleUnitModel", "OnChangeHp", "sinking_tide.on_hp_changed",
                kind="prefix", sig=_ON_CHANGE_HP_SIG,
            )
            self.log("监听 HP 变化: %s" % patch_id)
        else:
            self.log("HP 变化监听已关闭")

        self.log("阈值 %d 层 / 每层 %d 伤害 / 沉溺上限 %d / 被动 %s"
                 % (self.cfg.threshold, self.cfg.damage_per_stack,
                    self._sinking_max_stack, self._passive_name or "(无)"))

    def on_save(self) -> dict:
        # 热重载：账本 + 潮汐是否已就绪必须活下来，否则重载一次就清零。
        return {"ledger": self.ledger.to_dict(), "tide_armed": self.tide_armed,
                "last_burst_round": self.last_burst_round}

    def on_restore(self, state: dict) -> None:
        self.ledger = Ledger(state.get("ledger"))
        self.tide_armed = bool(state.get("tide_armed"))
        self.last_burst_round = int(state.get("last_burst_round") or 0)
        self.log("恢复账本: %df 命中 / %d 次爆发 / %d 伤害"
                 % (self.ledger.totals()["hits"], self.ledger.bursts, self.ledger.damage))

    # -- 静态数据表 ---------------------------------------------------------------------
    def _load_statics(self) -> None:
        """读 BuffData/PassiveData：叠层上限与被动名。缺表不是错误，只是少一点精度。"""
        root = self.cfg.statics_dir or os.path.join(_MOD_DIR, "data")
        self._statics_root = root
        try:
            db = gs.open_tables(root)
        except Exception as exc:  # noqa: BLE001 - 静态表缺失绝不能让 mod 加载失败
            self.log("静态表不可用 (%s): %s" % (root, exc))
            return

        table = db.table("BuffData")
        if table is not None:
            for row in table.rows():
                if str(row.get("keyword") or "").upper() == SINKING:
                    self._sinking_max_stack = int(row.get("maxStack") or self._sinking_max_stack)
                    break

        if self.cfg.burst_passive_id:
            passive = db.table("PassiveData")
            record = passive.get(self.cfg.burst_passive_id) if passive is not None else None
            if isinstance(record, dict):
                self._passive_name = str(record.get("name") or "")

        self.log("静态表 %d 张（%s）" % (len(db), root))

    # -- 时机钩子：命中 ----------------------------------------------------------------
    @hook("OnSucceedAttack", priority=10)
    def on_hit(self, ctx) -> None:
        """一次命中。做完记账后，用**一次快照**决定要不要爆发。"""
        if ctx.target is None:
            return

        book = gb.current(ctx.session)          # 1 次 SNAPSHOT 往返，之后全场读取都是内存操作
        unit = self._find_unit(book, ctx.target)

        head = bool(ctx.event.get("head"))
        crit = bool(ctx.event.get("crit"))
        uid = self._uid(unit, ctx.target)
        self.ledger.hit(uid, head=head, crit=crit)
        if head:
            self.tide_armed = True

        # 爆发判定复用同一份快照：不做第二次往返。
        self._evaluate(ctx, book, uid)

    # -- 时机钩子：吃到沉溺 -------------------------------------------------------------
    @hook("OnGainBuff", keyword=SINKING)
    def on_sinking_gained(self, ctx) -> None:
        """沉溺叠层变化。ctx.state 是**本 hook 私有**的字典，且跨热重载保留。"""
        self.tide_armed = True
        self.ledger.gained += 1
        ctx.state["gained"] = int(ctx.state.get("gained") or 0) + 1

    # -- 同步泳道：伤害倍率 -------------------------------------------------------------
    @hook("ChangeTakeDamage", sync=True, fallback=100)
    def take_damage_multiplier(self, ctx) -> int:
        """返回 100 = 不变，115 = +15%。

        同步钩子会**阻塞游戏主线程**等 Python 回包（默认 20 ms）。所以这里只读内存里的两个
        字段，绝不 ctx.call/get —— 任何往返都可能撞上预算而落到 fallback。
        """
        if not self.cfg.amplify_take_damage or not self.tide_armed:
            return 100
        return 100 + max(0, int(self.cfg.damage_per_stack) * 5)

    # -- Harmony 补丁：全场 HP 变化 -----------------------------------------------------
    @expose("sinking_tide.on_hp_changed")
    def on_hp_changed(self, ctx) -> None:
        """``BattleUnitModel.OnChangeHp`` 的 prefix。

        ctx.event = {"patch": ..., "kind": "prefix", "target": "void OnChangeHp(...)",
                     "instance": ref, "args": [oldHp, newHp, dmgSrcType, timing, ...],
                     "result": null}

        同一个方法上的所有 binding 共享一个 Harmony 补丁；返回 None 表示"继续原方法"，
        返回 {"skip": True} 才会吞掉原方法。这里只做观测，返回 None。
        """
        self.ledger.hp_events += 1
        args = ctx.event.get("args") or []
        if len(args) >= 2:
            # 只 append，不做任何桥调用：这是 20 ms 预算内唯一安全的写法。
            self.hp_events.append({
                "old": args[0], "new": args[1],
                "src": args[2] if len(args) > 2 else None,
                "timing": args[3] if len(args) > 3 else None,
            })
        return None

    # -- 暴露给内核 / REPL / 其它 mod ---------------------------------------------------
    @expose("sinking_tide.report")
    def report(self, ctx) -> dict:
        """战斗报告。纯内存拼装，0 次往返（从 REPL、Modular 的 py() 或别的 mod 调用）。"""
        return {
            "mod": self.name,
            "version": self.version,
            "armed": self.tide_armed,
            "last_burst_round": self.last_burst_round,
            "statics": {"root": self._statics_root,
                        "sinking_max_stack": self._sinking_max_stack,
                        "passive": self._passive_name},
            "config": self.cfg.as_dict(),
            "totals": self.ledger.totals(),
            "top": self.ledger.top(),
            "recent_hp_changes": list(self.hp_events)[-5:],
        }

    @expose("sinking_tide.force_burst")
    def force_burst(self, ctx) -> dict:
        """手动触发一次爆发（排障 / 做宏 / 验证用）。

        内核以 SYNCALL 调过来时，参数在 ``ctx.event["args"]`` 里：``[instance_id]``。
        """
        args = ctx.event.get("args") or []
        if not args:
            raise ValueError("force_burst 需要实例 id，例如 force_burst(201)")
        book = gb.current(ctx.session)
        unit = book.unit(int(args[0]))
        if unit is None:
            raise ValueError("战场里没有 instance_id=%r" % (args[0],))
        stacks = unit.buff_stack(SINKING) or int(self.cfg.threshold)
        return self._burst(ctx, unit, stacks, book)

    # -- 决策与执行 ---------------------------------------------------------------------
    def _evaluate(self, ctx, book, attacker_uid: str) -> Optional[dict]:
        """挑一个敌人：沉溺最多的那个；同层数时打血最少的。"""
        candidates = [unit for unit in book.enemy_units if not unit.data.get("is_dead")]
        if not candidates:
            return None

        # buff_stack 会对每个单位各往返一次，但同一 session 内按属性缓存，所以这里最多 1 次/单位。
        scored = []
        for unit in candidates:
            stacks = unit.buff_stack(SINKING)
            scored.append((stacks, -unit.hp_ratio, unit))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_stacks, neg_ratio, best = scored[0]

        if best_stacks < int(self.cfg.threshold):
            return None
        if -neg_ratio > float(self.cfg.hp_ratio_ceiling):
            return None

        round_ = int(book.round)
        if self.cfg.once_per_round and self.last_burst_round == round_:
            return None

        return self._burst(ctx, best, best_stacks, book)

    def _burst(self, ctx, unit, stacks: int, book) -> dict:
        """把叠层换成伤害。这是本 mod 唯一"真的动了游戏状态"的地方。"""
        stacks = max(0, min(int(stacks), int(self._sinking_max_stack)))
        damage = min(stacks * int(self.cfg.damage_per_stack), int(self.cfg.max_damage_per_burst))
        old_hp = int(unit.hp or 0)
        new_hp = max(0, old_hp - damage)
        uid = self._uid(unit, None)
        round_ = int(book.round)

        result = {"unit": uid, "round": round_, "stacks": stacks, "damage": damage,
                  "hp": [old_hp, new_hp], "dry_run": bool(self.cfg.dry_run),
                  "applied": [], "errors": []}

        if not self.cfg.dry_run:
            if self._deal_damage(unit, new_hp, result):
                self.ledger.burst(uid, damage, round_)
                self.last_burst_round = round_
                self.tide_armed = False
            if self.cfg.burst_passive_id:
                self._add_passive(unit, int(self.cfg.burst_passive_id), result)
        else:
            self.ledger.burst(uid, damage, round_)
            self.last_burst_round = round_

        self.log("潮汐爆发 %s: %d 层 → %d 伤害 (hp %d → %d)%s"
                 % (uid, stacks, damage, old_hp, new_hp, " [dry_run]" if self.cfg.dry_run else ""))
        return result

    def _deal_damage(self, unit, new_hp: int, result: dict) -> bool:
        """通过游戏自己的 HP 管线改血，而不是直接写字段。

        dump 里 ``BattleUnitModel.ChangeHp`` 的签名（Il2CppDumper-release/dump.cs:588122）::

            int ChangeHp(int value, DAMAGE_SOURCE_TYPE dmgSrcType, BATTLE_EVENT_TIMING timing,
                         BattleUnitModel attackerOrNull, BattleActionModel actionOrNull,
                         CoinModel coinOrNull, BUFF_UNIQUE_KEYWORD keyword = 2123,
                         bool deactivePassedBreakSection = false, bool ignorePassedBreakSection = false)

        注意 **value 是"目标 HP 值"，不是增量** —— 基类 ``ChangeStat.ChangeHp(int newHp, ...)``
        （il2csharp/out/limbus/full0/Assembly-CSharp/ChangeStat.cs:25）的参数名写得很清楚，
        游戏自己也到处用 ``ChangeHp(unit.MaxHp, ...)`` 来"回满血"。所以扣血要传 old_hp - damage。

        枚举参数过桥时传**成员名**（"PASSIVE"），可选参数也必须显式给全：内核按参数个数
        精确匹配重载，不会替你补默认值。
        """
        try:
            unit.call("ChangeHp", int(new_hp), "PASSIVE", "ON_SUCCESS_ATTACK",
                      None, None, None, NO_KEYWORD, False, False)
            result["applied"].append("ChangeHp")
            return True
        except BridgeError as exc:
            result["errors"].append("ChangeHp: %s" % exc)
            self.log("ChangeHp 失败: %s" % exc)
            return False

    def _add_passive(self, unit, passive_id: int, result: dict) -> None:
        """``BattleUnitModel.AddPassive(int)`` —— 单参数、int、void，最安全的"施加效果"入口。"""
        try:
            unit.call("AddPassive", int(passive_id))
            result["applied"].append("AddPassive(%d)" % passive_id)
        except BridgeError as exc:
            result["errors"].append("AddPassive: %s" % exc)
            self.log("AddPassive(%d) 失败: %s" % (passive_id, exc))

    # -- 小工具 -------------------------------------------------------------------------
    @staticmethod
    def _find_unit(book, target) -> Any:
        """把事件里的主对象换成快照里的 Unit（同一句柄即同一单位）。"""
        handle = int(target.get("h") or 0) if isinstance(target, dict) else int(getattr(target, "handle", 0) or 0)
        if handle:
            for unit in book.units:
                if unit.handle == handle:
                    return unit
        # 快照里没有（例如刚召唤出来的单位）：退化成按需往返的薄包装。
        return Unit(book.session, target) if isinstance(target, dict) else None

    @staticmethod
    def _uid(unit, target) -> str:
        if unit is not None:
            instance_id = unit.data.get("instance_id")
            if instance_id:
                return str(instance_id)
            if int(getattr(unit, "handle", 0) or 0):
                return "h:%d" % int(unit.handle)
        handle = int(target.get("h") or 0) if isinstance(target, dict) else 0
        return "h:%d" % handle if handle else "unknown"


#: 加载器会挑本模块里"最派生"的 Mod 子类，这里把基类也导出只是为了让 `from . import Ledger` 可用。
__all__ = ["SinkingTide", "Ledger"]
