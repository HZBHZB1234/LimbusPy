"""S0 地基的契约自洽测试。

这些断言守的是「A 层能不能低成本扩张」这条命脉 —— 它们不是测功能，是测**机制**：

1. 每个 payload 字段都必须声明绑定来源（arg / const / static），否则生成器会拒绝；
2. 每个时点都必须声明目标方法的完整参数名表，否则无法做签名漂移检测；
3. payload 里引用的参数名必须真的出现在 `params` 里（拼错一个字母就是静默取空值）；
4. 生成物（`OpsRegistry.g.cs` / `HookPayloadMap.g.cs`）必须覆盖契约里的每一项。

第 4 条尤其重要：它让「改了 toml 但忘了重新生成」在 CI 里立刻红，
而不是等到真机上发现某个时点不触发。
"""

from __future__ import annotations

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SPEC_DIR = os.path.join(ROOT, "ops", "spec")
REGISTRY_CS = os.path.join(ROOT, "kernel", "LimbusPy.Ops", "OpsRegistry.g.cs")
PAYLOAD_CS = os.path.join(ROOT, "kernel", "LimbusPy.Ops", "HookPayloadMap.g.cs")

#: 特殊来源，不对应目标方法的参数。
SPECIAL_ARGS = {"self", "return"}


def load_specs() -> dict:
    tomllib = pytest.importorskip("tomllib")
    merged: dict = {"ops": [], "hooks": []}
    for name in sorted(os.listdir(SPEC_DIR)):
        if not name.endswith(".toml"):
            continue
        with open(os.path.join(SPEC_DIR, name), "rb") as handle:
            data = tomllib.load(handle)
        merged["ops"].extend(data.get("op") or [])
        merged["hooks"].extend(data.get("hook") or [])
    return merged


@pytest.fixture(scope="module")
def spec() -> dict:
    return load_specs()


# --------------------------------------------------------------------------------------
# 1. payload 字段必须声明绑定来源
# --------------------------------------------------------------------------------------


def test_every_payload_field_declares_a_source(spec):
    for hook in spec["hooks"]:
        for item in hook["payload"]:
            sources = [key for key in ("arg", "const", "static") if key in item]
            assert sources, (
                "hook %r 的 payload 字段 %r 没有绑定来源（arg / const / static 之一）"
                % (hook["timing"], item["name"]))


# --------------------------------------------------------------------------------------
# 2. 每个时点必须声明目标方法参数名表
# --------------------------------------------------------------------------------------


def test_every_hook_declares_its_target_signature(spec):
    for hook in spec["hooks"]:
        # 零参数时点（OnZeroHp 这类）合法，但 `params` 这个键必须存在 ——
        # 安装期靠"空表匹配零参数方法"来定位，缺键就退化成"唯一候选"的弱校验。
        assert "params" in hook, (
            "hook %r 没有声明 params 键：没有参数名表就无法做签名漂移检测，"
            "也无法在安装期消解重载歧义" % hook["timing"])
        params = hook["params"]
        assert len(params) == len(set(params)), (
            "hook %r 的 params 有重复名字，参数名无法唯一定位" % hook["timing"])


# --------------------------------------------------------------------------------------
# 3. payload 引用的参数名必须存在于 params 里
# --------------------------------------------------------------------------------------


def test_payload_args_resolve_against_declared_params(spec):
    for hook in spec["hooks"]:
        params = set(hook.get("params") or [])
        for item in hook["payload"]:
            arg = item.get("arg")
            if not arg or arg in SPECIAL_ARGS:
                continue
            # "loseBuffInfo.keyword" 这种成员链，只要求宿主参数存在
            holder = arg.split(".", 1)[0]
            assert holder in params, (
                "hook %r 的字段 %r 绑定到参数 %r，但 params 里没有它（%s）"
                % (hook["timing"], item["name"], holder, ", ".join(sorted(params))))


# --------------------------------------------------------------------------------------
# 4. 生成物覆盖契约（改了 toml 忘重新生成 → 这里红）
# --------------------------------------------------------------------------------------


def _read(path: str) -> str:
    assert os.path.isfile(path), "生成物缺失：%s（运行 tools/gen_contract.py）" % path
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def test_registry_covers_every_op(spec):
    text = _read(REGISTRY_CS)
    for op in spec["ops"]:
        assert re.search(r'\{\s*"%s"\s*,' % re.escape(op["name"]), text), (
            "OpsRegistry.g.cs 里没有 op %r —— 是不是忘了重新生成？" % op["name"])

    # class/method 必须能被接上：生成的接线用的是 PascalCase 方法名
    for op in spec["ops"]:
        pascal = "".join(part[:1].upper() + part[1:] for part in op["method"].split("_") if part)
        assert "." + pascal in text, (
            "OpsRegistry.g.cs 里没有 %s.%s（契约 method=%r）"
            % (op["class"], pascal, op["method"]))


def test_payload_map_covers_every_hook_and_field(spec):
    text = _read(PAYLOAD_CS)
    for hook in spec["hooks"]:
        assert '["%s"] = new HookFieldBinding[]' % hook["timing"] in text, (
            "HookPayloadMap.g.cs 里没有时点 %r" % hook["timing"])
        for item in hook["payload"]:
            assert 'Name = "%s"' % item["name"] in text, (
                "HookPayloadMap.g.cs 里没有字段 %r（时点 %r）" % (item["name"], hook["timing"]))
        assert '["%s"] = new string[]' % hook["timing"] in text, (
            "HookPayloadMap.g.cs 里没有时点 %r 的参数名表" % hook["timing"])


def test_payload_map_types_match_contract(spec):
    """生成物里的 Type 必须和契约声明一致（防止改类型后忘了重新生成）。"""
    text = _read(PAYLOAD_CS)
    for hook in spec["hooks"]:
        for item in hook["payload"]:
            expected = 'Name = "%s", Type = "%s"' % (item["name"], item["type"])
            assert expected in text, (
                "HookPayloadMap.g.cs 里 %r 的类型不是 %r" % (item["name"], item["type"]))


# --------------------------------------------------------------------------------------
# 5. 改值型时点的声明必须自洽
# --------------------------------------------------------------------------------------


def test_mutating_hooks_declare_their_lane(spec):
    """``postfix_result`` 必须声明 result_type；``postfix`` 不该带 mutate。"""
    for hook in spec["hooks"]:
        kind = (hook.get("csharp") or {}).get("kind", "postfix")
        mutate = hook.get("mutate") or {}
        if kind == "postfix_result":
            assert "result_type" in mutate, (
                "时点 %r 是 postfix_result，但没有声明 mutate.result_type" % hook["timing"])
            assert mutate["result_type"] in ("int", "long", "float", "bool"), (
                "时点 %r 的 result_type 不受支持：%r" % (hook["timing"], mutate["result_type"]))
        elif kind == "postfix":
            assert not mutate, (
                "时点 %r 是观测型（postfix），不该声明 mutate" % hook["timing"])
        else:
            assert kind in ("prefix", "postfix_result"), (
                "时点 %r 的 kind 未知：%r" % (hook["timing"], kind))
