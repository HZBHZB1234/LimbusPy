"""示例 5：``shield_warden`` —— 响应式防御：条件过滤 + 写操作。

做的事：有人吃了一次重击（单次 HP 伤害 >= 21）时，立刻给他补一层护盾、重算行动顺序，
并顺手反打攻击者一下；平时只记账。

展示的点：

* **条件写在装饰器里**，不是写在函数体里 —— ``when=lp.ev.hp_damage > 20``。
  谓词在注册期编译成闭包，运行期只是一次比较；不满足时**函数体根本不会跑**。
* ``lp.ev.old_hp < lp.ev.new_hp``（字段比字段）可以用来识别"这是治疗不是伤害"。
* 三种写操作的成本各自是 1 次往返：``lp.add_shield`` / ``lp.refresh_speed`` / ``lp.damage``。
  写操作必然要过桥（游戏状态只能由游戏线程改），这一点没法也不该隐藏。
* 单位引用可以直接从 payload 拿来用（``ev.unit`` / ``ev.attacker``），
  不用自己拼 instance_id —— 时点 payload 里只有句柄。

一个诚实的限制：**谓词在类体创建时就固定了**，所以阈值写成模块常量而不是 ``cfg``。
要让它跟着配置走，就在 ``on_load`` 里用读到的配置重建处理器 —— 那是另一套写法，
这个示例不糊在一起。
"""

from __future__ import annotations

from typing import Any, Dict, List

import limbuspy as lp
from limbuspy.mod import Mod, config, expose

#: 算作「重击」的单次 HP 伤害阈值（谓词在类体里用它，见模块文档）
HEAVY_HIT = 20


class ShieldWarden(Mod):
    name = "shield_warden"
    version = "1.0.0"
    description = "重击之后补护盾、抢先手、反打一下"

    cfg = config(
        shield=8,           # 重击后给的护盾值
        counter_damage=5,   # 反打攻击者多少
        dry_run=False,      # 只记账不动手
    )

    def __init__(self) -> None:
        super().__init__()
        self.braces = 0
        self.heals_seen = 0
        self.breaks = 0
        self.rows: List[Dict[str, Any]] = []

    # -- 条件：重击 ----------------------------------------------------------------

    @lp.on_damage_taken(when=lp.ev.hp_damage > HEAVY_HIT)
    def brace(self, ev) -> None:
        """一次重击 → 护盾 + 抢速 + 反打。

        ``ev`` 是 ``HpDamageTakenPayload``：``unit`` 是受击者、``attacker`` 是攻击者、
        ``hp_damage`` 是这次的 HP 伤害、``attribute`` / ``source`` 说明伤害性质。
        """
        if ev.unit is None:
            return

        row: Dict[str, Any] = {
            "unit": ev.unit.key,
            "hp_damage": int(ev.hp_damage),
            "attribute": ev.attribute,
            "applied": {},
        }
        if not self.cfg.dry_run:
            # 1) 护盾（累加，不会超上限 —— 语义在 C# 侧）
            row["applied"]["shield"] = lp.add_shield(ev.unit, int(self.cfg.shield)).ok
            # 2) 速度没变，但行动顺序要重算（只改值不刷新，要等下一回合才生效）
            row["applied"]["refresh_speed"] = lp.refresh_speed(ev.unit).ok
            # 3) 反打攻击者
            if ev.attacker is not None:
                counter = lp.damage(ev.attacker, int(self.cfg.counter_damage), source="BUFF")
                row["applied"]["counter"] = counter.ok
                if counter.state is not None:
                    row["attacker_hp"] = counter.state.hp

        self.braces += 1
        self.rows.append(row)
        self.log("重击 %d 点 → 补盾 %d / 抢速 / 反打 %d"
                 % (row["hp_damage"], self.cfg.shield, self.cfg.counter_damage))

    # -- 条件：这是治疗，不是伤害 ----------------------------------------------------
    #
    # ``DamageDealt`` 是「任何方向的 HP 变化」，所以"治疗"也走它。
    # 用字段比字段（old_hp < new_hp）把它认出来 —— 同样 0 往返。

    @lp.on_damage(when=lp.ev.old_hp < lp.ev.new_hp)
    def on_healed(self, ev) -> None:
        self.heals_seen += 1

    # -- 崩溃 ---------------------------------------------------------------------------

    @lp.on_break
    def on_broken(self, ev) -> None:
        """崩溃（break）。``is_forced`` 区分「拼点打赢造成的」和「被强制打破的」。"""
        self.breaks += 1
        self.rows.append({
            "unit": ev.unit.key if ev.unit else "?",
            "attacker": ev.attacker.key if ev.attacker else None,
            "forced": bool(ev.is_forced),
        })

    # -- 报告 ---------------------------------------------------------------------------

    @expose("warden.report")
    def report(self, ctx) -> Dict[str, Any]:
        """纯内存拼装，0 次往返。"""
        return {
            "mod": self.name,
            "braces": self.braces,
            "heals_seen": self.heals_seen,
            "breaks": self.breaks,
            "shield_granted": self.braces * int(self.cfg.shield),
            "rows": list(self.rows),
            "config": self.cfg.as_dict(),
        }


__all__ = ["ShieldWarden", "HEAVY_HIT"]
