#!/usr/bin/env python
"""由 `ops/spec/*.toml` 生成 A 层（封装 API）的四个产物 —— 单一契约源，三向生成。

    ops/spec/api.toml
        ├─ python/limbuspy/api/ops.py                 类型化封装（真类 + docstring）
        ├─ python/limbuspy/api/hooks.py               时机 payload dataclass + on() 的 overload
        ├─ kernel/LimbusPy.Ops/OpsManifest.g.cs       C# 注册表/manifest（启动自检用）
        └─ python/limbuspy/api/_contract_manifest.json 运行时协商 + 测试比对

用法::

    python tools/gen_contract.py                 # 生成
    python tools/gen_contract.py --check         # 只校验产物是否最新（CI 门禁）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPEC_DIR = os.path.join(ROOT, "ops", "spec")
OUT_OPS = os.path.join(ROOT, "python", "limbuspy", "api", "ops.py")
OUT_HOOKS = os.path.join(ROOT, "python", "limbuspy", "api", "hooks.py")
OUT_CS = os.path.join(ROOT, "kernel", "LimbusPy.Ops", "OpsManifest.g.cs")
OUT_CS_REGISTRY = os.path.join(ROOT, "kernel", "LimbusPy.Ops", "OpsRegistry.g.cs")
OUT_CS_PAYLOAD = os.path.join(ROOT, "kernel", "LimbusPy.Ops", "HookPayloadMap.g.cs")
OUT_MANIFEST = os.path.join(ROOT, "python", "limbuspy", "api", "_contract_manifest.json")

GENERATOR_VERSION = "0.1.0"
HEADER = '"""由 tools/gen_contract.py 从 ops/spec/api.toml 生成 —— 不要手改。"""'

#: spec 参数类型 → Python 注解
ARG_TYPES = {
    "unit": "UnitRefLike",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "str": "str",
    "timing": "str",
    "source": "str",
    "ability_source": "str",
    "buff_keyword": "str",
    "unit_keyword": "str",
    "list_str": "List[str]",
    "list_obj": "List[Dict[str, Any]]",
}

#: spec 返回类型 → (Python 注解, 转换表达式模板, 需要的 import)
RETURN_TYPES = {
    "void": ("None", "{raw}", set()),
    "int": ("int", "{raw}", set()),
    "float": ("float", "{raw}", set()),
    "bool": ("bool", "{raw}", set()),
    "str": ("str", "{raw}", set()),
    "Optional[int]": ("Optional[int]", "{raw}", set()),
    "UnitInfo": ("UnitInfo", "UnitInfo.from_wire({raw})", {"UnitInfo"}),
    "UnitState": ("UnitState", "UnitState.from_wire({raw})", {"UnitState"}),
    "List[UnitState]": ("List[UnitState]", "[UnitState.from_wire(row) for row in ({raw} or [])]", {"UnitState"}),
    "BurstInfo": ("BurstInfo", "BurstInfo.from_wire({raw})", {"BurstInfo"}),
    "List[UnitInfo]": ("List[UnitInfo]", "[UnitInfo.from_wire(row) for row in ({raw} or [])]", {"UnitInfo"}),
    "List[BuffInfo]": ("List[BuffInfo]", "[BuffInfo.from_wire(row) for row in ({raw} or [])]", {"BuffInfo"}),
}

PAYLOAD_TYPES = {
    "unit": ("Optional[UnitRef]", "UnitRef.from_wire"),
    "int": ("int", "int"),
    "float": ("float", "float"),
    "bool": ("bool", "bool"),
    "str": ("str", "str"),
}


def load_specs() -> dict:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python < 3.11
        raise SystemExit("需要 Python 3.11+（tomllib）来生成契约")
    merged = {"meta": {}, "ops": [], "hooks": []}
    for name in sorted(os.listdir(SPEC_DIR)):
        if not name.endswith(".toml"):
            continue
        with open(os.path.join(SPEC_DIR, name), "rb") as handle:
            data = tomllib.load(handle)
        merged["meta"].update(data.get("meta") or {})
        merged["ops"].extend(data.get("op") or [])
        merged["hooks"].extend(data.get("hook") or [])
    return merged


def py_annotation(arg: dict) -> str:
    kind = arg.get("type") or "str"
    base = ARG_TYPES.get(kind)
    if base is None:
        raise SystemExit("未知参数类型 %r（op 参数 %r）" % (kind, arg.get("name")))
    return base


def default_literal(arg: dict) -> str | None:
    return arg.get("default")


# --------------------------------------------------------------------------------------
# Python: api/ops.py
# --------------------------------------------------------------------------------------


def emit_ops(spec: dict) -> str:
    groups: dict[str, list[dict]] = {}
    for op in spec["ops"]:
        groups.setdefault(op["class"], []).append(op)

    imports = set()
    for op in spec["ops"]:
        _ann, _expr, needs = RETURN_TYPES[op["returns"]]
        imports |= needs
    type_imports = sorted(imports | {"UnitRefLike", "to_wire_unit as _wire_unit"})

    lines = [HEADER, "", "from __future__ import annotations", "",
             "from typing import Any, Dict, List, Optional", "",
             "from .types import %s" % ", ".join(type_imports), "",
             '__contract_version__ = %r' % spec["meta"].get("version", "1.0"), "",
             ""]

    for cls_name in sorted(groups):
        lines.append("class %s:" % cls_name)
        lines.append('    """%s 封装（A 层）：一次调用 = 一次 crossing，语义在 C# 侧。"""'
                     % cls_name.replace("Ops", "").lower())
        lines.append("")
        lines.append("    __slots__ = (\"_session\",)")
        lines.append("")
        lines.append("    def __init__(self, session) -> None:")
        lines.append("        self._session = session")
        lines.append("")

        for op in sorted(groups[cls_name], key=lambda item: item["method"]):
            args = op.get("args") or []
            params = ["self"]
            for arg in args:
                annotation = py_annotation(arg)
                default = default_literal(arg)
                params.append("%s: %s%s" % (arg["name"], annotation,
                                            " = %s" % default if default else ""))
            ret_ann, ret_expr, _ = RETURN_TYPES[op["returns"]]
            lines.append("    def %s(%s) -> %s:" % (op["method"], ", ".join(params), ret_ann))
            doc_lines = [op.get("summary") or op["name"]]
            body = (op.get("doc") or "").strip("\n")
            if body:
                doc_lines.append("")
                doc_lines.extend(body.splitlines())
            doc_lines.append("")
            doc_lines.append("op: ``%s``（stability=%s）" % (op["name"], op.get("stability", "stable")))
            for arg in args:
                if arg.get("doc"):
                    doc_lines.append(":param %s: %s" % (arg["name"], arg["doc"]))
            if op.get("returns_doc"):
                doc_lines.append(":returns: %s" % op["returns_doc"])
            lines.append('        """' + doc_lines[0])
            for extra in doc_lines[1:]:
                lines.append("        " + extra if extra else "")
            lines.append('        """')
            wire = "{%s}" % ", ".join(
                "%r: _wire_%s(%s)" % (arg["name"], arg["type"], arg["name"]) if arg["type"] == "unit"
                else "%r: %s" % (arg["name"], arg["name"])
                for arg in args)
            lines.append("        return %s" % ret_expr.format(
                raw="self._session.op(%r, %s)" % (op["name"], wire)))
            lines.append("")
        lines.append("")

    lines.extend([
        "",
        "class Ops:",
        '    """A 层入口：``ops.unit.damage(...)`` / ``ops.battle.units()``。"""',
        "",
        "    __slots__ = (%s)" % ", ".join('"%s"' % cls_name.replace("Ops", "").lower()
                                       for cls_name in sorted(groups)),
        "",
        "    def __init__(self, session) -> None:",
    ])
    for cls_name in sorted(groups):
        attr = cls_name.replace("Ops", "").lower()
        lines.append("        self.%s = %s(session)" % (attr, cls_name))
    lines.extend([
        "",
        "",
        "def ops(session) -> Ops:",
        '    """给一个 session 造 A 层入口（通常用 ``ctx.ops`` 就够了）。"""',
        "    return Ops(session)",
        "",
    ])

    # 单位引用归一化
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Python: api/hooks.py（payload + on() 的 overload + 运行时）
# --------------------------------------------------------------------------------------

HOOKS_RUNTIME = '''

# --------------------------------------------------------------------------------------
# 运行时：`@on("Timing")` 注册成普通 hook，回调里把 ctx.payload 换成类型化对象
# --------------------------------------------------------------------------------------


def wants_context(fn: Any) -> bool:
    """回调是否接受第三个参数（``ctx``）。约定：``def f(self, ev, ctx)``。"""
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = [p for p in parameters
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    has_varargs = any(p.kind == p.VAR_POSITIONAL for p in parameters)
    return has_varargs or len(positional) >= 3


def _arity(fn: Any) -> int:
    """位置参数个数；``*args`` 或签名读不到时返回 ``-1``（表示"愿意全收"）。"""
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return -1
    if any(p.kind == p.VAR_POSITIONAL for p in parameters):
        return -1
    return sum(1 for p in parameters
               if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))


def on(timing: str, when: Any = None, allow_rpc: bool = False, **filters: Any):
    """声明一个 A 层时机回调。

    **三种签名都可以**（装饰器按位置参数个数自己判断，作者不用记规则）::

        @on("DamageDealt")                     # 类体：只要 payload
        def on_damage(self, ev: DamageDealtPayload) -> None: ...

        @on("DamageDealt")                     # 类体：还要 ctx（ops/session）
        def on_damage(self, ev: DamageDealtPayload, ctx) -> None: ...

        @on("DamageDealt")                     # 模块顶层：没有 self
        def on_damage(ev: DamageDealtPayload) -> None: ...

    过滤条件用 ``when=``（声明式谓词，注册期编译、运行期零往返）::

        @on("AttackSucceeded", when=victim.has(Drowning))
        def drown(self, ev: AttackSucceededPayload) -> None: ...

    与高级层 ``@hook`` 的区别：payload 是**归一化的类型化对象**（C# 侧已经拆好参数），
    不需要知道游戏方法的顺序。
    """

    def decorate(fn):
        payload_cls = PAYLOAD_BY_TIMING.get(timing)
        priority = int(filters.get("priority") or 0)
        spec = HookSpec(timing, dict(filters), priority, False, None,
                        when=when, allow_rpc=bool(allow_rpc))
        arity = _arity(fn)

        def wrapper(*args: Any):
            # 内核/运行时永远只传一个 ctx；类体里由 Python 描述符补上 self，
            # 所以这里看到 1 个参数 = 模块级函数，2 个 = 绑定的方法。
            if len(args) == 1:
                bound, owner, ctx = False, None, args[0]
            elif len(args) == 2:
                bound, owner, ctx = True, args[0], args[1]
            else:  # pragma: no cover - 运行时不会这么调
                raise TypeError("@on 回调只接受 (ctx) 或 (self, ctx)，收到 %d 个参数" % (len(args),))

            payload = ctx.payload
            if payload_cls is not None:
                if isinstance(payload, dict):
                    payload = payload_cls.from_wire(payload)
                elif payload is None:
                    payload = payload_cls.from_wire({})

            # 去掉 self 之后还剩几个位置参数，决定作者要不要 ctx。
            slots = arity - 1 if bound else arity
            if arity < 0 or slots >= 2:
                return fn(owner, payload, ctx) if bound else fn(payload, ctx)
            return fn(owner, payload) if bound else fn(payload)

        wrapper.__name__ = getattr(fn, "__name__", "on_" + timing.lower())
        wrapper.__doc__ = fn.__doc__
        wrapper.__limbuspy_hook__ = spec
        return wrapper

    return decorate
'''

HOOKS_IMPORTS = """from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, overload

from ..mod.decorators import HookSpec
from .types import UnitRef
"""


def payload_class_name(timing: str) -> str:
    return timing + "Payload"


def emit_hooks(spec: dict) -> str:
    lines = [HEADER, "", HOOKS_IMPORTS, ""]
    for hook in spec["hooks"]:
        cls = payload_class_name(hook["timing"])
        lines.append("@dataclass(frozen=True)")
        lines.append("class %s:" % cls)
        lines.append('    """%s：%s' % (hook["timing"], hook.get("summary") or ""))
        lines.append("")
        lines.append("    IL2CPP 挂点: ``%s.%s``（%s）" % (
            hook["csharp"]["target"], hook["csharp"]["method"], hook["csharp"].get("kind", "postfix")))
        lines.append('    """')
        lines.append("")
        for item in hook["payload"]:
            annotation, _conv = PAYLOAD_TYPES[item["type"]]
            if item["type"] == "unit":
                default = " = None"
            elif item["type"] == "int":
                default = " = 0"
            elif item["type"] == "float":
                default = " = 0.0"
            elif item["type"] == "bool":
                default = " = False"
            else:
                default = ' = ""'
            lines.append("    %s: %s%s" % (item["name"], annotation, default))
        lines.append("")
        lines.append("    @classmethod")
        lines.append("    def from_wire(cls, raw: Dict[str, Any]) -> \"%s\":" % cls)
        lines.append("        raw = raw or {}")
        args = []
        for item in hook["payload"]:
            annotation, conv = PAYLOAD_TYPES[item["type"]]
            if item["type"] == "unit":
                args.append("%s=UnitRef.from_wire(raw.get(%r))" % (item["name"], item["name"]))
            elif item["type"] == "int":
                args.append("%s=int(raw.get(%r) or 0)" % (item["name"], item["name"]))
            elif item["type"] == "bool":
                args.append("%s=bool(raw.get(%r))" % (item["name"], item["name"]))
            else:
                args.append("%s=str(raw.get(%r) or \"\")" % (item["name"], item["name"]))
        lines.append("        return cls(%s)" % ", ".join(args))
        lines.append("")
        lines.append("")

    lines.append("#: 时机名 → payload 类型")
    lines.append("PAYLOAD_BY_TIMING: Dict[str, Any] = {")
    for hook in spec["hooks"]:
        lines.append("    %r: %s," % (hook["timing"], payload_class_name(hook["timing"])))
    lines.append("}")
    lines.append("")

    lines.append("#: 全部 A 层时机（可用于校验/文档）")
    lines.append("TIMINGS: List[str] = [%s]" % ", ".join(repr(h["timing"]) for h in spec["hooks"]))
    lines.append("")

    # overload：让 @on("DamageDealt") 的 payload 类型被静态推导
    lines.append("# -- 静态推导：@on(\"Timing\") 的 payload 类型 ------------------------------------")
    lines.append("")
    for hook in spec["hooks"]:
        cls = payload_class_name(hook["timing"])
        lines.append("@overload")
        lines.append('def on(timing: Literal["%s"], when: Any = ..., allow_rpc: bool = ..., '
                     '**filters: Any) -> Callable[[Callable[..., Any]], Any]: ...'
                     % hook["timing"])
    lines.append("")
    lines.append(HOOKS_RUNTIME)
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# C#: OpsManifest.g.cs
# --------------------------------------------------------------------------------------


def emit_csharp(spec: dict) -> str:
    lines = [
        "// <auto-generated>由 tools/gen_contract.py 从 ops/spec/api.toml 生成 —— 不要手改。</auto-generated>",
        "using System;",
        "using System.Collections.Generic;",
        "using LimbusPy.Bridge.Core.Ops;",
        "",
        "namespace LimbusPy.Ops",
        "{",
        "    /// <summary>契约清单：启动时与注册表比对，缺实现直接报错（开发期就能发现）。</summary>",
        "    public static class OpsManifest",
        "    {",
        '        public const string Version = "%s";' % spec["meta"].get("version", "1.0"),
        "",
        "        public static readonly OpSpec[] Ops = new OpSpec[]",
        "        {",
    ]
    for op in spec["ops"]:
        arg_names = ", ".join('"%s"' % arg["name"] for arg in op.get("args") or [])
        arg_types = ", ".join('"%s"' % arg["type"] for arg in op.get("args") or [])
        lines.append('            new OpSpec("%s", "%s", "%s", new string[] { %s }, new string[] { %s }, "%s"),'
                     % (op["name"], op["class"], op.get("stability", "stable"),
                        arg_names, arg_types, op["returns"]))
    lines.extend(["        };", "", "        public static readonly HookSpec[] Hooks = new HookSpec[]", "        {"])
    for hook in spec["hooks"]:
        names = ", ".join('"%s"' % item["name"] for item in hook["payload"])
        types = ", ".join('"%s"' % item["type"] for item in hook["payload"])
        extra = _hook_extra(hook)
        lines.append('            new HookSpec("%s", "%s", "%s", "%s", new string[] { %s }, new string[] { %s }%s),'
                     % (hook["timing"], hook["csharp"]["target"], hook["csharp"]["method"],
                        hook["csharp"].get("kind", "postfix"), names, types, extra))
    lines.extend(["        };", "    }", "}", ""])
    return "\n".join(lines)


def _hook_extra(hook: dict) -> str:
    """hook 的扩展元数据袋（目前只有 mutate.result_type）。"""
    mutate = hook.get("mutate") or {}
    pairs = []
    for key in ("result_type",):
        if key in mutate:
            pairs.append('["%s"] = "%s"' % (key, mutate[key]))
    if not pairs:
        return ""
    return (", new Dictionary<string, object>(StringComparer.Ordinal) { %s }" % ", ".join(pairs))


# --------------------------------------------------------------------------------------
# manifest.json
# --------------------------------------------------------------------------------------


def _pascal(name: str) -> str:
    """``buff_stacks`` → ``BuffStacks``（契约 method 名 → C# 方法名）。"""
    return "".join(part[:1].upper() + part[1:] for part in str(name).split("_") if part)


def _owner_param(class_name: str) -> str:
    """``UnitOps`` → ``unitOps``（生成代码里持有实现的字段名）。"""
    return class_name[:1].lower() + class_name[1:]


def emit_csharp_registry(spec: dict) -> str:
    """op 名 → 实现的注册表。

    契约里已经有 ``class`` 与 ``method``，所以这张表是纯机械映射：把「手写一行
    ``Register("unit.info", _units.Info)``」变成生成物。加一个 op 从此不再需要改 C# 接线。
    """
    classes = []
    for op in spec["ops"]:
        if op["class"] not in classes:
            classes.append(op["class"])

    lines = [
        "// <auto-generated>由 tools/gen_contract.py 从 ops/spec/*.toml 生成 —— 不要手改。</auto-generated>",
        "using System;",
        "using System.Collections.Generic;",
        "",
        "namespace LimbusPy.Ops",
        "{",
        "    /// <summary>",
        "    /// op 名 → 实现的委托表（契约的 class/method 生成）。",
        "    ///",
        "    /// 这张表存在，是为了让「新增一个 op」只需要写实现方法：接线、清单、Python 壳、",
        "    /// docstring 全由 tools/gen_contract.py 产出。",
        "    /// </summary>",
        "    internal static class OpsRegistry",
        "    {",
        "        internal static Dictionary<string, Func<Dictionary<string, object>, object>> Build(%s)"
        % ", ".join("%s %s" % (cls, _owner_param(cls)) for cls in classes),
        "        {",
        "            return new Dictionary<string, Func<Dictionary<string, object>, object>>(StringComparer.Ordinal)",
        "            {",
    ]
    for op in spec["ops"]:
        lines.append('                { "%s", %s.%s },'
                     % (op["name"], _owner_param(op["class"]), _pascal(op["method"])))
    lines.extend([
        "            };",
        "        }",
        "    }",
        "}",
        "",
    ])
    return "\n".join(lines)


def _field_source(item: dict, timing: str) -> str:
    """解析一个 payload 字段的绑定来源，返回 C# 初始化片段。"""
    name = item["name"]
    kind = item["type"]
    if "const" in item:
        return ('new HookFieldBinding { Name = "%s", Type = "%s", Source = HookFieldSource.Constant, Literal = "%s" }'
                % (name, kind, item["const"]))
    if "static" in item:
        return ('new HookFieldBinding { Name = "%s", Type = "%s", Source = HookFieldSource.Static, Literal = "%s" }'
                % (name, kind, item["static"]))
    arg = item.get("arg")
    if not arg:
        raise SystemExit(
            "hook %r 的 payload 字段 %r 没有绑定来源：必须写 arg / const / static 之一"
            "（arg = \"参数名\" 或 \"self\" / \"return\" / \"参数.成员\"）" % (timing, name))
    if arg == "self":
        return ('new HookFieldBinding { Name = "%s", Type = "%s", Source = HookFieldSource.Self }'
                % (name, kind))
    if arg == "return":
        return ('new HookFieldBinding { Name = "%s", Type = "%s", Source = HookFieldSource.Return }'
                % (name, kind))
    if "." in arg:
        holder, member = arg.split(".", 1)
        return ('new HookFieldBinding { Name = "%s", Type = "%s", Source = HookFieldSource.Member, ArgName = "%s", MemberPath = "%s" }'
                % (name, kind, holder, member))
    return ('new HookFieldBinding { Name = "%s", Type = "%s", Source = HookFieldSource.Argument, ArgName = "%s" }'
            % (name, kind, arg))


def emit_csharp_payload(spec: dict) -> str:
    """payload 字段 → 游戏方法参数的映射表。

    这张表取代了原先手写的 ``HookTable.Shape()`` switch。加一个时点从此只需要改 toml：
    字段来源按**参数名**声明，安装期再解析成下标（并顺带校验签名漂移、消解重载歧义）。
    """
    lines = [
        "// <auto-generated>由 tools/gen_contract.py 从 ops/spec/*.toml 生成 —— 不要手改。</auto-generated>",
        "using System;",
        "using System.Collections.Generic;",
        "",
        "namespace LimbusPy.Ops",
        "{",
        "    /// <summary>一个 payload 字段的值从哪里来。</summary>",
        "    internal enum HookFieldSource",
        "    {",
        "        /// <summary>被补丁的实例（BattleUnitModel 自己）。</summary>",
        "        Self,",
        "        /// <summary>目标方法的一个参数，按参数名解析成下标。</summary>",
        "        Argument,",
        "        /// <summary>某个参数对象上的成员（例如 loseBuffInfo.keyword）。</summary>",
        "        Member,",
        "        /// <summary>契约里写死的常量。</summary>",
        "        Constant,",
        "        /// <summary>静态调用（例如 StageController.GetCurrentRound()）。</summary>",
        "        Static,",
        "        /// <summary>返回值（配合 kind = prefix_result 才能改值）。</summary>",
        "        Return,",
        "    }",
        "",
        "    /// <summary>一个 payload 字段怎么从补丁参数里取出来。</summary>",
        "    internal sealed class HookFieldBinding",
        "    {",
        "        public string Name;",
        "        public string Type;",
        "        public HookFieldSource Source;",
        "        /// <summary>目标方法的参数名（Argument / Member 用）。</summary>",
        "        public string ArgName;",
        "        /// <summary>参数对象上的成员链（Member 用）。</summary>",
        "        public string MemberPath;",
        "        /// <summary>常量字面量或静态目标（Constant / Static 用）。</summary>",
        "        public string Literal;",
        "        /// <summary>安装期解析出来的参数下标；-1 = 未解析。</summary>",
        "        public int ArgIndex = -1;",
        "    }",
        "",
        "    /// <summary>时点 → 目标方法参数名表 / payload 字段绑定表。</summary>",
        "    internal static class HookPayloadMap",
        "    {",
        "        /// <summary>契约声明的目标方法参数名（安装期用来校验签名漂移）。</summary>",
        "        internal static readonly Dictionary<string, string[]> Params =",
        "            new Dictionary<string, string[]>(StringComparer.Ordinal)",
        "        {",
    ]
    for hook in spec["hooks"]:
        # 注意用 "params" in hook 而不是真值判断：零参数时点（OnZeroHp 这类）
        # 的 params 是空列表，但它依然要进表 —— 安装期靠"空表匹配零参数方法"来定位。
        if "params" not in hook:
            continue
        lines.append('            ["%s"] = new string[] { %s },'
                     % (hook["timing"], ", ".join('"%s"' % p for p in hook["params"])))
    lines.extend([
        "        };",
        "",
        "        /// <summary>payload 字段 → 参数来源。</summary>",
        "        internal static readonly Dictionary<string, HookFieldBinding[]> Fields =",
        "            new Dictionary<string, HookFieldBinding[]>(StringComparer.Ordinal)",
        "        {",
    ])
    for hook in spec["hooks"]:
        lines.append('            ["%s"] = new HookFieldBinding[]' % hook["timing"])
        lines.append("            {")
        for item in hook["payload"]:
            lines.append("                %s," % _field_source(item, hook["timing"]))
        lines.append("            },")
    lines.extend([
        "        };",
        "    }",
        "}",
        "",
    ])
    return "\n".join(lines)


def emit_manifest(spec: dict, digest: str) -> str:
    payload = {
        "version": spec["meta"].get("version", "1.0"),
        "generator": GENERATOR_VERSION,
        "spec_sha256": digest,
        "ops": [
            {
                "name": op["name"],
                "class": op["class"],
                "method": op["method"],
                "stability": op.get("stability", "stable"),
                "args": [{"name": a["name"], "type": a["type"],
                          "optional": "default" in a} for a in op.get("args") or []],
                "returns": op["returns"],
            }
            for op in spec["ops"]
        ],
        "hooks": [
            {
                "timing": hook["timing"],
                "csharp": hook["csharp"],
                "payload": [{"name": item["name"], "type": item["type"],
                             "optional": bool(item.get("optional"))} for item in hook["payload"]],
            }
            for hook in spec["hooks"]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write(path: str, content: str, check: bool) -> bool:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if check:
        if not os.path.isfile(path):
            print("缺失: %s" % os.path.relpath(path, ROOT))
            return False
        existing = open(path, "r", encoding="utf-8").read()
        if existing != content:
            print("过期: %s（重新运行 tools/gen_contract.py）" % os.path.relpath(path, ROOT))
            return False
        return True
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 A 层契约产物")
    parser.add_argument("--check", action="store_true", help="只校验产物是否最新")
    args = parser.parse_args(argv)

    started = time.time()
    spec = load_specs()
    raw = b""
    for name in sorted(os.listdir(SPEC_DIR)):
        if name.endswith(".toml"):
            raw += open(os.path.join(SPEC_DIR, name), "rb").read()
    digest = hashlib.sha256(raw).hexdigest()[:16]

    outputs = [
        (OUT_OPS, emit_ops(spec)),
        (OUT_HOOKS, emit_hooks(spec)),
        (OUT_CS, emit_csharp(spec)),
        (OUT_CS_REGISTRY, emit_csharp_registry(spec)),
        (OUT_CS_PAYLOAD, emit_csharp_payload(spec)),
        (OUT_MANIFEST, emit_manifest(spec, digest)),
    ]
    ok = True
    for path, content in outputs:
        ok = write(path, content, args.check) and ok

    print("%s %d 个 op / %d 个 hook / %.2fs（spec %s）"
          % ("校验" if args.check else "生成", len(spec["ops"]), len(spec["hooks"]),
             time.time() - started, digest))
    if args.check and not ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
