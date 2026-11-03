"""示例 2：``damage_ledger`` —— **只靠时机推送，0 次往返**。

这个示例故意不调用任何动作：

* ``@lp.on_damage`` —— 每次 HP 变化（含其它 mod / 游戏本身造成的）都带完整分解
  （``old_hp`` / ``new_hp`` / ``source`` / ``keyword``），记账不需要再回查；
* ``@lp.on_death`` —— 死亡事件自带击杀者；
* ``@lp.on_break`` —— 崩溃事件自带施加者与"是否强制"；
* ``@expose`` 报告 —— 纯内存拼装。

它的意义：如果一件事**只读事件里已有的信息**，这一层可以做到零往返 ——
这就是「推送模型」相对「反射轮询」的本质优势。只有当你要判断"血最少的敌人在哪"
这种需要额外状态的问句时，才值得花 1 次往返去拿快照。
"""

from __future__ import annotations

import collections
from typing import Any, Dict, List

import limbuspy as lp
from limbuspy.mod import Mod, config, expose


class DamageLedger(Mod):
    name = "damage_ledger"
    version = "1.0.0"
    description = "记录全场的伤害与控制（纯时机推送示例，0 次往返）"

    cfg = config(
        keep_recent=8,        # 最近多少条明细
        count_overkill=True,  # 是否把过量伤害单独统计
    )

    def __init__(self) -> None:
        super().__init__()
        self.totals: Dict[str, Dict[str, int]] = {}
        self.recent: collections.deque = collections.deque(maxlen=32)
        self.deaths: List[Dict[str, Any]] = []
        self.breaks: List[Dict[str, Any]] = []
        self.overkill = 0

    # -- 时机 ---------------------------------------------------------------------------

    @lp.on_damage
    def on_damage(self, ev) -> None:
        """一次 HP 变化。payload 已经是拆好的字段，不需要任何动作调用。"""
        if ev.unit is None:
            return
        lost = int(ev.old_hp) - int(ev.new_hp)
        row = self.totals.setdefault(ev.unit.key, {"taken": 0, "healed": 0, "hits": 0})
        if lost > 0:
            row["taken"] += lost
            row["hits"] += 1
            if self.cfg.count_overkill and int(ev.new_hp) <= 0:
                self.overkill += max(0, lost - int(ev.old_hp))
        elif lost < 0:
            row["healed"] += -lost
        self.recent.append({
            "unit": ev.unit.key,
            "from": int(ev.old_hp),
            "to": int(ev.new_hp),
            "source": ev.source,
            "keyword": ev.keyword,
        })

    @lp.on_death
    def on_died(self, ev) -> None:
        self.deaths.append({
            "unit": ev.unit.key if ev.unit else "?",
            "killer": ev.killer.key if ev.killer else None,
            "source": ev.source,
        })

    @lp.on_break
    def on_broken(self, ev) -> None:
        self.breaks.append({
            "unit": ev.unit.key if ev.unit else "?",
            "attacker": ev.attacker.key if ev.attacker else None,
            "forced": bool(ev.is_forced),
        })

    # -- 报告 ---------------------------------------------------------------------------

    @expose("ledger.report")
    def report(self, ctx) -> Dict[str, Any]:
        totals = {
            "taken": sum(row["taken"] for row in self.totals.values()),
            "healed": sum(row["healed"] for row in self.totals.values()),
            "hits": sum(row["hits"] for row in self.totals.values()),
        }
        return {
            "mod": self.name,
            "totals": totals,
            "overkill": self.overkill,
            "units": dict(sorted(self.totals.items())),
            "deaths": list(self.deaths),
            "breaks": list(self.breaks),
            "recent": list(self.recent)[-int(self.cfg.keep_recent):],
        }

    @expose("ledger.reset")
    def reset(self, ctx) -> Dict[str, Any]:
        self.totals.clear()
        self.recent.clear()
        self.deaths.clear()
        self.breaks.clear()
        self.overkill = 0
        return {"ok": True}


__all__ = ["DamageLedger"]
