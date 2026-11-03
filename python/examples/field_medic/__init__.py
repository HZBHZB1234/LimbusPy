"""示例 3：``field_medic`` —— 写操作与「读一次、写一次」的成本模型。

做的事：每个单位回合开始时，血量低于阈值就治疗；治疗量随回合计递增；
同时把「治疗了多少、救活了几次」记下来。

展示的点：

* ``lp.unit(ev.unit)`` 读 + ``lp.heal(...)`` 写 = **2 次往返**，且语义在 C# 侧
  （不会写成 ``ChangeHp`` 的"增量还是目标值"那种方向错误，也不会超过上限）；
* ``lp.add_buff`` 是 **experimental** 级动作：示例里默认关闭，可通过配置打开 ——
  封装层要能表达"这条还没被真机验证"；
* 回合数不进桥：``self.turn_seq`` 自己数 ``TurnStarted``。一个「回合」信息其实可以
  零成本拿到，没必要为它花一次往返。
"""

from __future__ import annotations

from typing import Any, Dict, List

import limbuspy as lp
from limbuspy.mod import Mod, config, expose

CHARGE = "CHARGE"


class FieldMedic(Mod):
    name = "field_medic"
    version = "1.0.0"
    description = "回合开始的自动治疗（写操作示例）"

    cfg = config(
        hp_ratio=0.6,       # 低于这个比例就治疗
        base_amount=8,      # 基础治疗量
        per_round=2,        # 每回合额外治疗量
        max_rounds=5,
        give_charge=False,  # experimental：要不要顺手给一层 CHARGE
        dry_run=False,
    )

    def __init__(self) -> None:
        super().__init__()
        self.healed_total = 0
        self.heals = 0
        self.saves = 0
        self.rows: List[Dict[str, Any]] = []
        #: 自己数回合（不花往返）：TurnStarted 每单位一次
        self.turn_seq = 0

    # -- 时机 ---------------------------------------------------------------------------

    @lp.on_turn_start
    def on_turn(self, ev) -> None:
        if ev.unit is None:
            return
        self.turn_seq += 1

        unit = lp.unit(ev.unit)                          # crossing 1（读）
        if unit is None or unit.is_dead or unit.hp >= unit.max_hp:
            return
        if unit.hp_ratio > float(self.cfg.hp_ratio):
            return

        amount = int(self.cfg.base_amount) + min(
            int(self.cfg.max_rounds), self.turn_seq - 1) * int(self.cfg.per_round)
        if self.cfg.dry_run:
            self.rows.append({"unit": unit.instance_id, "would_heal": amount, "hp": unit.hp})
            return

        result = lp.heal(ev.unit, amount)                 # crossing 2（写）
        if not result.ok:
            self.log("治疗没有生效：%s" % (result.reason or "未知",))
            return

        after = result.state
        healed = max(0, int(after.hp) - unit.hp) if after is not None else amount
        self.healed_total += healed
        self.heals += 1
        if after is not None and after.hp_ratio > 0.5:
            self.saves += 1
        self.rows.append({"unit": unit.instance_id, "hp": [unit.hp, after.hp if after else 0],
                          "healed": healed})

        if self.cfg.give_charge:
            # experimental：真机上需要验证 BuffDetail.AddBuff 的时序
            lp.add_buff(ev.unit, CHARGE, stacks=1, turns=2)

        self.log("治疗 #%d：+%d（%d → %s）"
                 % (unit.instance_id, healed, unit.hp, after.hp if after else "?"))

    @lp.on_death
    def on_died(self, ev) -> None:
        self.state["deaths"] = int(self.state.get("deaths") or 0) + 1

    # -- 报告 ---------------------------------------------------------------------------

    @expose("medic.report")
    def report(self, ctx) -> Dict[str, Any]:
        return {
            "mod": self.name,
            "heals": self.heals,
            "healed_total": self.healed_total,
            "saves": self.saves,
            "deaths": int(self.state.get("deaths") or 0),
            "rows": list(self.rows),
            "turn_seq": self.turn_seq,
            "config": self.cfg.as_dict(),
        }


__all__ = ["FieldMedic", "CHARGE"]
