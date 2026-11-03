"""``python/examples/sinking_tide`` 的回归测试：示例也是代码，必须一直被跑。

复用 ``python/examples/run_sinking_tide.py`` 里的 ``Harness``（mock 战斗 + mock 内核 + 真实
LPCP 通路），所以这里验证的是**同一份**示例代码与同一份驱动器。

这个示例是唯一同时用上「门面」和「门面覆盖不到的两层」的示例，所以它守的断言也最多：
爆发数值、阈值与轮次门控、``dry_run``、Harmony 补丁观测、同步泳道倍率、
报告能过桥、热重载不丢账本且补丁重装。
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXAMPLES = os.path.abspath(os.path.join(_HERE, "..", "examples"))
if _EXAMPLES not in sys.path:
    sys.path.insert(0, _EXAMPLES)

import run_sinking_tide as demo  # noqa: E402

from limbuspy.runtime import codec  # noqa: E402


@pytest.fixture()
def harness():
    h = demo.Harness().start()
    try:
        yield h
    finally:
        h.close()


def test_example_mod_is_discovered_and_wired(harness):
    assert list(harness.runtime.mods) == [demo.MOD_NAME], harness.runtime.load_errors
    mod = harness.mod
    assert mod.name == "sinking_tide"
    assert mod.version == "1.0.0"
    # 3 个 hook + 1 个补丁（补丁从 on_load 里装上，id 被基类记住以便重载时摘掉）
    stats = harness.runtime.registry.stats()
    assert stats["hooks"] == 3, stats
    assert stats["exposed"] == 4, stats
    assert len(harness.patches) == 1
    assert list(harness.patches)[0].startswith("BattleUnitModel.OnChangeHp")


def test_curated_hooks_use_the_new_timings(harness):
    """时点名必须是 A 层的（``AttackSucceeded``），不是高级层的 ``OnSucceedAttack``。

    这条曾经是活的 bug：示例写 ``@hook("OnSucceedAttack")``，而内核发的是
    ``AttackSucceeded``，于是回调从来没被调用过。
    """
    timings = {entry.timing for entry in harness.runtime.registry.all_hooks()}
    assert "AttackSucceeded" in timings
    assert "DamageDealt" in timings
    assert "OnSucceedAttack" not in timings


def test_snapshot_driven_burst_deals_real_damage(harness):
    assert harness.hp(demo.MARKED) == 120
    harness.attack(demo.HERO, demo.MARKED, 20)

    # 沉溺 8 层 × 每层 3 = 24，走游戏自己的 HP 管线，再加上这一击本身的 20
    assert harness.hp(demo.MARKED) == 120 - 24 - 20
    totals = harness.mod.ledger.totals()
    assert totals["bursts"] == 1
    assert totals["damage"] == 24
    assert totals["hits"] == 1


def test_burst_respects_threshold_and_round_gate(harness):
    harness.set_buff_stack(demo.MARKED, "SINKING", 5)          # 低于阈值 6
    harness.attack(demo.HERO, demo.MARKED, 10)
    assert harness.hp(demo.MARKED) == 110

    harness.set_buff_stack(demo.MARKED, "SINKING", 6)          # 刚好到阈值
    harness.attack(demo.HERO, demo.MARKED, 10)
    assert harness.hp(demo.MARKED) == 110 - 18 - 10

    # 同一轮不再爆发（once_per_round）
    harness.set_buff_stack(demo.MARKED, "SINKING", 9)
    harness.attack(demo.HERO, demo.MARKED, 10)
    assert harness.hp(demo.MARKED) == 110 - 18 - 10 - 10

    # 下一轮可以
    harness.start_round()
    harness.attack(demo.HERO, demo.MARKED, 10)
    assert harness.hp(demo.MARKED) == 110 - 18 - 10 - 10 - 27 - 10


def test_burst_skips_healthy_targets(harness):
    harness.set_buff_stack(demo.MARKED, "SINKING", 12)
    harness.unit(demo.MARKED).hp = 240                         # 满血 → 超过 hp 上限 0.5

    harness.attack(demo.HERO, demo.MARKED, 10)
    assert harness.hp(demo.MARKED) == 230
    assert harness.mod.ledger.bursts == 0


def test_dry_run_never_touches_the_game(harness):
    harness.mod.cfg.dry_run = True
    harness.attack(demo.HERO, demo.MARKED, 20)

    # 只有那一击本身生效，爆发只记账
    assert harness.hp(demo.MARKED) == 100
    assert harness.mod.ledger.bursts == 1
    assert harness.mod.ledger.damage == 24


def test_predicate_hook_counts_only_sinking_damage(harness):
    """``@lp.on_damage(when=lp.ev.keyword == SINKING)`` 只收沉溺造成的那一类。"""
    harness.emit("DamageDealt", {"unit": harness.ref(demo.MARKED), "attacker": None,
                                 "old_hp": 120, "new_hp": 117, "source": "BUFF",
                                 "keyword": "SINKING"}, victim=demo.MARKED)
    harness.emit("DamageDealt", {"unit": harness.ref(demo.BRUTE), "attacker": None,
                                 "old_hp": 300, "new_hp": 297, "source": "PASSIVE",
                                 "keyword": ""}, victim=demo.BRUTE)
    assert harness.mod.ledger.ticks == 1


def test_patch_lane_observes_hp_changes(harness):
    harness.call("sinking_tide.on_hp_changed", {
        "patch": "BattleUnitModel.OnChangeHp#1", "kind": "prefix",
        "args": [120, 96, "PASSIVE", "ON_SUCCESS_ATTACK", None, None, 2123],
    })
    assert harness.mod.ledger.hp_events == 1
    assert list(harness.mod.hp_events)[-1] == {
        "old": 120, "new": 96, "src": "PASSIVE", "timing": "ON_SUCCESS_ATTACK",
    }


def test_sync_lane_multiplier(harness):
    assert harness.call("sinking_tide.take_damage_multiplier", {}) == 100   # 未就绪 → 不变
    harness.mod.tide_armed = True
    assert harness.call("sinking_tide.take_damage_multiplier", {}) == 100   # 配置关闭
    harness.mod.cfg.amplify_take_damage = True
    assert harness.call("sinking_tide.take_damage_multiplier", {}) == 115


def test_report_survives_the_wire(harness):
    harness.attack(demo.HERO, demo.MARKED, 20)

    report = harness.report()
    assert report["totals"]["bursts"] == 1
    assert report["config"]["threshold"] == 6
    # 必须是 msgpack 可编码的纯数据（它会作为 SYNCALL 的回包过桥）
    assert codec.unpack(codec.pack(report))["totals"]["bursts"] == 1


def test_statics_calibrate_the_stack_ceiling(harness):
    """静态表（BuffData）里的 maxStack 会被读进来当上限用。"""
    assert harness.mod._sinking_max_stack == 99      # data/BuffData.json 里 Sinking 是 99
    harness.set_buff_stack(demo.MARKED, "SINKING", 200)
    harness.mod._sinking_max_stack = 4               # 手动压低，验证它真的被用上
    harness.attack(demo.HERO, demo.MARKED, 10)
    assert harness.mod.ledger.damage == 4 * 3


def test_force_burst_uses_threshold_when_no_stacks(harness):
    result = harness.call("sinking_tide.force_burst", {"args": [demo.BRUTE]})
    assert result["stacks"] == 6 and result["damage"] == 18
    assert harness.hp(demo.BRUTE) == 300 - 18

    with pytest.raises(Exception):
        harness.call("sinking_tide.force_burst", {"args": []})


def test_hot_reload_keeps_ledger_and_reinstalls_patch(harness):
    harness.attack(demo.HERO, demo.MARKED, 20)
    before = harness.report()["totals"]

    result = harness.runtime.reload([demo.MOD_NAME], reason="test")
    assert result.ok, result.failed
    assert result.generation[demo.MOD_NAME] > 2

    after = harness.report()["totals"]
    assert before == after, "热重载丢了账本"
    assert len(harness.patches) == 1, "重载后补丁应当被重新装上"

    # 重载后的新代码继续工作
    harness.start_round()
    harness.attack(demo.HERO, demo.MARKED, 20)
    assert harness.report()["totals"]["bursts"] == before["bursts"] + 1


def test_demo_script_runs_green_end_to_end():
    assert demo.run(quiet=True) == 0
