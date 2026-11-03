"""示例 1：``burst_on_hit`` —— 复合动作 + 时机 payload（A 层的主要卖点）。

做的事情：敌人身上的 SINKING 叠层到阈值、且血够少时，把叠层换成一次真实伤害，
并把每次爆发的账记下来，通过 ``@expose`` 暴露成可查询的报告。

用到的门面能力：

* ``@lp.on_hit`` —— 类型化时点（``ev.target`` / ``ev.hp_damage`` 有补全，不用背参数顺序）
* ``lp.unit(ev.target)`` —— 一次往返拿快照，之后 ``u.hp_ratio`` / ``u.is_dead`` 全部免费
* ``lp.burst(...)`` —— **复合动作**：读层数 → 夹上限 → 算目标 HP → 改血，
  全在同一个主线程时间片里（拆成 Python 多步会被帧边界插进来，读到旧值）
* ``@expose`` —— 报告（0 次往返，纯内存）

成本对照：同样的事用反射（B 层）要 3 次以上往返，并且要自己处理 ``ChangeHp``
「参数是目标 HP 值而不是增量」这个坑。
"""

from __future__ import annotations

from typing import Any, Dict, List

import limbuspy as lp
from limbuspy.mod import Mod, config, expose

#: 沉溺的 BuffModel 主关键字
SINKING = "SINKING"


class BurstOnHit(Mod):
    name = "burst_on_hit"
    version = "1.0.0"
    description = "把 SINKING 叠层换成伤害（复合动作示例）"

    cfg = config(
        threshold=6,            # 触发所需层数
        per_stack=3,            # 每层换多少伤害
        max_stacks=8,           # 参与结算的最大层数
        hp_ratio_ceiling=0.75,  # 只打血少的
        once_per_round=True,
        dry_run=False,
    )

    def __init__(self) -> None:
        super().__init__()
        self.rows: List[Dict[str, Any]] = []
        self.last_round = 0

    # -- 时机 ---------------------------------------------------------------------------

    @lp.on_hit
    def on_attack(self, ev) -> None:
        """一次命中之后判断要不要爆发。

        这里只用**一次**快照 + **一次**复合动作，所以整个回调最多 2 次往返：

        * ``lp.unit(...)`` —— 拿 ``hp_ratio`` / ``is_dead`` / ``buff_count``；
        * ``lp.burst(...)`` —— 结算。
        """
        if ev.target is None:
            return

        enemy = lp.unit(ev.target)                    # crossing 1
        if enemy is None or enemy.is_dead:
            return
        if enemy.hp_ratio > float(self.cfg.hp_ratio_ceiling):
            return

        stacks = enemy.buff_stack(SINKING)            # crossing 2（只读这一个关键字，不拉整张表）
        if stacks < int(self.cfg.threshold):
            return

        current_round = lp.round() or 0               # crossing 3
        if self.cfg.once_per_round and self.last_round == current_round:
            return

        if self.cfg.dry_run:
            self.rows.append({"unit": enemy.instance_id, "stacks": stacks, "dry_run": True})
            return

        result = lp.burst(                            # crossing 4：一次调用做完多步
            ev.target, SINKING,
            per_stack=int(self.cfg.per_stack),
            max_stacks=int(self.cfg.max_stacks),
        )
        if not result.ok:
            self.log("爆发没有生效：%s" % (result.reason or "未知",))
            return

        self.last_round = current_round
        self.rows.append({
            "unit": enemy.instance_id,
            "stacks": result.stacks,
            "damage": result.damage,
            "hp": [result.hp_before, result.hp_after],
        })
        self.log("潮汐爆发 #%d：%d 层 → %d 伤害（HP %d → %d）"
                 % (enemy.instance_id, result.stacks, result.damage,
                    result.hp_before, result.hp_after))

    # -- 报告 ---------------------------------------------------------------------------

    @expose("burst.report")
    def report(self, ctx) -> Dict[str, Any]:
        """爆发账本（纯内存，0 次往返）。"""
        return {
            "mod": self.name,
            "bursts": len(self.rows),
            "damage": sum(int(row.get("damage") or 0) for row in self.rows),
            "rows": list(self.rows),
            "config": self.cfg.as_dict(),
        }


__all__ = ["BurstOnHit", "SINKING"]
