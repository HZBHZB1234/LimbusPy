"""A 层（封装 API）的**契约**测试。

它盯着三件容易漂移的事：

1. **契约源 ↔ Python 壳**：`ops/spec/api.toml` 里的每个 op 在 `api/ops.py` 里有对应方法，
   参数名/默认值/返回注解一致；
2. **契约源 ↔ 参考实现**：`MockOps` 实现了清单里的每个 op（否则 example/测试会假绿）；
3. **契约源 ↔ 时机 payload**：hook 的 payload 字段与生成的 dataclass 字段一一对应。

示例本身的**行为**断言在 ``test_examples.py``（它们只是这个契约的消费者，
不该让契约测试跟着示例改来改去）。
"""

from __future__ import annotations

import inspect
import json
import os
import sys

import pytest

from limbuspy.api import hooks as hook_api
from limbuspy.api import ops as ops_api
from limbuspy._mock import MockBattle, MockOps, MockUnit
from limbuspy.runtime import protocol
from limbuspy.runtime.errors import BridgeError

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
EXAMPLES = os.path.join(ROOT, "python", "examples")
MANIFEST = os.path.join(ROOT, "python", "limbuspy", "api", "_contract_manifest.json")

if EXAMPLES not in sys.path:
    sys.path.insert(0, EXAMPLES)


@pytest.fixture(scope="module")
def manifest() -> dict:
    with open(MANIFEST, "r", encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------------------
# 1. 契约 ↔ Python 壳
# --------------------------------------------------------------------------------------


def test_generated_wrapper_matches_manifest(manifest):
    assert manifest["ops"], "清单为空"
    for op in manifest["ops"]:
        group = getattr(ops_api, op["class"], None)
        assert group is not None, "缺少 Ops 分组 %s" % op["class"]
        method = getattr(group, op["method"], None)
        assert callable(method), "缺少 %s.%s（op %s）" % (op["class"], op["method"], op["name"])

        signature = inspect.signature(method)
        params = [name for name in signature.parameters if name != "self"]
        expected = [arg["name"] for arg in op["args"]]
        assert params == expected, "%s 的参数名漂移: %r != %r" % (op["name"], params, expected)
        for arg in op["args"]:
            has_default = signature.parameters[arg["name"]].default is not inspect.Parameter.empty
            assert has_default == arg.get("optional", False), \
                "%s.%s 的默认值语义漂移" % (op["name"], arg["name"])
        # 每个方法都要有 docstring（A 层的硬要求）
        assert (method.__doc__ or "").strip(), "%s.%s 缺少 docstring" % (op["class"], op["method"])
        assert op["name"] in method.__doc__, "%s.%s 的 docstring 没写 op 名" % (op["class"], op["method"])


def test_hooks_payloads_match_manifest(manifest):
    assert manifest["hooks"], "清单里没有 hook"
    for hook in manifest["hooks"]:
        payload_cls = hook_api.PAYLOAD_BY_TIMING.get(hook["timing"])
        assert payload_cls is not None, "缺少 %s 的 payload 类型" % hook["timing"]
        fields = [name for name in payload_cls.__dataclass_fields__]
        expected = [item["name"] for item in hook["payload"]]
        assert fields == expected, "%s 的 payload 字段漂移: %r != %r" % (hook["timing"], fields, expected)
        assert (payload_cls.__doc__ or "").strip(), "%s payload 缺 docstring" % hook["timing"]
        for item in hook["payload"]:
            field = payload_cls.__dataclass_fields__[item["name"]]
            if item["type"] == "int":
                assert field.type in ("int", int), "%s.%s 类型漂移" % (hook["timing"], item["name"])


def test_timings_are_exposed():
    assert hook_api.TIMINGS == list(hook_api.PAYLOAD_BY_TIMING)
    assert "DamageDealt" in hook_api.TIMINGS


# --------------------------------------------------------------------------------------
# 2. 契约 ↔ 参考实现
# --------------------------------------------------------------------------------------


def test_mock_ops_covers_manifest(manifest):
    battle = MockBattle([MockUnit(instance_id=1, hp=10, max_hp=10)])
    mock = MockOps(battle)
    for op in manifest["ops"]:
        handler = getattr(mock, "op_" + op["name"].replace(".", "_"), None)
        assert callable(handler), "MockOps 没实现 %s" % op["name"]
        # 参数名也要对得上（位置参数顺序按清单）
        params = list(inspect.signature(handler).parameters)
        assert params == ["args"], "MockOps.%s 签名应为 (args)" % op["name"]
        assert (handler.__doc__ or "").strip(), "MockOps.%s 缺 docstring" % op["name"]


def test_unknown_op_is_unsupported(manifest):
    battle = MockBattle([])
    mock = MockOps(battle)
    with pytest.raises(KeyError):
        mock.dispatch("unit.nope", {})


# --------------------------------------------------------------------------------------
# 3. 行为：示例走真实 LPCP 通路（场景本体在 test_examples.py）
# --------------------------------------------------------------------------------------
#
# 这里只保留「示例目录本身是干净的」这一条结构断言 —— 它防止有人在 examples/ 下再放
# 一份 limbuspy 副本，让回归测试跑在旧代码上。


def test_examples_dir_is_package_free():
    """examples/ 下不应该再有 limbuspy 包副本（P8：双份实现会让回归测试测旧副本）。"""
    assert not os.path.isdir(os.path.join(EXAMPLES, "limbuspy"))


@pytest.fixture(scope="module")
def demo():
    """``python/examples/run_examples.py``（示例驱动器）。"""
    import importlib.util

    path = os.path.join(EXAMPLES, "run_examples.py")
    spec = importlib.util.spec_from_file_location("examples_demo", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["examples_demo"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------------------
# 4. 协议/会话层：op 调用与能力协商
# --------------------------------------------------------------------------------------


def test_session_op_reports_unsupported(demo):
    """内核没实现的 op 要报 unsupported，而不是静默返回 None。"""
    harness = demo.ApiHarness("burst_on_hit", os.path.join(EXAMPLES, "burst_on_hit")).start()
    try:
        harness.ops.dispatch = lambda op, args: (_ for _ in ()).throw(KeyError(op))
        with pytest.raises(BridgeError) as err:
            harness.session.op("unit.info", {"unit": {"instance_id": 201}})
        assert err.value.code == protocol.ErrorCode.UNSUPPORTED
    finally:
        harness.close()


def test_session_has_op(demo):
    harness = demo.ApiHarness("burst_on_hit", os.path.join(EXAMPLES, "burst_on_hit")).start()
    try:
        assert harness.session.has_op("unit.damage")
        assert not harness.session.has_op("unit.does_not_exist")
        assert harness.session.ops_manifest.get("ops")
    finally:
        harness.close()


# --------------------------------------------------------------------------------------
# 5. 会话语义：缓存只活一次回调；Mod 上的入口必须无缓存
# --------------------------------------------------------------------------------------


def test_session_cache_is_optional(demo):
    """``use_cache=False`` 的会话读到的是当次真值（长生命周期会话不能缓存）。"""
    from limbuspy.game.session import Session

    harness = demo.ApiHarness("burst_on_hit", os.path.join(EXAMPLES, "burst_on_hit")).start()
    try:
        unit = harness.unit(201)
        ref = harness.battle.ref(unit)
        cached = Session(harness.bridge)
        fresh = Session(harness.bridge, use_cache=False)

        assert cached.read(ref, "Hp") == 300
        assert fresh.read(ref, "Hp") == 300

        harness.ops.dispatch("unit.damage", {"unit": ref, "amount": 40})

        assert cached.read(ref, "Hp") == 300, "带缓存的会话保留旧值（这是 ctx.session 的语义）"
        assert fresh.read(ref, "Hp") == 260, "无缓存会话必须读到新值"
        assert cached.use_cache is True and fresh.use_cache is False
    finally:
        harness.close()


def test_mod_session_and_ops_are_cacheless(demo):
    """``Mod.session`` / ``Mod.ops`` 活在整个 mod 生命周期上，必须无缓存、且互相解耦。"""
    harness = demo.ApiHarness("field_medic", os.path.join(EXAMPLES, "field_medic")).start()
    try:
        mod = harness.mod
        assert mod.session is not None
        assert mod.session.use_cache is False
        assert mod.ops is not None
        # ops 不搭在 Mod.session 上（它有自己的无缓存会话）
        assert mod.ops.unit._session is not mod.session
        assert mod.ops.unit._session.use_cache is False
        # 重复访问拿到同一个门面（不重复建连接对象）
        assert mod.ops is mod.ops and mod.session is mod.session
    finally:
        harness.close()


def test_on_supports_optional_context():
    """``@on`` 既支持 ``(self, ev)`` 也支持 ``(self, ev, ctx)``。"""
    from limbuspy.api import hooks

    seen = {}

    class FakeCtx:
        payload = {"unit": {"h": 4321}, "attacker": None, "old_hp": 40, "new_hp": 30,
                   "source": "PASSIVE", "timing": "ON_SUCCESS_ATTACK", "keyword": "SINKING"}

    @hooks.on("DamageDealt")
    def two_args(self, ev):
        seen["two"] = ev

    @hooks.on("DamageDealt")
    def three_args(self, ev, ctx):
        seen["three"] = (ev, ctx)

    context = FakeCtx()
    two_args(None, context)
    three_args(None, context)

    assert isinstance(seen["two"], hooks.DamageDealtPayload)
    assert (seen["two"].old_hp, seen["two"].new_hp) == (40, 30)
    assert isinstance(seen["three"][0], hooks.DamageDealtPayload)
    assert seen["three"][1] is context
    # 两个都被注册成真正的 hook（timing 一致）
    assert two_args.__limbuspy_hook__.timing == "DamageDealt"
    assert three_args.__limbuspy_hook__.timing == "DamageDealt"
