"""示例 6：``battle_scribe`` —— 快照经济学：「获取」花钱，「读属性」免费。

这是整个门面最核心的一条公理的样板：

> **往返发生在「获取」的时候，不发生在「读属性」的时候。**

做法是把它变成可测量的东西：

* 每回合开始只做一件事 —— 把缓存标记为**过期**（0 次往返）；
* 所有伤害/死亡事件只记账，不读任何状态（0 次往返）；
* 只有真的有人问"现在场上谁最惨"时（``scribereport``），才花那 1 次往返把全场读回来，
  之后过滤、排序、求和、切片全部免费。

于是「一场 30 个事件的乱斗 = 0 次往返，一次报告 = 1 次往返」。
``run_examples.py`` 里的 ``scenario_scribe`` 会把这两个数字打出来，用 ``op_calls`` 实测。

反面样板（很容易写出来、但每回合要 N+1 次）：在每次伤害事件里
``lp.unit(ev.unit)`` 读一次血。这个示例把它挪到"有人问的时候"，就是全部的区别。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import limbuspy as lp
from limbuspy.mod import Mod, config, expose


class BattleScribe(Mod):
    name = "battle_scribe"
    version = "1.0.0"
    description = "每回合最多读一次全场，其余全部靠事件推送（快照经济学示例）"

    cfg = config(
        keep_rows=6,       # 报告里保留多少个单位
        track_deaths=True,
    )

    def __init__(self) -> None:
        super().__init__()
        #: 缓存是否过期（回合开始 / 战斗开始都会置位）
        self.dirty = True
        #: 上一次真正读回来的战场快照（纯内存结构）
        self.frozen: Optional[Dict[str, Any]] = None
        #: 事件侧的记账（全部 0 往返）
        self.damage_events = 0
        self.deaths = 0
        self.taken: Dict[str, int] = {}
        #: 本 mod 到目前为止真的花过几次往返（自测用）
        self.snapshots = 0

    # -- 生命周期 -----------------------------------------------------------------------

    def on_load(self) -> None:
        self.log("战地书记就位（每回合最多 1 次往返）")

    # -- 标记过期：0 往返 ---------------------------------------------------------------

    @lp.on_round_start
    def mark_stale(self, ev) -> None:
        """回合开始。这里**故意不读战场** —— 现在读，也不知道这一回合会打成什么样。

        真正需要数据的时刻是"有人问的时候"（报告 / 决策），那就到那时候再读一次。
        """
        self.dirty = True

    # -- 记账：0 往返 ---------------------------------------------------------------

    @lp.on_damage
    def on_damage(self, ev) -> None:
        """一次 HP 变化。payload 自带 ``old_hp`` / ``new_hp``，不需要回查。"""
        self.damage_events += 1
        if ev.unit is None:
            return
        lost = int(ev.old_hp) - int(ev.new_hp)
        if lost > 0:
            self.taken[ev.unit.key] = self.taken.get(ev.unit.key, 0) + lost

    @lp.on_death
    def on_death(self, ev) -> None:
        if self.cfg.track_deaths:
            self.deaths += 1

    # -- 真正的读取：只在有人问的时候发生 ------------------------------------------------

    @expose("scribe.report")
    def report(self, ctx) -> Dict[str, Any]:
        """战场报告。缓存过期时读一次全场（1 次往返），否则 0 次。

        注意这里用 ``lp.battle()`` 而不是 `for id in ids: lp.unit(id)` ——
        前者是 1 次批量读，后者是 N 次。
        """
        if self.dirty or self.frozen is None:
            self.frozen = self._freeze()
            self.dirty = False
            self.snapshots += 1

        frozen = dict(self.frozen or {})
        frozen["mod"] = self.name
        frozen["damage_events"] = self.damage_events
        frozen["deaths"] = self.deaths
        frozen["snapshots_taken"] = self.snapshots
        return frozen

    def _freeze(self) -> Dict[str, Any]:
        """一次往返读回全场，之后所有查询都是内存操作。"""
        book = lp.battle()                                    # ← 这一次（最多 2 次）往返
        enemies = [unit for unit in book.enemies if not unit.is_dead]
        players = [unit for unit in book.players if not unit.is_dead]

        rows: List[Dict[str, Any]] = [
            {
                "unit": unit.instance_id,
                "hp": unit.hp,
                "max_hp": unit.max_hp,
                "hp_ratio": round(unit.hp_ratio, 3),
                "buffs": unit.buff_count,
                "level": unit.level,
                "is_break": unit.is_break,
            }
            for unit in (enemies + players)
        ]
        rows.sort(key=lambda row: row["hp_ratio"])

        weakest = min(enemies, key=lambda unit: unit.hp_ratio) if enemies else None
        return {
            "round": book.round,
            "units": len(book),
            "alive": len(book.alive),
            "enemy_hp": sum(unit.hp for unit in enemies),
            "enemy_max_hp": sum(unit.max_hp for unit in enemies),
            "weakest_enemy": None if weakest is None else {
                "unit": weakest.instance_id,
                "hp": weakest.hp,
                "hp_ratio": round(weakest.hp_ratio, 3),
            },
            "rows": rows[: int(self.cfg.keep_rows)],
        }

    def invalidate(self) -> None:
        """手动把缓存作废（示例/排障用）。"""
        self.dirty = True


__all__ = ["BattleScribe"]
