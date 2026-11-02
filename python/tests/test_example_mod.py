"""``examples/sinking_tide`` 的回归测试：示例也是代码，必须一直被跑。

复用 ``examples/run_sinking_tide.py`` 里的"游戏形状替身"，所以这里验证的是**同一份**替身与
同一份示例代码；替身成员名与真实 dump 一致，写错会像真机一样 notfound。

全部无需游戏、无需 C#：走 mock kernel 的真实 LPCP 通路。
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXAMPLES = os.path.abspath(os.path.join(_HERE, "..", "..", "examples"))
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


def _hp_of(harness, instance_id):
    for entry in harness.game.battle["units"]:
        if entry["instance_id"] == instance_id:
            return entry["hp"]
    raise AssertionError("no unit %r" % (instance_id,))


def test_example_mod_is_discovered_and_wired(harness):
    assert list(harness.runtime.mods) == [demo.MOD_NAME], harness.runtime.load_errors
    mod = harness.mod
    assert mod.name == "sinking_tide"
    assert mod.version == "1.0.0"
    # 3 个 hook + 1 个补丁（补丁从 on_load 里装上，id 被基类记住以便重载时摘掉）
    assert harness.runtime.registry.stats()["hooks"] == 3
    assert harness.runtime.registry.stats()["exposed"] == 4
    assert len(harness.kernel.patches) == 1
    assert list(harness.kernel.patches)[0].startswith("BattleUnitModel.OnChangeHp")


def test_snapshot_driven_burst_deals_real_damage(harness):
    refs = demo.build_scenario(harness.game)
    assert _hp_of(harness, 202) == 120

    harness.emit([
        {"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True, "crit": False},
        {"timing": "OnGainBuff", "ctx": refs["marked"], "keyword": "SINKING"},
    ])

    # 沉溺 8 层 × 每层 3 = 24，走游戏自己的 ChangeHp 管线
    assert _hp_of(harness, 202) == 96
    assert harness.game.hp_calls == [(202, 120, 96, "PASSIVE", "ON_SUCCESS_ATTACK")]
    totals = harness.mod.ledger.totals()
    assert totals["bursts"] == 1
    assert totals["damage"] == 24
    assert totals["sinking_gained"] == 1


def test_burst_respects_threshold_and_round_gate(harness):
    refs = demo.build_scenario(harness.game)
    harness.game.set_buff_stack(refs["marked"], "Sinking", 5)          # 低于阈值 6

    harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])
    assert _hp_of(harness, 202) == 120

    harness.game.set_buff_stack(refs["marked"], "Sinking", 6)          # 刚好到阈值
    harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])
    assert _hp_of(harness, 202) == 120 - 18

    # 同一轮不再爆发（once_per_round）
    harness.game.set_buff_stack(refs["marked"], "Sinking", 9)
    harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])
    assert _hp_of(harness, 202) == 120 - 18

    # 下一轮可以
    harness.game.battle["round"] = 9
    harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])
    assert _hp_of(harness, 202) == 120 - 18 - 27


def test_burst_skips_healthy_targets(harness):
    refs = demo.build_scenario(harness.game)
    harness.game.set_buff_stack(refs["marked"], "Sinking", 12)
    for entry in harness.game.battle["units"]:                          # 满血 → 超过 hp 上限
        if entry["instance_id"] == 202:
            entry["hp"] = 240
            harness.game.handles[refs["marked"]["h"]]["members"]["Hp"] = 240

    harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])
    assert _hp_of(harness, 202) == 240
    assert harness.mod.ledger.bursts == 0


def test_dry_run_never_touches_the_game(harness):
    refs = demo.build_scenario(harness.game)
    harness.mod.cfg.dry_run = True

    harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])

    assert _hp_of(harness, 202) == 120
    assert harness.game.hp_calls == []
    assert harness.mod.ledger.bursts == 1        # 只记账
    assert harness.mod.ledger.damage == 24


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
    refs = demo.build_scenario(harness.game)
    harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])

    report = harness.report()
    assert report["totals"]["bursts"] == 1
    assert report["config"]["threshold"] == 6
    # 必须是 msgpack 可编码的纯数据（它会作为 SYNCALL 的回包过桥）
    assert codec.unpack(codec.pack(report))["totals"]["bursts"] == 1


def test_force_burst_uses_threshold_when_no_stacks(harness):
    demo.build_scenario(harness.game)
    result = harness.call("sinking_tide.force_burst", {"args": [201]})
    assert result["stacks"] == 6 and result["damage"] == 18
    assert result["hp"] == [300, 282]
    assert _hp_of(harness, 201) == 282

    with pytest.raises(Exception):
        harness.call("sinking_tide.force_burst", {"args": []})


def test_hot_reload_keeps_ledger_and_reinstalls_patch(harness):
    refs = demo.build_scenario(harness.game)
    harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])
    before = harness.report()["totals"]

    result = harness.runtime.reload([demo.MOD_NAME], reason="test")
    assert result.ok, result.failed
    assert result.generation[demo.MOD_NAME] > 2

    after = harness.report()["totals"]
    assert before == after, "热重载丢了账本"
    assert len(harness.kernel.patches) == 1, "重载后补丁应当被重新装上"

    # 重载后的新代码继续工作
    harness.game.battle["round"] = 7
    harness.emit([{"timing": "OnSucceedAttack", "ctx": refs["marked"], "head": True}])
    assert harness.report()["totals"]["bursts"] == before["bursts"] + 1


def test_demo_script_runs_green_end_to_end():
    assert demo.run(quiet=True) == 0
