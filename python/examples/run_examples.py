"""跑通 ``python/examples`` 下的全部示例，并把成本模型实测出来。

    python python/examples/run_examples.py
    python python/examples/run_examples.py --quiet
    python python/examples/run_examples.py --only drowning_tide

不需要游戏、不需要 C#：内核角色由 ``MockKernel`` 扮演，op 的语义由 ``MockOps``
（A 层契约的 Python 参考实现）扮演 —— 但走的是**真实 LPCP 通路**，所以 mod 里写的
每一行都跑在真机上会走的那条路径上。

每个场景返回一组 ``(说明, 是否通过)``，最后汇总；``python/tests/test_examples.py``
也复用同一份场景函数，所以示例改坏了 CI 会红。
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from typing import Any, Callable, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from api_harness import ApiHarness          # noqa: E402

Result = Dict[str, Any]


def _harness(name: str, **kwargs: Any) -> ApiHarness:
    return ApiHarness(name, os.path.join(HERE, name), **kwargs).start()


# --------------------------------------------------------------------------------------
# 0. hello_passive —— 十行的 Hello World（模块级注册）
# --------------------------------------------------------------------------------------
def scenario_hello() -> Result:
    """最小示例：模块顶层 ``@lp.on_hit`` 也能被登记（不需要 Mod 子类）。"""
    harness = _harness("hello_passive")
    try:
        before = harness.hp(201)
        harness.attack(101, 201, 50)          # 50 是这一击，外加回调里的 3 点
        after = harness.hp(201)
        return {
            "name": "hello_passive",
            "before": before,
            "after": after,
            "checks": [
                ("模块级 @lp.on_hit 被登记并触发", after == before - 50 - 3),
                ("没有用到任何反射", harness.reflect_calls == 0),
            ],
        }
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 1. burst_on_hit —— 复合动作
# --------------------------------------------------------------------------------------
def scenario_burst() -> Result:
    """复合动作：叠层到阈值 → 一次 ``lp.burst`` 换成伤害。"""
    harness = _harness("burst_on_hit")
    try:
        # 敌人 201 有 8 层 SINKING。先把它打到 hp_ratio <= 0.75（300 → 220），
        # 下一次命中的回调才会「看到」这个血量并触发爆发。
        harness.attack(101, 201, 80)
        harness.attack(101, 201, 10)      # 触发 burst
        harness.attack(101, 202, 5)       # 另一个敌人：层数不够，不触发
        report = harness.call("burst.report")
        return {
            "name": "burst_on_hit",
            "bursts": report["bursts"],
            "damage": report["damage"],
            "enemy_hp": harness.hp(201),
            "checks": [
                ("爆发了一次", report["bursts"] == 1),
                ("爆发造成 8 层 × 3 = 24 伤害", report["damage"] == 24),
                ("血量按 80 + 24 + 10 下降", harness.hp(201) == 300 - 80 - 24 - 10),
                ("层数不够的敌人不触发", harness.hp(202) == 120 - 5),
                ("没有用到任何反射", harness.reflect_calls == 0),
            ],
        }
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 2. damage_ledger —— 纯推送，0 往返
# --------------------------------------------------------------------------------------
def scenario_ledger() -> Result:
    """纯推送：记账不产生任何动作调用。"""
    harness = _harness("damage_ledger")
    try:
        harness.attack(201, 101, 15)      # 敌人打玩家：AttackSucceeded + DamageDealt
        harness.attack(201, 101, 30)      # 打死（40 血）
        harness.emit("UnitBroken", {"unit": harness.ref(202), "attacker": harness.ref(101),
                                    "is_forced": True}, victim=202)
        report = harness.call("ledger.report")
        return {
            "name": "damage_ledger",
            "taken": report["totals"]["taken"],
            "hits": report["totals"]["hits"],
            "deaths": len(report["deaths"]),
            "breaks": len(report["breaks"]),
            "op_calls": harness.op_calls,
            "checks": [
                ("记录到了 40 点总伤害（含被 HP 下限截断的部分）", report["totals"]["taken"] == 40),
                ("两次命中都记到了", report["totals"]["hits"] == 2),
                ("记录了死亡", len(report["deaths"]) == 1),
                ("击杀者被记录", report["deaths"][0]["killer"] is not None),
                ("记录了崩溃", len(report["breaks"]) == 1),
                ("零往返（只读事件里的字段）", harness.op_calls == 0),
            ],
        }
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 3. field_medic —— 读一次 + 写一次
# --------------------------------------------------------------------------------------
def scenario_medic() -> Result:
    """读一次 + 写一次：回合开始时按血量治疗。"""
    harness = _harness("field_medic")
    try:
        harness.start_turn()
        report = harness.call("medic.report")
        # 102：30/70 → +8；202：120/240 → +14（每个单位 turn_seq 递增）
        return {
            "name": "field_medic",
            "heals": report["heals"],
            "healed": report["healed_total"],
            "hp_102": harness.hp(102),
            "hp_202": harness.hp(202),
            "op_calls": harness.op_calls,
            "checks": [
                ("治疗了 2 次（满血的单位被跳过）", report["heals"] == 2),
                ("治疗量随回合递增：10 + 14", report["healed_total"] == 24),
                ("残血单位血量回升", harness.hp(102) == 40),
                ("每个单位 1 次读 + 需要时 1 次写", harness.op_calls == 4 + 2),
            ],
        }
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 4. drowning_tide —— 注册层 + 零往返条件
# --------------------------------------------------------------------------------------
def scenario_drowning() -> Result:
    """注册一个 Buff + 一个被动，条件写成 ``victim.has(...) & once_per_round()``。"""
    harness = _harness("drowning_tide")
    try:
        # 游戏里由别的效果挂上去；这里直接给敌人 201 挂 4 层溺水。
        harness.unit(201).buffs["DROWNING"] = [4, 2, 99, 99]

        before = harness.op_calls
        harness.emit("AttackSucceeded", {
            "unit": harness.ref(101), "target": harness.ref(201),
            "final_damage": 10, "real_damage": 10, "hp_damage": 10,
            "is_critical": False, "timing": "ON_SUCCESS_ATTACK",
        }, victim=201, self_unit=101)
        report = harness.call("drowning.report")
        gated = harness.op_calls - before          # 触发那一次：只有 lp.burst 的 1 次往返

        # 第二个敌人身上没有溺水 → 谓词不放行，函数体根本不跑
        harness.emit("AttackSucceeded", {
            "unit": harness.ref(101), "target": harness.ref(202),
            "final_damage": 10, "real_damage": 10, "hp_damage": 10,
        }, victim=202, self_unit=101)
        # 同回合第二次命中 201：once_per_round 拦住
        harness.emit("AttackSucceeded", {
            "unit": harness.ref(101), "target": harness.ref(201),
            "final_damage": 10, "real_damage": 10, "hp_damage": 10,
        }, victim=201, self_unit=101)
        same_round = harness.call("drowning.report")

        # 新回合：可以再触发
        harness.unit(201).buffs["DROWNING"] = [4, 2, 99, 99]
        harness.start_round()
        harness.emit("AttackSucceeded", {
            "unit": harness.ref(101), "target": harness.ref(201),
            "final_damage": 10, "real_damage": 10, "hp_damage": 10,
        }, victim=201, self_unit=101)
        next_round = harness.call("drowning.report")

        return {
            "name": "drowning_tide",
            "bit": report["drowning"]["bit"],
            "game_id": report["drowning"]["game_id"],
            "row_fields": report["drowning"]["row_fields"],
            "crushes": [report["tide_crush"]["crushes"],
                        same_round["tide_crush"]["crushes"],
                        next_round["tide_crush"]["crushes"]],
            "gated_op_calls": gated,
            "checks": [
                ("自定义 Buff 拿到了位序号 0", report["drowning"]["bit"] == 0),
                ("内核给它分配了真枚举值（基址 24576）",
                 report["drowning"]["game_id"] == 24576),
                ("生成了完整的 BuffStaticData 行（20+ 字段）",
                 report["drowning"]["row_fields"] >= 20),
                ("溺水在身 → 触发一次", report["tide_crush"]["crushes"] == 1),
                ("没有该 Buff 的敌人不触发", harness.hp(202) == 120),
                ("同回合不重复触发", same_round["tide_crush"]["crushes"] == 1),
                ("新回合可以再触发", next_round["tide_crush"]["crushes"] == 2),
                ("条件判断本身 0 往返（只花 lp.burst 的那 1 次）", gated == 1),
            ],
        }
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 5. shield_warden —— 条件过滤 + 写操作
# --------------------------------------------------------------------------------------
def scenario_warden() -> Result:
    """重击后补盾、抢速、反打；条件写在装饰器里。"""
    harness = _harness("shield_warden")
    try:
        harness.emit("HpDamageTaken", {
            "unit": harness.ref(101), "attacker": harness.ref(201),
            "final_damage": 30, "hp_damage": 30, "source": "SKILL",
            "keyword": "", "attribute": "WRATH", "atk_type": "SLASH",
            "timing": "ON_SUCCESS_ATTACK",
        }, victim=101, self_unit=201)
        heavy_shield = harness.unit(101).shield
        attacker_hp = harness.hp(201)

        # 轻击：谓词不满足，函数体不跑
        harness.emit("HpDamageTaken", {
            "unit": harness.ref(101), "attacker": harness.ref(201),
            "final_damage": 5, "hp_damage": 5, "source": "SKILL",
            "keyword": "", "attribute": "WRATH", "atk_type": "SLASH",
        }, victim=101, self_unit=201)
        light_shield = harness.unit(101).shield

        # 治疗方向（old_hp < new_hp）：被字段比字段认出来
        harness.emit("DamageDealt", {
            "unit": harness.ref(101), "attacker": None,
            "old_hp": 20, "new_hp": 35, "source": "BUFF", "keyword": "",
        }, victim=101)
        harness.emit("UnitBroken", {"unit": harness.ref(202), "attacker": harness.ref(101),
                                    "is_forced": True}, victim=202)
        report = harness.call("warden.report")
        return {
            "name": "shield_warden",
            "braces": report["braces"],
            "heals_seen": report["heals_seen"],
            "breaks": report["breaks"],
            "checks": [
                ("重击触发一次", report["braces"] == 1),
                ("补上了 8 点护盾", heavy_shield == 8),
                ("反打了攻击者 5 点", attacker_hp == 295),
                ("轻击不触发（谓词在函数体之外）", light_shield == 8),
                ("治疗方向被 old_hp < new_hp 认出来", report["heals_seen"] == 1),
                ("崩溃被记录", report["breaks"] == 1),
            ],
        }
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 6. battle_scribe —— 快照经济学
# --------------------------------------------------------------------------------------
def scenario_scribe() -> Result:
    """每回合最多 1 次往返；事件全部免费；只有"有人问"时才读。"""
    harness = _harness("battle_scribe")
    try:
        harness.start_round()
        before = harness.op_calls
        for _ in range(10):
            harness.ops.dispatch("unit.damage", {
                "unit": harness.ref(202), "amount": 5, "source": "PASSIVE",
                "timing": "ON_SUCCESS_ATTACK",
            })
            harness.flush()
        event_cost = harness.op_calls - before         # 必须 == 0

        first = harness.call("scribe.report")
        first_cost = harness.op_calls - before
        second = harness.call("scribe.report")         # 缓存还有效 → 再问一次不花钱
        second_cost = harness.op_calls - before

        return {
            "name": "battle_scribe",
            "event_cost": event_cost,
            "first_report_cost": first_cost,
            "second_report_cost": second_cost,
            "snapshots": first["snapshots_taken"],
            "checks": [
                ("10 个伤害事件 = 0 次往返", event_cost == 0),
                ("第一次报告才读战场（1～2 次往返）", 1 <= first_cost <= 2),
                ("缓存有效时再问一次不花钱", second_cost == first_cost),
                ("只读了一次战场", first["snapshots_taken"] == 1),
                ("事件计数正确", first["damage_events"] == 10),
                ("最惨的敌人是 202（被打了 50）",
                 first["weakest_enemy"]["unit"] == 202),
                ("敌人总血量正确", first["enemy_hp"] == 300 + (120 - 50)),
            ],
        }
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 7. A 层 vs B 层 —— 成本对照
# --------------------------------------------------------------------------------------
def scenario_contrast() -> Result:
    """同一件事的两种写法：门面 1 次往返拿快照 vs 反射逐字段读。"""
    from limbuspy.api import bindings
    from limbuspy.game.session import Session

    harness = _harness("burst_on_hit")
    try:
        unit = harness.unit(201)
        ref = harness.battle.ref(unit)

        import limbuspy as lp
        before = harness.bridge.counters.frames_sent
        snapshot = lp.unit(201)                       # 1 次
        values = (snapshot.hp, snapshot.max_hp, snapshot.mp, snapshot.instance_id,
                  snapshot.hp_ratio, snapshot.is_break)
        a_crossings = harness.bridge.counters.frames_sent - before

        fresh = Session(harness.bridge)               # 新 session：避免 B 层缓存干扰计数
        before = harness.bridge.counters.frames_sent
        bound = bindings.BattleUnitModel(fresh, ref)
        b_values = (bound.hp, bound.max_hp, bound.mp, bound.instance_id)
        b_crossings = harness.bridge.counters.frames_sent - before

        return {
            "name": "A vs B",
            "a_crossings": a_crossings,
            "b_crossings": b_crossings,
            "checks": [
                ("门面：1 次往返拿 6 个字段", a_crossings == 1),
                ("门面：值是对的", values[:4] == b_values
                 and values[4] == unit.hp / unit.max_hp),
                ("反射：4 个字段 = 4 次往返", b_crossings == 4),
            ],
        }
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 8. 全动作可达性
# --------------------------------------------------------------------------------------
def scenario_actions() -> Result:
    """每个门面动作都能真的调下去（不是"为了好看而存在"的别名）。"""
    import limbuspy as lp

    harness = _harness("hello_passive")
    try:
        results: List[tuple] = []

        def check(label: str, action: Any) -> None:
            ok = bool(getattr(action, "ok", action))
            results.append((label, ok))

        check("lp.damage", lp.damage(201, 10))
        check("lp.heal", lp.heal(201, 5))
        check("lp.set_hp", lp.set_hp(201, 250))
        check("lp.set_sp", lp.set_sp(201, -3))
        check("lp.add_shield", lp.add_shield(201, 7))
        check("lp.set_speed", lp.set_speed(201, 9))
        check("lp.refresh_speed", lp.refresh_speed(201))
        check("lp.add_buff", lp.add_buff(201, "CHARGE", stacks=1, turns=2))
        check("lp.add_passive", lp.add_passive(201, 9103201))
        burst = lp.burst(201, "SINKING", per_stack=3, max_stacks=6)
        check("lp.burst", burst)
        results.append(("lp.burst 带回结算明细", burst.stacks == 6 and burst.damage == 18))
        check("lp.break_unit", lp.break_unit(201))

        # 读入口
        results.append(("lp.status", lp.status().connected))
        book = lp.battle()
        results.append(("lp.battle", len(book) == 4 and book.round == 1))
        results.append(("lp.units", len(lp.units()) == 4))
        target = lp.unit(201)
        results.append(("lp.unit", target is not None and target.instance_id == 201))
        results.append(("lp.unit(ev.target) 形态", lp.unit(harness.ref(201)) is not None))
        results.append(("lp.player", lp.player() is not None))
        results.append(("lp.round", lp.round() == 1))
        results.append(("lp.buff_stacks",
                        lp.buff_stacks("SINKING") == {201: 2, 202: 2}))
        results.append(("u.buffs / u.buff_stack / u.has_buff",
                        bool(target.buffs) and target.buff_stack("SINKING") == 2
                        and target.has_buff("SINKING")))

        return {
            "name": "actions",
            "checked": len(results),
            "checks": results,
        }
    finally:
        harness.close()


SCENARIOS: Dict[str, Callable[[], Result]] = {
    "hello_passive": scenario_hello,
    "burst_on_hit": scenario_burst,
    "damage_ledger": scenario_ledger,
    "field_medic": scenario_medic,
    "drowning_tide": scenario_drowning,
    "shield_warden": scenario_warden,
    "battle_scribe": scenario_scribe,
    "contrast": scenario_contrast,
    "actions": scenario_actions,
}


# --------------------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------------------
def _title(text: str) -> None:
    print("\n== %s %s" % (text, "=" * max(4, 74 - len(text))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="跑通 python/examples 下的全部示例")
    parser.add_argument("--quiet", action="store_true", help="只打印失败项与汇总")
    parser.add_argument("--only", action="append", default=[], choices=sorted(SCENARIOS),
                        help="只跑指定场景（可重复）")
    args = parser.parse_args(argv)

    names = args.only or list(SCENARIOS)
    results: List[Result] = []
    for name in names:
        try:
            results.append(SCENARIOS[name]())
        except Exception as exc:  # noqa: BLE001 - 一个示例炸了不影响其它示例的结果
            traceback.print_exc()
            results.append({"name": name, "checks": [("异常: %s" % exc, False)]})

    failed = 0
    for result in results:
        if not args.quiet or any(not ok for _label, ok in result["checks"]):
            _title(result["name"])
        for label, ok in result["checks"]:
            if not ok:
                failed += 1
                print("  FAIL  %s" % label)
            elif not args.quiet:
                print("  ok    %s" % label)
        extras = {k: v for k, v in result.items() if k not in ("name", "checks")}
        if extras and not args.quiet:
            print("  -> %s" % ", ".join("%s=%r" % item for item in sorted(extras.items())))

    total = sum(len(result["checks"]) for result in results)
    print("\n%d 个场景 / %d 项断言 / %d 项失败" % (len(results), total, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
