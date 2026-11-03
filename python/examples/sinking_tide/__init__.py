"""沉溺潮汐 (sinking_tide) —— 「深水区」示例：门面 + 门面覆盖不到的那两层。

它把一件事做完整，而不是把 API 罗列一遍。做的事情是：

    把敌人身上的 SINKING（沉溺）叠层，在合适的时机换成一次真实的 HP 损失，
    并把整场战斗的「谁被打了多少 / 叠层怎么涨 / 谁触发了爆发」记成一份可查询的报告。

这个示例和 ``burst_on_hit`` / ``damage_ledger`` 的区别是**它走到底**：

| 通路 | 这个示例用在哪 |
|---|---|
| ``@lp.on_hit`` / ``@lp.on_damage`` | 命中与沉溺伤害的记账（类型化 payload，0 往返） |
| ``lp.unit(...)`` 快照 | 一次往返看清一个单位，之后读属性全免费 |
| ``lp.burst(...)`` | 复合动作：叠层 → 伤害，在同一个主线程时间片里 |
| ``StaticData`` | 用 ``BuffData`` 校准叠层上限（静态表不在门面里） |
| ``Mod.patch`` | 监听游戏自己的**每一次** HP 变化（含非本 mod 造成的）—— 门面没有这条路 |
| ``@hook(sync=True)`` | 同步泳道：阻塞主线程要一个倍率 —— 门面刻意不提供 |
| ``config`` / ``on_save`` | 配置与热重载不丢账本 |
| ``@expose`` | 报告给内核 / REPL / 其它 mod |

**为什么后三条不在门面里**：它们不是"更长一点的 API"，而是**不同的成本模型**。

* Harmony 补丁跑在游戏方法内部，属于"改游戏行为"而不是"调游戏功能"；
* 同步泳道会阻塞游戏主线程等 Python 回包（硬预算 20 ms），用错就是掉帧和判定错乱。

把这两样混进 ``lp.*``（作者每天都要用的东西）会让"这里可能有坑"的信号消失。
所以它们留在 ``limbuspy.mod``，并且需要显式写出来。
"""

from __future__ import annotations

import collections
import os
from typing import Any, Dict, List, Optional

import limbuspy as lp
from limbuspy.game import statics as gs
from limbuspy.mod import Mod, config, expose
from limbuspy.mod.decorators import hook          # 同步泳道（高级层）

#: 沉溺的 BuffModel 主关键字（枚举名是 Sinking，比较时大写化）
SINKING = "SINKING"

#: OnChangeHp 的参数表（用于 Harmony 补丁的签名消歧）。逐字取自
#: ``il2csharp/out/limbus/full0/Assembly-CSharp/ChangeStat.cs`` 的实现签名。
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
        self.ticks = 0
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
            "sinking_ticks": self.ticks,
        }

    def top(self, limit: int = 5) -> List[dict]:
        rows = [{"unit": key, **row} for key, row in self.units.items()]
        rows.sort(key=lambda item: (item["damage"], item["hits"]), reverse=True)
        return rows[:limit]

    # -- 持久化 -------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"units": self.units, "bursts": self.bursts, "damage": self.damage,
                "hp_events": self.hp_events, "ticks": self.ticks}

    def load(self, data: dict) -> None:
        units = (data or {}).get("units")
        if isinstance(units, dict):
            for key, row in units.items():
                if isinstance(row, dict):
                    base = self._row(key)
                    for field, value in row.items():
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            base[field] = int(value)
        for field in ("bursts", "damage", "hp_events", "ticks"):
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
        amplify_take_damage=False,  # 同步泳道：是否放大受到的伤害
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

        self.log("阈值 %d 层 / 每层 %d 伤害 / 沉溺上限 %d"
                 % (self.cfg.threshold, self.cfg.damage_per_stack, self._sinking_max_stack))

    def on_save(self) -> dict:
        # 热重载：账本 + 潮汐是否已就绪必须活下来，否则重载一次就清零。
        return {"ledger": self.ledger.to_dict(), "tide_armed": self.tide_armed,
                "last_burst_round": self.last_burst_round}

    def on_restore(self, state: dict) -> None:
        self.ledger = Ledger(state.get("ledger"))
        self.tide_armed = bool(state.get("tide_armed"))
        self.last_burst_round = int(state.get("last_burst_round") or 0)
        self.log("恢复账本: %d 命中 / %d 次爆发 / %d 伤害"
                 % (self.ledger.totals()["hits"], self.ledger.bursts, self.ledger.damage))

    # -- 静态数据表 ---------------------------------------------------------------------
    def _load_statics(self) -> None:
        """读 BuffData：叠层上限。缺表不是错误，只是少一点精度。"""
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

        self.log("静态表 %d 张（%s）" % (len(db), root))

    # -- 时机钩子：命中 ----------------------------------------------------------------
    @lp.on_hit
    def on_hit(self, ev) -> None:
        """一次命中：记账，然后用**一次快照**决定要不要爆发。"""
        if ev.target is None:
            return

        enemy = lp.unit(ev.target)              # 1 次往返，之后读多少属性都免费
        uid = str(enemy.instance_id) if enemy is not None else ev.target.key
        self.ledger.hit(uid, head=bool(ev.is_critical), crit=bool(ev.is_critical))
        if ev.is_critical:
            self.tide_armed = True

        self._evaluate(ev.target, enemy, uid)

    # -- 时机钩子：沉溺自己掉血 ---------------------------------------------------------
    #
    # ``DamageDealt`` 带着 ``keyword``（这次 HP 变化是哪个 buff 造成的）。用声明式谓词
    # 只看关键字带 SINKING 的那些 —— 谓词读的是 payload，**0 次往返**，
    # 而且不满足时函数体根本不会跑。

    @lp.on_damage(when=lp.ev.keyword == SINKING)
    def on_sinking_tick(self, ev) -> None:
        self.tide_armed = True
        self.ledger.ticks += 1

    # -- 同步泳道：伤害倍率 -------------------------------------------------------------
    @hook("ChangeTakeDamage", sync=True, fallback=100)
    def take_damage_multiplier(self, ctx) -> int:
        """返回 100 = 不变，115 = +15%。

        同步钩子会**阻塞游戏主线程**等 Python 回包（默认 20 ms）。所以这里只读内存里的
        两个字段，绝不调用任何 op —— 任何往返都可能撞上预算而落到 fallback。
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
                        "sinking_max_stack": self._sinking_max_stack},
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
        target = lp.unit(int(args[0]))
        if target is None:
            raise ValueError("战场里没有 instance_id=%r" % (args[0],))
        stacks = target.buff_stack(SINKING) or int(self.cfg.threshold)
        return self._burst(target, stacks)

    # -- 决策与执行 ---------------------------------------------------------------------
    def _evaluate(self, ref, enemy, uid: str) -> Optional[dict]:
        """判定要不要爆发。

        ``enemy`` 是刚才那次快照（可能为 None：单位在两次读之间消失了）。
        这里不再读任何别的东西 —— 需要全场视角的版本见 ``docs/02-mod-authoring.md``
        里 ``lp.battle()`` 的写法（一次往返拿全场，而不是逐单位 N 次）。
        """
        if enemy is None or enemy.is_dead:
            return None

        stacks = enemy.buff_stack(SINKING)      # 只读这一个关键字，不拉整张 buff 表
        if stacks < int(self.cfg.threshold):
            return None
        if enemy.hp_ratio > float(self.cfg.hp_ratio_ceiling):
            return None

        round_ = int(lp.round() or 0)
        if self.cfg.once_per_round and self.last_burst_round == round_:
            return None
        return self._burst(ref, stacks)

    def _burst(self, ref, stacks: int) -> dict:
        """把叠层换成伤害。这是本 mod 唯一"真的动了游戏状态"的地方。"""
        stacks = max(0, min(int(stacks), int(self._sinking_max_stack)))
        damage = min(stacks * int(self.cfg.damage_per_stack),
                     int(self.cfg.max_damage_per_burst))
        round_ = int(lp.round() or 0)

        result: Dict[str, Any] = {
            "unit": str(ref), "round": round_, "stacks": stacks, "damage": damage,
            "dry_run": bool(self.cfg.dry_run), "applied": [], "errors": [],
        }
        if self.cfg.dry_run:
            self.ledger.burst(str(ref), damage, round_)
            self.last_burst_round = round_
            self.log("潮汐爆发(dry_run) %s: %d 层 → %d 伤害" % (ref, stacks, damage))
            return result

        # ``lp.damage`` 收的是"扣多少"，不是 ``ChangeHp`` 的"目标 HP 值" ——
        # 方向、枚举、关键字哨兵都在 C# 侧封好了。
        action = lp.damage(ref, damage, source="PASSIVE")
        if not action.ok:
            result["errors"].append(action.reason or "未知")
            self.log("伤害没有生效: %s" % (action.reason or "未知",))
            return result

        result["applied"].append("damage")
        if action.state is not None:
            result["hp"] = [action.state.hp, action.state.hp]
        self.ledger.burst(str(ref), damage, round_)
        self.last_burst_round = round_
        self.tide_armed = False

        self.log("潮汐爆发 %s: %d 层 → %d 伤害（剩余 HP %s）"
                 % (ref, stacks, damage,
                    action.state.hp if action.state is not None else "?"))
        return result


#: 加载器会挑本模块里"最派生"的 Mod 子类，这里把基类也导出只是为了让 `from . import Ledger` 可用。
__all__ = ["SinkingTide", "Ledger"]

