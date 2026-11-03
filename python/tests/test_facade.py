"""门面（``import limbuspy as lp``）的测试。

守的是问题 1 的两条硬承诺：

1. **往返发生在「获取」时，不发生在「读属性」时。**
   ``lp.unit(201)`` 之后读多少个属性，``ops.calls`` 都不再增加。
2. **``import limbuspy`` 不解析 2 MB 的反射绑定。**
   这条用 ``sys.modules`` 直接断言 —— 它曾经是每个 A 层用户的隐性成本。

外加降级行为：没有连接时所有读操作返回空值、所有写操作返回 ``ok=False``，绝不抛。
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading

import pytest

import limbuspy as lp
from limbuspy._mock import MockBattle, MockKernel, MockOps, MockUnit
from limbuspy.mod.runtime import ModRuntime
from limbuspy.runtime.client import Bridge
from limbuspy.runtime.transport import FileTransport

UNITS = [
    dict(instance_id=101, origin_id=1, hp=40, max_hp=40, is_player=True, position="MAIN"),
    dict(instance_id=201, origin_id=10, hp=300, max_hp=300, is_player=False, position="MAIN",
         buffs={"SINKING": [8, 3]}),
]


def _pipe_pair():
    left, right = socket.socketpair()
    left_file = left.makefile("rwb", buffering=0)
    right_file = right.makefile("rwb", buffering=0)
    left.close()
    right.close()
    return left_file, right_file


@pytest.fixture()
def live():
    """一个连着 mock kernel 的门面（走真实 LPCP 通路，和真机同一条代码路径）。"""
    battle = MockBattle([MockUnit(**data) for data in UNITS], round_=3)
    ops = MockOps(battle)
    client_file, kernel_file = _pipe_pair()
    kernel = MockKernel(
        FileTransport(kernel_file, "mock-kernel"),
        ops=ops,
        battle={"round": 3, "wave": 1, "units": []},
    )
    bridge = Bridge(FileTransport(client_file, "limbuspy-facade"), name="facade").start()
    mods_dir = tempfile.mkdtemp(prefix="limbuspy-facade-mods-")
    state_dir = tempfile.mkdtemp(prefix="limbuspy-facade-state-")
    runtime = ModRuntime(bridge, mods_dir, state_dir, logger=lambda message: None)
    thread = threading.Thread(target=lambda: kernel.start(5.0), daemon=True)
    thread.start()
    runtime.start()
    thread.join(5.0)

    try:
        yield {"runtime": runtime, "ops": ops, "battle": battle, "bridge": bridge}
    finally:
        runtime.stop()
        bridge.close()
        kernel.close()


# --------------------------------------------------------------------------------------
# 1. 导入成本
# --------------------------------------------------------------------------------------


def test_import_does_not_pull_generated_bindings():
    """``import limbuspy`` 不该解析 2 MB 的反射绑定（曾经的隐性成本）。

    必须在**子进程**里断言：同一次 pytest 里别的用例可能已经把 bindings 导进来了，
    那样断言就变成依赖执行顺序的假绿/假红。
    """
    import subprocess

    python_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    code = (
        "import sys\n"
        "import limbuspy\n"
        "print('bindings' if 'limbuspy.api.bindings' in sys.modules else 'clean')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=python_root,
        env={**os.environ, "PYTHONPATH": python_root},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith("clean"), (
        "import limbuspy 顺带把 2 MB 的生成绑定拉进来了：%r" % (completed.stdout.strip(),))


def test_tutorial_name_budget():
    """教程级名字必须收敛：只有 ``TUTORIAL`` 里那 15 个，且它们都真实存在。

    这条是 API 设计的守门员 —— 名字一多，作者就要在文档里找"这次该用哪个"，
    而那正是我们要消灭的心智负担。新增名字前先问：能不能挂到已有的值对象上？
    """
    assert len(lp.facade.TUTORIAL) <= 15, (
        "教程级名字超预算: %d 个 %r" % (len(lp.facade.TUTORIAL), lp.facade.TUTORIAL))
    assert len(set(lp.facade.TUTORIAL)) == len(lp.facade.TUTORIAL), "TUTORIAL 里有重复名字"
    for name in lp.facade.TUTORIAL:
        assert hasattr(lp, name), "TUTORIAL 里的 %r 在门面上不存在" % (name,)


def test_no_module_level_value_attributes():
    """模块级只放函数和类：值必须挂在对象属性上。

    模块属性只能靠 PEP 562 ``__getattr__``，pyright/mypy 不追踪它 ——
    所以 ``lp.round`` 必须是函数 ``lp.round()``，回合数走 ``lp.battle().round``。
    """
    assert callable(lp.round)
    battle = lp.battle()
    assert isinstance(battle, lp.Battle)


# --------------------------------------------------------------------------------------
# 2. 零往返承诺
# --------------------------------------------------------------------------------------


def test_unit_snapshot_attributes_cost_nothing(live):
    """``lp.unit()`` 付一次往返，之后读多少属性都不再付。"""
    ops = live["ops"]
    before = len(ops.calls)

    unit = lp.unit(201)
    assert unit is not None
    after_read = len(ops.calls)
    assert after_read - before == 1, "取快照应该只花 1 次往返，实际 %d" % (after_read - before)

    # 读一堆属性 —— 计数不应该变
    _ = unit.hp, unit.max_hp, unit.mp, unit.hp_ratio, unit.missing_hp
    _ = unit.is_dead, unit.is_break, unit.is_player, unit.level, unit.alive
    _ = unit.instance_id, unit.origin_id, unit.faction, unit.position
    assert len(ops.calls) == after_read, (
        "读属性产生了额外往返：%r" % (ops.calls[after_read:],))


def test_battle_reads_units_once(live):
    """``lp.battle()`` 的单位列表只花 1 次批量往返，切片/筛选都免费。"""
    ops = live["ops"]
    before = len(ops.calls)
    battle = lp.battle()
    bulk = [name for name in ops.calls[before:] if name in ("battle.states", "battle.units")]
    assert len(bulk) == 1, "全场单位应该只批量读一次，实际 %r" % (ops.calls[before:],)

    before_filters = len(ops.calls)
    _ = battle.players, battle.enemies, battle.alive, battle.round
    _ = battle.unit(201), battle.of_origin(10)
    assert len(ops.calls) == before_filters, "筛选/查找又产生了往返"


def test_buff_stack_is_one_call_not_a_list_scan(live):
    """``u.buff_stack(k)`` 走专门的 op，不把整张 buff 表拉回来。"""
    ops = live["ops"]
    unit = lp.unit(201)
    before = len(ops.calls)
    assert unit.buff_stack("SINKING") == 8
    assert ops.calls[before:] == ["unit.buff_stacks"], ops.calls[before:]


# --------------------------------------------------------------------------------------
# 3. 动作与返回值形态
# --------------------------------------------------------------------------------------


def test_damage_returns_action_result(live):
    result = lp.damage(201, 25)
    assert isinstance(result, lp.ActionResult)
    assert result.ok
    assert result.state is not None
    assert result.state.hp == 275
    assert live["battle"].resolve({"instance_id": 201}).hp == 275


def test_failed_action_is_not_an_exception(live):
    """打一个已死的单位：返回 ok=False + reason，而不是抛异常。"""
    battle = live["battle"]
    battle.resolve({"instance_id": 101}).is_dead = True
    result = lp.damage(101, 10)
    assert isinstance(result, lp.ActionResult)
    assert not result.ok


def test_unknown_unit_reads_none(live):
    assert lp.unit(999999) is None


# --------------------------------------------------------------------------------------
# 4. 降级：没有连接时也不炸
# --------------------------------------------------------------------------------------


def test_degrades_without_bridge():
    """未连接时：读返回空值，写返回 ok=False —— 一行异常都不许抛。"""
    lpm = lp.facade
    lpm.detach()
    try:
        status = lp.status()
        assert isinstance(status, lp.Status)
        assert not status.connected

        assert lp.units() == []
        assert lp.unit(1) is None
        assert lp.player() is None
        assert lp.round() is None
        assert lp.buff_stacks("SINKING") == {}
        assert lp.battle().round is None

        for action in (
            lp.damage(1, 5), lp.heal(1, 5), lp.set_hp(1, 5), lp.set_sp(1, 1),
            lp.add_shield(1, 5), lp.set_speed(1, 5), lp.refresh_speed(1),
            lp.break_unit(1), lp.add_buff(1, "SINKING"), lp.add_passive(1, 1),
            lp.burst(1, "SINKING"),
        ):
            assert isinstance(action, lp.ActionResult)
            assert not action.ok
            assert action.reason
    finally:
        lpm.detach()


# --------------------------------------------------------------------------------------
# 5. 逃生口
# --------------------------------------------------------------------------------------


def test_raw_never_raises():
    """B 层逃生口在拿不到东西时返回 None，而不是把异常丢给作者。"""
    lpm = lp.facade
    lpm.detach()
    unit = lp.Unit(lp.UnitInfo(instance_id=1))
    try:
        assert unit.raw.get("Hp") is None
        assert unit.raw.call("Whatever") is None
        assert unit.raw.set("Hp", 1) is False
    finally:
        lpm.detach()
