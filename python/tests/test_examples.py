"""``python/examples`` 的回归测试：示例也是代码，必须一直被跑。

两层：

1. **行为**：``run_examples.SCENARIOS`` 里每个场景的断言全部要成立 —— 那是示例自己声明的
   "这个示例想证明什么"。
2. **机制**：那些场景背后的关键机制各有一条独立断言（模块级注册、位图回填、
   字符串关键字的位序号绑定、热重载后内容计划仍在、``lp.unit(ev.target)`` 的句柄形态）。

全部无需游戏、无需 C#：走 mock kernel 的真实 LPCP 通路。
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXAMPLES = os.path.abspath(os.path.join(_HERE, "..", "examples"))
if _EXAMPLES not in sys.path:
    sys.path.insert(0, _EXAMPLES)

import run_examples as demo  # noqa: E402

import limbuspy as lp  # noqa: E402
from limbuspy.registration import (  # noqa: E402
    Cond,
    HasBuff,
    compile_predicate,
    once_per_round,
    plan_for,
    victim,
)


# --------------------------------------------------------------------------------------
# 1. 每个示例自己声明的断言
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(demo.SCENARIOS))
def test_example_scenario(name):
    result = demo.SCENARIOS[name]()
    failures = [label for label, ok in result["checks"] if not ok]
    assert not failures, "%s 失败: %s（%r）" % (name, failures, result)
    assert result["checks"], "%s 没有任何断言 —— 那它就不是示例，只是文档" % name


def test_scenarios_are_all_listed():
    """``SCENARIOS`` 必须覆盖 examples/ 下每个 mod 包（漏一个就等于它没被测）。

    ``sinking_tide`` 是例外：它有独立的驱动器 ``run_sinking_tide.py`` 与
    ``test_example_mod.py``（因为它要多验补丁/同步泳道/热重载）。
    """
    expected = {
        entry for entry in os.listdir(_EXAMPLES)
        if os.path.isdir(os.path.join(_EXAMPLES, entry)) and not entry.startswith(("_", "."))
    }
    expected.discard("sinking_tide")
    expected.update({"contrast", "actions"})
    assert set(demo.SCENARIOS) == expected, (
        "示例目录与场景表不一致: %r" % (expected ^ set(demo.SCENARIOS),))
    assert os.path.isfile(os.path.join(_EXAMPLES, "run_sinking_tide.py"))


# --------------------------------------------------------------------------------------
# 2. 模块级注册（hello_passive 的形状）
# --------------------------------------------------------------------------------------


def test_module_level_hook_is_registered_and_adapts_signature():
    """``@lp.on_hit`` 写在模块顶层也要真的被登记（不需要 Mod 子类）。

    这条曾经是"看起来支持、其实没扫"的坑：加载器只扫 ``type(instance).__mro__``，
    模块级函数根本进不了注册表，而门面文档写着两种写法都行。
    """
    harness = demo.ApiHarness("hello_passive", os.path.join(_EXAMPLES, "hello_passive")).start()
    try:
        names = [entry.name for entry in harness.runtime.registry.all_hooks()]
        assert "hello_passive.punish" in names, names
        before = harness.hp(201)
        harness.attack(101, 201, 20)
        assert harness.hp(201) == before - 20 - 3
    finally:
        harness.close()


def test_mod_is_optional():
    """没有 Mod 子类时加载器也会造一个裸实例（否则模块级写法根本起不来）。"""
    harness = demo.ApiHarness("hello_passive", os.path.join(_EXAMPLES, "hello_passive")).start()
    try:
        mod = harness.mod
        assert mod is not None
        assert mod.name == "hello_passive"
        assert mod.contents == []
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 3. 位图：C# 侧怎么算，mock 就怎么算
# --------------------------------------------------------------------------------------


def test_plan_bitmaps_match_the_registered_predicate():
    """``content.plan`` 发下去的 watch 与位序，要和谓词要求的一致。

    这条是"零往返条件"的地基：位错一位，谓词就会在错误的对象上成立 —— 而且不会报错。
    """
    harness = demo.ApiHarness("drowning_tide", os.path.join(_EXAMPLES, "drowning_tide")).start()
    try:
        plan = harness.ops.plan
        assert plan["watch"] == ["DROWNING"], plan
        entry = [row for row in plan["hooks"] if row["timing"] == "AttackSucceeded"]
        assert entry and entry[0]["victim"] == [0], plan

        # 只有挂了这个 buff 的单位才拿到这一位
        assert harness.mask("AttackSucceeded", 201) == 0
        harness.set_buff(201, "DROWNING", 3)
        assert harness.mask("AttackSucceeded", 201) == 1
        assert harness.mask("AttackSucceeded", 202) == 0

        # 自定义 Buff 被真注入（内核用 24576 起分配枚举值）
        assert harness.ops.injected == ["DROWNING"], harness.ops.injected
    finally:
        harness.close()


def test_string_keyword_gets_a_bit_at_plan_time():
    """``victim.has("SINKING")``（原版 Buff 的关键字）也要能绑到位序号。

    否则作者写了条件、注册期不报错、运行期恒假 —— 最难查的一类 bug。
    """
    pred = victim.has("SINKING") & once_per_round()
    assert isinstance(pred.children[0], HasBuff)
    assert pred.children[0].bit < 0, "还没进 plan_for 时不该有位序号"

    plan = plan_for([], [])
    # plan_for 只遍历 hook；这里直接拿一个带谓词的假 hook
    class _Hook:
        timing = "AttackSucceeded"
        when = pred

    plan = plan_for([_Hook()], [])
    assert plan["watch"] == ["SINKING"], plan
    assert plan["hooks"][0]["victim"] == [0], plan

    # 绑好之后，谓词就是一次按位与
    cond = Cond(mask_victim=1)
    assert compile_predicate(pred)(cond) is True
    cond = Cond(mask_victim=0)
    assert compile_predicate(pred)(cond) is False


def test_field_to_field_comparison():
    """``ev.old_hp < ev.new_hp`` 要按同一次事件求值，而不是变成恒假的反射比较。"""
    from limbuspy.api.hooks import DamageDealtPayload

    pred = lp.ev.old_hp < lp.ev.new_hp
    check = compile_predicate(pred)
    assert check(Cond(payload=DamageDealtPayload.from_wire({"old_hp": 20, "new_hp": 35}))) is True
    assert check(Cond(payload=DamageDealtPayload.from_wire({"old_hp": 35, "new_hp": 20}))) is False


def test_predicate_on_missing_field_is_false_not_an_error():
    """payload 里没有的字段 → 谓词为假（不是抛异常，也不是恒真）。"""
    from limbuspy.api.hooks import TurnStartedPayload

    check = compile_predicate(lp.ev.hp_damage > 10)
    assert check(Cond(payload=TurnStartedPayload.from_wire({}))) is False


# --------------------------------------------------------------------------------------
# 4. 热重载：内容计划不能丢
# --------------------------------------------------------------------------------------


def test_reload_republishes_content_plan():
    """重载后谓词必须仍然有效。

    曾经的 bug：``_publish_plan`` 只在首次加载时调用，热重载一次之后位图永远是 0 ——
    表现是"改完代码被动不触发了"，而日志里一切正常。
    """
    harness = demo.ApiHarness("drowning_tide", os.path.join(_EXAMPLES, "drowning_tide")).start()
    try:
        harness.set_buff(201, "DROWNING", 2)
        harness.emit("AttackSucceeded", {
            "unit": harness.ref(101), "target": harness.ref(201),
            "final_damage": 1, "real_damage": 1, "hp_damage": 1,
        }, victim=201, self_unit=101)
        before = harness.call("drowning.report")["tide_crush"]["crushes"]
        assert before == 1

        result = harness.runtime.reload([harness.mod_name], reason="test")
        assert result.ok, result.failed

        harness.set_buff(201, "DROWNING", 2)
        harness.start_round()                     # 新回合才允许再次触发
        harness.emit("AttackSucceeded", {
            "unit": harness.ref(101), "target": harness.ref(201),
            "final_damage": 1, "real_damage": 1, "hp_damage": 1,
        }, victim=201, self_unit=101)
        after = harness.call("drowning.report")["tide_crush"]["crushes"]
        # 注意：账本用的是**类属性**，而热重载会重新 import 模块 → 计数从头开始。
        # 所以这里要断言的是"重载后谓词仍然能成立"（== 1），而不是"累计到 2"。
        # 如果内容计划没重发，mask 会是 0，谓词恒假，这里会是 0。
        assert after == 1, "重载后谓词失效了（内容计划没有重发）"
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 5. 门面形态：时点 payload 的引用只有句柄
# --------------------------------------------------------------------------------------


def test_unit_accepts_payload_reference():
    """``lp.unit(ev.target)`` 必须能吃时点 payload 里的引用（那里只有句柄，没有 instance_id）。"""
    harness = demo.ApiHarness("hello_passive", os.path.join(_EXAMPLES, "hello_passive")).start()
    try:
        ref = harness.ref(201)
        assert "instance_id" not in ref, "harness 的引用应当只有句柄（与真机一致）"

        unit = lp.unit(ref)
        assert unit is not None
        assert unit.instance_id == 201
        assert unit.hp == harness.hp(201)

        # 拿到的快照可以直接喂回写操作
        assert lp.damage(unit, 5).ok
        assert harness.hp(201) == 295
    finally:
        harness.close()


def test_input_ref_normalisation():
    """``lp.unit`` 能吃 int / UnitRef / dict / Unit 四种写法。"""
    from limbuspy.api.types import UnitRef

    harness = demo.ApiHarness("hello_passive", os.path.join(_EXAMPLES, "hello_passive")).start()
    try:
        by_id = lp.unit(201)
        by_ref = lp.unit(UnitRef(handle=harness.ref(201)["h"]))
        by_dict = lp.unit({"instance_id": 201})
        again = lp.unit(by_id)
        assert by_id is again, "已经是 Unit 时应当原样返回"
        for candidate in (by_ref, by_dict, again):
            assert candidate is not None and candidate.instance_id == 201
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 6. 示例不是孤岛：文档里点名的示例都得存在
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("required", [
    "hello_passive", "burst_on_hit", "damage_ledger", "field_medic",
    "drowning_tide", "shield_warden", "battle_scribe", "sinking_tide",
])
def test_example_directory_exists(required):
    root = os.path.join(_EXAMPLES, required)
    assert os.path.isdir(root), "README 里点名的示例不存在: %s" % required
    assert os.path.isfile(os.path.join(root, "__init__.py"))


def test_readme_lists_every_example():
    with open(os.path.join(_EXAMPLES, "README.md"), "r", encoding="utf-8") as handle:
        text = handle.read()
    for name in sorted(demo.SCENARIOS):
        if name in ("contrast", "actions"):
            continue                      # 这两个是驱动器里的对照场景，不是 mod 包
        assert name in text, "README 没有提到示例 %s" % (name,)
