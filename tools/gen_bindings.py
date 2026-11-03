#!/usr/bin/env python
"""由 Il2CppDumper 索引生成**类型化绑定类**（真代码，不是 ``__getattr__`` 伪属性）。

产物（默认写到包内稳定路径，随包分发）::

    python/limbuspy/api/bindings.py         # 真类：@property / def + 注解 + docstring
    python/limbuspy/api/bindings_names.py   # Python 名 → (kind, IL2CPP 名) 反查表

设计要点（详见 docs/05-typed-api.md）：

* 一个成员一行真代码，**静态检查器必须能完整推导**：不用 ``__getattr__``、不用字符串转发；
* 每个成员都有 docstring（结构化描述 + 可选手写 overlay），overlay 走
  ``tools/bindings_notes.json``；
* C# 重载用 ``typing.overload`` 展开，运行时由内核按参数打分选重载；
* 可选参数默认值从 ``dump.cs`` 抓（``keyword = 2123``），抓不到就不给默认值（宁缺勿错）；
* 泛型方法 / struct 返回值 / 协程返回值**不生成可调用体**，而是生成一个显式的
  ``UnsupportedReturn`` 说明——绝不假装能用。

用法::

    python tools/gen_bindings.py --index python/limbuspy/_gen/dc33561883ca/methods.json \\
        --dump ../../Il2CppDumper-release/dump.cs --scope tools/bindings_scope.txt

    python tools/gen_bindings.py --audit python/limbuspy/api/_generated.py
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(ROOT, "python", "limbuspy", "api", "bindings.py")
DEFAULT_NAMES = os.path.join(ROOT, "python", "limbuspy", "api", "bindings_names.py")
DEFAULT_NOTES = os.path.join(HERE, "bindings_notes.json")
DEFAULT_SCOPE = os.path.join(HERE, "bindings_scope.txt")

GENERATOR_VERSION = "0.1.0"

# --------------------------------------------------------------------------------------
# C# 类型 → Python
# --------------------------------------------------------------------------------------

SCALARS: Dict[str, str] = {
    "int": "int", "long": "int", "short": "int", "byte": "int", "sbyte": "int",
    "uint": "int", "ulong": "int", "ushort": "int",
    "float": "float", "double": "float", "decimal": "float",
    "bool": "bool", "string": "str", "char": "str",
    "object": "Any", "void": "None",
}

#: 集合族：统一映射到 list[...]（内核按数组过桥）。
LIST_FAMILIES = {
    "List", "IList", "IReadOnlyList", "IEnumerable", "ICollection", "IReadOnlyCollection",
    "HashSet", "ISet", "Queue", "Stack", "LinkedList", "SortedSet",
}
DICT_FAMILIES = {"Dictionary", "IDictionary", "IReadOnlyDictionary", "SortedDictionary",
                 "SortedList", "ConcurrentDictionary"}
#: 协程/异步：返回值不过桥。
ASYNC_FAMILIES = {"UniTask", "Task", "ValueTask", "IEnumerator", "Coroutine", "UniTaskVoid"}

GENERIC_RE = re.compile(r"^(?P<name>[\w.]+)\s*<(?P<args>.*)>$", re.S)


def split_generic_args(text: str) -> List[str]:
    parts, depth, current = [], 0, []
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


@dataclass
class PyType:
    """一个 C# 类型在 Python 侧的表达。"""

    annotation: str
    conv: str = "any"            # scalar|enum|object|list|dict|optional|any
    inner: Optional["PyType"] = None
    key: Optional["PyType"] = None
    note: str = ""
    enum_class: Optional[str] = None

    def enum_name(self) -> Optional[str]:
        return self.annotation if self.conv == "enum" else None


class TypeMapper:
    """把索引里的 C# 类型串映射成 :class:`PyType`。"""

    def __init__(self, index: Dict[str, dict], generated: Dict[str, str],
                 enum_names: Optional[set] = None, big_enums: Optional[set] = None) -> None:
        self.index = index
        self.generated = generated  # C# 类型名 → 生成类名
        self.enum_names = enum_names or set()
        self.used_enums: set = set()
        self.big_enums = big_enums or set()

    def kind_of(self, name: str) -> str:
        record = self.index.get(name)
        if record is None:
            record = self.index.get(name.rsplit(".", 1)[-1])
        return (record or {}).get("kind") or "unknown"

    def map(self, cs_type: str) -> PyType:
        text = (cs_type or "").strip()
        if not text:
            return PyType("Any")

        if text.endswith("[]"):
            inner = self.map(text[:-2])
            return PyType("List[%s]" % inner.annotation, "list", inner=inner, note=inner.note)

        if text in SCALARS:
            return PyType(SCALARS[text], "scalar")

        match = GENERIC_RE.match(text)
        if match:
            family = match.group("name").split(".")[-1]
            args = split_generic_args(match.group("args"))
            if family == "Nullable" and args:
                inner = self.map(args[0])
                return PyType("Optional[%s]" % inner.annotation, "optional", inner=inner, note=inner.note)
            if family in ASYNC_FAMILIES:
                return PyType("Any", "any", note="协程/异步返回值不过桥")
            if family in LIST_FAMILIES:
                inner = self.map(args[0]) if args else PyType("Any")
                return PyType("List[%s]" % inner.annotation, "list", inner=inner, note=inner.note)
            if family in DICT_FAMILIES and len(args) >= 2:
                key = self.map(args[0])
                value = self.map(args[1])
                return PyType("Dict[%s, %s]" % (key.annotation, value.annotation),
                              "dict", key=key, inner=value, note=value.note)
            return PyType("Any", "any", note="泛型类型未建模（%s）" % text)

        kind = self.kind_of(text)
        if kind == "enum":
            simple = text.split(".")[-1]
            if self.enum_names and simple not in self.enum_names:
                return PyType("int", "scalar", note="枚举 %s 未在 enums.py 中生成，按整数处理" % simple)
            self.used_enums.add(simple)
            if simple in self.big_enums:
                return PyType("Union[str, int]", "enum", enum_class="enums.%s" % simple,
                              note="大型枚举 %s：传成员名（str）或数值（常量类成员）" % simple)
            return PyType("enums.%s" % simple, "enum", enum_class="enums.%s" % simple,
                          note="枚举：可传成员名（str）或 IntEnum")
        if kind == "class":
            if text in self.generated:
                return PyType("Optional[%s]" % self.generated[text], "object")
            return PyType("Any", "any", note="未生成绑定（不在 scope 内）：返回原始引用")
        if kind in ("struct", "interface"):
            return PyType("Any", "any", note="值类型/接口：内核不过桥（只能观测原始 wire 值）")
        return PyType("Any", "any", note="未索引类型（%s）" % text)


# --------------------------------------------------------------------------------------
# 命名
# --------------------------------------------------------------------------------------

PY_KEYWORDS = set(sys.modules["keyword"].kwlist) | {
    "self", "session", "wire", "target", "handle", "data", "raw", "cls", "args", "kwargs",
}
NAME_RESERVED = {"select", "session", "wire", "target", "handle", "data", "raw"}


def to_snake(name: str) -> str:
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^0-9A-Za-z_]+", "_", text)
    text = text.strip("_")
    text = re.sub(r"_+", "_", text)
    return text.lower() or "unnamed"


class NameTable:
    """给一个类型分配 Python 名；冲突按 属性 > 字段 > 方法 的优先级让位。"""

    def __init__(self) -> None:
        self.entries: Dict[str, Dict[str, tuple]] = {}
        self.used: Dict[str, Dict[tuple, str]] = {}

    def assign(self, type_name: str, member: str, kind: str, argc: int = 0) -> str:
        base = to_snake(member)
        if base in NAME_RESERVED or base in PY_KEYWORDS:
            base += "_"
        bucket = self.used.setdefault(type_name, {})
        taken = set(self.entries.setdefault(type_name, {}))
        candidate = base
        if candidate in taken:
            # 重载：同名同 kind 视为一组（由 @overload 处理）。
            existing = self.entries[type_name][candidate]
            if existing[0] == kind and kind in ("method", "property", "field"):
                return candidate
            suffix = {"property": "_p", "field": "_f", "method": "_m"}.get(kind, "_x")
            candidate = base + suffix
            while candidate in taken:
                candidate += "_"
        self.entries[type_name][candidate] = (kind, member)
        bucket[(kind, member, argc)] = candidate
        return candidate


# --------------------------------------------------------------------------------------
# 默认值（从 dump.cs 抓；methods.json 里没有）
# --------------------------------------------------------------------------------------

DUMP_TYPE_DECL = re.compile(
    r"^\s*(?:public|internal|private|protected|sealed|abstract|static|readonly|unsafe|partial|\s)*"
    r"(?:class|struct|interface|enum)\s+(?P<name>[A-Za-z_<][\w.<>]*)"
)
DUMP_METHOD = re.compile(
    r"^\s*(?:public|internal|private|protected|static|virtual|override|abstract|sealed|new|\s)+"
    r"(?P<ret>[\w.<>\[\],\s?]+?)\s+(?P<name>[A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\((?P<params>.*)\)\s*\{"
)
DUMP_PARAM_DEFAULT = re.compile(r"=\s*(?P<value>[^,)]+)")
DUMP_PARAM = re.compile(
    r"^\s*(?P<type>[\w.<>\[\],?\s]+?)\s+(?P<name>[A-Za-z_]\w*)\s*(?:=\s*(?P<default>.+))?$")

TYPE_ALIASES = {"Boolean": "bool", "Int32": "int", "Int64": "long", "Single": "float",
                "Double": "double", "String": "string", "Byte": "byte"}


def normalize_cs_type(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text or "").replace("Boolean", "bool").replace("Int32", "int")
    for alias, actual in TYPE_ALIASES.items():
        cleaned = cleaned.replace(alias, actual)
    return cleaned


DUMP_ENUM_CONST = re.compile(
    r"^\s*public\s+const\s+(?P<enum>[\w.]+)\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<value>-?0x[0-9A-Fa-f]+|-?\d+)\s*;")


def parse_enums(dump_path: str, wanted: set) -> Dict[str, List[Tuple[str, int]]]:
    """从 dump.cs 抓枚举成员：``{EnumName: [(member, value), ...]}``（单遍流式）。"""
    found: Dict[str, Dict[str, int]] = {}
    if not dump_path or not os.path.isfile(dump_path) or not wanted:
        return {}
    with open(dump_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "public const" not in line:
                continue
            match = DUMP_ENUM_CONST.match(line)
            if not match:
                continue
            enum_name = match.group("enum").split(".")[-1]
            if enum_name not in wanted:
                continue
            raw = match.group("value")
            value = int(raw, 16) if "0x" in raw.lower() else int(raw)
            found.setdefault(enum_name, {})[match.group("name")] = value
    return {name: sorted(members.items(), key=lambda item: item[1]) for name, members in found.items()}


def emit_enum_module(path: str, enums: Dict[str, List[Tuple[str, int]]], build: str,
                     intenum_threshold: int = 0) -> int:
    """只生成绑定实际用到的枚举（几十个，不是全部 1091 个）。"""
    lines = [
        '"""绑定层专用的精简枚举 —— 由 ``tools/gen_bindings.py`` 生成，不要手改。',
        "",
        "只包含 ``limbuspy.api.bindings`` 引用到的枚举；全量枚举见 ``_gen/<build>/enums.py``。",
        "构建: %s" % build,
        '"""',
        "",
        "from enum import IntEnum",
        "",
        "",
    ]
    for name in sorted(enums):
        members = enums[name]
        if not members:
            continue
        big = intenum_threshold and len(members) > intenum_threshold
        lines.append("class %s:" % name if big else "class %s(IntEnum):" % name)
        if big:
            lines.append('    """%s（由 dump.cs 生成；%d 个成员，超过 IntEnum 阈值 %d，'
                         '以常量类生成以免启动开销）"""' % (name, len(members), intenum_threshold))
        else:
            lines.append('    """%s（由 dump.cs 生成）"""' % name)
        emitted = set()
        for member, value in members:
            py_member = member if member.isidentifier() and member not in PY_KEYWORDS else member + "_"
            if py_member in emitted:
                continue
            emitted.add(py_member)
            lines.append("    %s = %d" % (py_member, value))
        if big:
            lines.append("")
            lines.append("    @classmethod")
            lines.append("    def from_name(cls, name: str) -> int:")
            lines.append('        """按成员名取数值（常量类没有 Enum 的索引能力）。"""')
            lines.append("        return int(getattr(cls, name))")
        lines.append("")
        lines.append("")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
    return len(enums)


def parse_defaults(dump_path: str, wanted: set) -> Dict[tuple, List[tuple]]:
    """``{(type, method, argc): [((param types...), [default_or_None, ...]), ...]}``。

    只抓需要的类型，单遍流式。**带上参数类型**是为了避免把同名同参数个数的另一个重载的
    默认值错位贴过来（踩过：`GetActivatedBuffStack(a, b=False)` 被贴到了
    `GetActivatedBuffStack(category, exceptions)` 上）。
    """
    result: Dict[tuple, List[tuple]] = {}
    if not dump_path or not os.path.isfile(dump_path):
        return result
    current = ""
    with open(dump_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            decl = DUMP_TYPE_DECL.match(line)
            if decl:
                current = decl.group("name")
                continue
            if current not in wanted or "=" not in line:
                continue
            method = DUMP_METHOD.match(line)
            if not method:
                continue
            params = method.group("params")
            types: List[str] = []
            defaults: List[str] = []
            for piece in split_generic_args(params):
                parsed = DUMP_PARAM.match(piece)
                if parsed is None:
                    types.append("")
                    defaults.append(None)
                    continue
                types.append(normalize_cs_type(parsed.group("type")))
                defaults.append((parsed.group("default") or "").strip() or None)
            if any(default is not None for default in defaults):
                key = (current, method.group("name"), len(defaults))
                result.setdefault(key, []).append((tuple(types), defaults))
    return result


CS_LITERAL = {"false": "False", "true": "True", "null": "None", "default": "None", "0.0f": "0.0"}


def _trailing_defaults(defaults: Sequence[Optional[str]]) -> bool:
    """C# 的默认值必须是尾部连续的一段；否则说明抓错了重载。"""
    seen_default = False
    for value in defaults:
        if value is None:
            if seen_default:
                return False
        else:
            seen_default = True
    return seen_default


def _default_count(defaults: Sequence[Optional[str]]) -> int:
    return sum(1 for value in defaults if value is not None)
SIMPLE_ENUM_VALUE = re.compile(r"^\d+$")


def py_default(cs_value: Optional[str], annotation: str) -> Optional[str]:
    if cs_value is None:
        return None
    text = cs_value.strip()
    if text in CS_LITERAL:
        return CS_LITERAL[text]
    if re.fullmatch(r"-?\d+", text):
        return text
    if re.fullmatch(r"-?\d+\.\d+[fFdD]?", text):
        return text.rstrip("fFdD")
    if text.upper() in ("FALSE", "TRUE"):
        return text.capitalize()
    quoted = re.fullmatch(r'"(?P<body>.*)"', text)
    if quoted:
        return repr(quoted.group("body"))
    if SIMPLE_ENUM_VALUE.match(text):
        return text
    if "::" in text:  # Enum.Member
        return None
    if annotation.startswith("Optional[") or annotation == "Any":
        return "None"
    return None


# --------------------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------------------


class ClassGen:
    def __init__(self, name: str, cs_name: str, base: Optional[str], record: dict) -> None:
        self.name = name
        self.cs_name = cs_name
        self.base = base
        self.record = record
        self.members: List[tuple] = []  # (python_name, kind, il2cpp_name, payload)


def accessor_target(member: str) -> Optional[str]:
    for prefix in ("get_", "set_"):
        if member.startswith(prefix) and len(member) > len(prefix):
            return member[len(prefix):]
    return None


def build_classes(
    index: Dict[str, dict],
    scope: List[str],
    mapper: TypeMapper,
    names: NameTable,
    limit_members: int = 0,
) -> Dict[str, ClassGen]:
    classes: Dict[str, ClassGen] = {}
    # 先把 base 链补进 scope（只为拿成员，避免子类重复声明）
    ordered: List[str] = []
    seen = set()

    def visit(cs_name: str) -> None:
        if cs_name in seen or cs_name not in index:
            return
        seen.add(cs_name)
        for base_name in index[cs_name].get("bases") or []:
            visit(base_name.split("<")[0])
        ordered.append(cs_name)

    for cs_name in scope:
        visit(cs_name)

    for cs_name in ordered:
        record = index[cs_name]
        if (record.get("kind") or "class") != "class":
            continue
        simple = cs_name.split(".")[-1]
        base_cs = (record.get("bases") or [None])[0]
        base_name = None
        if base_cs:
            base_root = base_cs.split("<")[0]
            if base_root in index and (index[base_root].get("kind") == "class"):
                base_name = base_root.split(".")[-1]
        classes[cs_name] = ClassGen(simple, cs_name, base_name, record)

    # 属性/字段/方法 → Python 名
    for cs_name, generated in classes.items():
        record = generated.record
        properties = {prop.get("name") for prop in record.get("properties") or []}
        count = 0
        for prop in record.get("properties") or []:
            if prop.get("static"):
                continue
            py_name = names.assign(cs_name, prop["name"], "property")
            generated.members.append((py_name, "property", prop["name"], prop))
            count += 1
            if limit_members and count >= limit_members:
                break
        for field in record.get("fields") or []:
            if field.get("static") or field.get("name") in properties:
                continue
            py_name = names.assign(cs_name, field["name"], "field")
            generated.members.append((py_name, "field", field["name"], field))
            count += 1
            if limit_members and count >= limit_members:
                break
        for method in record.get("methods") or []:
            name = method.get("name") or ""
            if not name or name.startswith(".") or method.get("static"):
                continue
            target = accessor_target(name)
            if target is not None and target in properties:
                continue  # 属性访问器：由 @property 表达
            if name in ("Finalize", "GetHashCode", "Equals", "ToString", "GetType", "MemberwiseClone"):
                continue
            if "<" in name:  # 泛型方法：反射无法实例化
                continue
            py_name = names.assign(cs_name, name, "method", len(method.get("params") or []))
            generated.members.append((py_name, "method", name, method))
            count += 1
            if limit_members and count >= limit_members:
                break
    return classes


class Emitter:
    def __init__(self, mapper: TypeMapper, names: NameTable, defaults: Dict[tuple, List[str]],
                 notes: Dict[str, Dict[str, str]], build: str) -> None:
        self.mapper = mapper
        self.names = names
        self.defaults = defaults
        self.notes = notes
        self.build = build
        self.lines: List[str] = []

    # -- 工具 ---------------------------------------------------------------------------

    def note(self, cs_type: str, member: str) -> str:
        return (self.notes.get(cs_type) or {}).get(member) or ""

    @staticmethod
    def doc_block(indent: str, summary: str, details: Sequence[str]) -> List[str]:
        text = summary.strip() or "（无描述）"
        lines = [indent + '"""' + text]
        body = [line for line in details if line]
        if body:
            lines.append("")
            for line in body:
                lines.append(indent + line)
        lines.append(indent + '"""')
        return lines

    def params_of(self, method: dict) -> List[Tuple[str, PyType]]:
        out = []
        for param in method.get("params") or []:
            py_type = self.mapper.map(param.get("type") or "")
            py_name = to_snake(param.get("name") or "arg")
            if py_name in ("self", "cls") or py_name in PY_KEYWORDS:
                py_name += "_"
            out.append((py_name, py_type))
        return out

    def defaults_of(self, cs_type: str, method: dict) -> List[Optional[str]]:
        params = method.get("params") or []
        count = len(params)
        key = (cs_type, method["name"], count)
        candidates = self.defaults.get(key) or []
        wanted = tuple(normalize_cs_type(param.get("type") or "") for param in params)
        best: Optional[List[Optional[str]]] = None
        for types, defaults in candidates:
            if types != wanted:          # 参数类型必须逐个对上，否则是别的重载
                continue
            if not _trailing_defaults(defaults):
                continue
            if best is None or _default_count(defaults) > _default_count(best):
                best = defaults
        return list(best) if best else [None] * count

    @staticmethod
    def signature_text(method: dict) -> str:
        params = ", ".join("%s %s" % (p.get("type"), p.get("name")) for p in method.get("params") or [])
        return "%s %s(%s)" % (method.get("ret") or "void", method.get("name"), params)

    # -- 成员 ---------------------------------------------------------------------------

    def emit_property(self, cls: ClassGen, info: tuple) -> None:
        py_name, kind, cs_name, payload = info
        py_type = self.mapper.map(payload.get("type") or "")
        writable = bool(payload.get("set")) if kind == "property" else not payload.get("readonly")
        summary = self.note(cls.cs_name, cs_name)
        cn_kind = "属性" if kind == "property" else "字段"
        if not summary:
            summary = "%s（%s）" % (cs_name, cn_kind)
        details = [
            "IL2CPP: ``%s.%s``（%s，%s，%s）" % (
                cls.cs_name, cs_name, cn_kind, payload.get("type") or "?",
                "可读写" if writable else "只读"),
        ]
        if py_type.note:
            details.append("注意: " + py_type.note)
        details.append("读一次 = 一次反射往返（除非命中 snapshot/mirror）；批量请用 ``select()``。")
        self.lines.append("    @property")
        self.lines.append("    def %s(self) -> %s:" % (py_name, py_type.annotation))
        self.lines.extend(self.doc_block("        ", summary, details))
        self.lines.append("        return %s" % self.render_value("self._read(%r)" % cs_name, py_type))
        self.lines.append("")
        if writable:
            self.lines.append("    @%s.setter" % py_name)
            self.lines.append("    def %s(self, value: %s) -> None:" % (py_name, py_type.annotation))
            self.lines.extend(self.doc_block("        ", "写入 " + summary, details[1:]))
            self.lines.append("        self._write(%r, value)" % cs_name)
            self.lines.append("")

    def render_value(self, expr: str, py_type: PyType) -> str:
        """把 wire 值转成注解承诺的 Python 类型（显式、可静态推导）。"""
        if py_type.conv == "enum":
            enum_ref = py_type.enum_class or py_type.annotation
            if not enum_ref.startswith("enums."):
                enum_ref = "enums." + enum_ref
            return "self._as_enum(%s, %s)" % (expr, enum_ref)
        if py_type.conv == "object":
            target = py_type.annotation
            target = target[len("Optional["):-1] if target.startswith("Optional[") else target
            return "self._as(%s, %s)" % (expr, target)
        if py_type.conv == "list":
            inner = py_type.inner or PyType("Any")
            if inner.conv in ("object", "enum"):
                target = inner.annotation
                target = target[len("Optional["):-1] if target.startswith("Optional[") else target
                return "self._as_list(%s, %s)" % (expr, target)
            return "%s or []" % expr
        if py_type.conv == "dict":
            inner = py_type.inner or PyType("Any")
            if inner.conv in ("object", "enum"):
                target = inner.annotation
                target = target[len("Optional["):-1] if target.startswith("Optional[") else target
                return "self._as_dict(%s, %s)" % (expr, target)
            return "self._as_dict(%s)" % expr
        if py_type.conv == "optional" and py_type.inner is not None:
            return self.render_value(expr, py_type.inner)
        return expr

    def emit_method(self, cls: ClassGen, group: List[tuple]) -> None:
        py_name, _kind, cs_name, method = group[0]
        params = self.params_of(method)
        return_type = self.mapper.map(method.get("ret") or "void")
        summary = self.note(cls.cs_name, cs_name) or "%s（方法）" % cs_name
        details: List[str] = []
        if len(group) > 1:
            details.append("IL2CPP 重载（%d 个）:" % len(group))
            for _, _, _, variant in group:
                details.append("  - ``%s``" % self.signature_text(variant))
        else:
            details.append("IL2CPP: ``%s``" % self.signature_text(method))
        if return_type.note:
            details.append("注意: " + return_type.note)
        self.note_param_hints(details)

        if len(group) > 1:
            for _, _, _, variant in group:
                variant_params = self.params_of(variant)
                variant_defaults = self.defaults_of(cls.cs_name, variant)
                variant_return = self.mapper.map(variant.get("ret") or "void")
                self.lines.append("    @overload")
                self.lines.append("    def %s(%s) -> %s:" % (
                    py_name, self._render_params(variant_params, variant_defaults), variant_return.annotation))
                self.lines.append('        """%s"""' % summary.replace('"""', "'"))
                self.lines.append("        ...")
            impl_return = return_type.annotation if return_type.annotation != "None" else "Any"
            self.lines.append("    def %s(self, *args: Any) -> %s:" % (py_name, impl_return))
            self.lines.extend(self.doc_block("        ", summary, details + [
                "重载由内核按参数打分选择；参数不对会在运行期报 marshal 并附候选签名。",
            ]))
            self.lines.append("        result = self._invoke(%r, *args)" % cs_name)
            self.lines.append("        return %s" % self.render_value("result", return_type))
            self.lines.append("")
            return

        defaults = self.defaults_of(cls.cs_name, method)
        signature = self._render_params(params, defaults)
        self.lines.append("    def %s(%s) -> %s:" % (py_name, signature, return_type.annotation))
        self.lines.extend(self.doc_block("        ", summary, details))
        args = "".join(", " + name for name, _ in params)
        if (method.get("ret") or "void") == "void":
            self.lines.append("        self._invoke(%r%s)" % (cs_name, args))
        else:
            self.lines.append("        result = self._invoke(%r%s)" % (cs_name, args))
            self.lines.append("        return %s" % self.render_value("result", return_type))
        self.lines.append("")

    def note_param_hints(self, details: List[str]) -> None:
        """给"怎么传参"的类型提示（枚举传名、引用传绑定对象）。"""

    @staticmethod
    def _render_params(params, defaults) -> str:
        rendered = ["self"]
        for index, (name, py_type) in enumerate(params):
            default = defaults[index] if index < len(defaults) else None
            if default is not None:
                rendered.append("%s: %s = %s" % (name, py_type.annotation, default))
            else:
                rendered.append("%s: %s" % (name, py_type.annotation))
        return ", ".join(rendered)

    # -- 类型 ---------------------------------------------------------------------------

    def emit_class(self, cls: ClassGen) -> None:
        base = cls.base or "BoundObject"
        self.lines.append("class %s(%s):" % (cls.name, base))
        record = cls.record
        summary = self.note(cls.cs_name, "__doc__") or "%s —— 由 IL2CPP 索引生成的绑定。" % cls.name
        details = [
            "IL2CPP: ``%s%s``" % (cls.cs_name, (" : " + (record.get("bases") or [""])[0])
                                  if record.get("bases") else ""),
            "命名空间: ``%s``；程序集: ``%s``" % (record.get("namespace") or "(global)",
                                              record.get("image") or "?"),
            "本类声明成员: 属性 %d / 字段 %d / 方法 %d（生成后可用成员数见 ``_generated_names.py``）"
            % (len(record.get("properties") or []), len(record.get("fields") or []),
               len(record.get("methods") or [])),
            "读成员 = 一次反射往返；热点路径请用 ``select()``、snapshot 或 mirror。",
        ]
        self.lines.extend(self.doc_block("    ", summary, details))
        self.lines.append("    __il2cpp_type__ = %r" % cls.cs_name)
        self.lines.append("")

        # 属性 / 字段
        for info in cls.members:
            if info[1] in ("property", "field"):
                self.emit_property(cls, info)

        # 方法：按 (python 名, cs 名) 分组成重载族
        groups: Dict[Tuple[str, str], List[tuple]] = {}
        order: List[Tuple[str, str]] = []
        for info in cls.members:
            if info[1] != "method":
                continue
            key = (info[0], info[2])
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(info)
        for key in order:
            self.emit_method(cls, groups[key])

        if not any(info[1] in ("property", "field", "method") for info in cls.members):
            self.lines.append("    # 本类在这个 build 里没有可生成的实例成员。")
            self.lines.append("")
        return


# --------------------------------------------------------------------------------------
# 审核（无 mypy/pyright 时的等价证据）
# --------------------------------------------------------------------------------------


def audit(path: str) -> int:
    import importlib
    import typing

    path = os.path.abspath(path)
    parts = path.split(os.sep)
    module = None
    if "limbuspy" in parts:
        index = parts.index("limbuspy")
        root = os.sep.join(parts[:index]) or os.sep
        dotted = ".".join(parts[index:]).removesuffix(".py")
        if root not in sys.path:
            sys.path.insert(0, root)
        module = importlib.import_module(dotted)
    if module is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_limbuspy_bindings_audit", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

    source = open(path, "r", encoding="utf-8").read()
    tree = ast.parse(source)

    problems: List[str] = []
    classes = 0
    members = 0
    documented = 0

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        classes += 1
        if "__getattr__" in {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}:
            problems.append("%s 使用了 __getattr__（禁止：静态不可查）" % node.name)
        class_obj = getattr(module, node.name, None)
        if class_obj is None:
            problems.append("类 %s 无法导入" % node.name)
            continue
        if not (class_obj.__doc__ or "").strip():
            problems.append("%s：类缺少 docstring" % node.name)
        if not getattr(class_obj, "__il2cpp_type__", ""):
            problems.append("%s：缺少 __il2cpp_type__" % node.name)
        seen: Dict[str, ast.FunctionDef] = {}
        for child in node.body:
            if not isinstance(child, ast.FunctionDef):
                continue
            if child.name.startswith("__") and child.name.endswith("__"):
                continue
            if child.name in seen:
                # 同名多分支（property getter/setter、@overload 桩）：按一个成员审，只审第一支
                continue
            seen[child.name] = child
            members += 1
            document = ast.get_docstring(child)
            if not (document or "").strip():
                problems.append("%s.%s：缺少 docstring" % (node.name, child.name))
            else:
                documented += 1
            if child.returns is None:
                problems.append("%s.%s：缺少返回注解" % (node.name, child.name))
            arguments = [a.arg for a in child.args.posonlyargs + child.args.args if a.arg not in ("self", "cls")]
            annotated = {a.arg for a in child.args.posonlyargs + child.args.args if a.annotation is not None}
            for arg in arguments:
                if arg not in annotated:
                    problems.append("%s.%s：参数 %s 缺少注解" % (node.name, child.name, arg))
            target = getattr(class_obj, child.name, None)
            functions = []
            if isinstance(target, property):
                functions = [fn for fn in (target.fget, target.fset) if fn is not None]
            elif callable(target):
                functions = [target]
            for function in functions:
                try:
                    typing.get_type_hints(function)
                except Exception as exc:  # noqa: BLE001
                    problems.append("%s.%s：注解无法解析（%s）" % (node.name, child.name, exc))

    print("审计 %s" % path)
    print("  类: %d   成员: %d   有 docstring: %d (%.1f%%)"
          % (classes, members, documented, 100.0 * documented / max(1, members)))
    if problems:
        print("  问题 %d 条（前 20）:" % len(problems))
        for problem in problems[:20]:
            print("    -", problem)
        return 1
    print("  结论: 全部成员均有 docstring + 完整注解，注解可解析，未使用 __getattr__")
    return 0


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------


def collect_type_refs(classes: Dict[str, "ClassGen"], mapper: TypeMapper, names: "NameTable") -> None:
    """走一遍成员类型，让 mapper 记录"用到了哪些枚举"（不做任何代码生成）。"""
    for cls in classes.values():
        for _py_name, kind, _cs_name, payload in cls.members:
            if kind in ("property", "field"):
                mapper.map(payload.get("type") or "")
                continue
            mapper.map(payload.get("ret") or "void")
            for param in payload.get("params") or []:
                mapper.map(param.get("type") or "")


def load_enum_names(directory: str) -> set:
    """``_gen/<build>/enums.py`` 里生成了哪些枚举类。"""
    path = os.path.join(directory, "enums.py")
    if not os.path.isfile(path):
        return set()
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return {match.group(1) for match in re.finditer(r"^class (\w+)\(", handle.read(), re.M)}


def load_scope(path: str, explicit: Optional[List[str]], index: Dict[str, dict]) -> List[str]:
    names: List[str] = []
    if explicit:
        names.extend(explicit)
    if path and os.path.isfile(path):
        for line in open(path, "r", encoding="utf-8"):
            text = line.split("#", 1)[0].strip()
            if text:
                names.extend(part.strip() for part in text.split(",") if part.strip())
    resolved = []
    for name in names:
        if name in index:
            resolved.append(name)
        else:
            # 允许只写简单名
            matches = [key for key in index if key.split(".")[-1] == name]
            if len(matches) == 1:
                resolved.append(matches[0])
    seen, ordered = set(), []
    for name in resolved:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 LimbusPy 类型化绑定")
    parser.add_argument("--index", default=None, help="methods.json 路径（或 _gen/<build> 目录）")
    parser.add_argument("--dump", default=None, help="dump.cs（取可选参数默认值）")
    parser.add_argument("--scope", default=DEFAULT_SCOPE, help="要生成的类型清单")
    parser.add_argument("--types", default=None, help="逗号分隔的类型名（覆盖 --scope）")
    parser.add_argument("--notes", default=DEFAULT_NOTES, help="docstring overlay JSON")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--names-out", default=DEFAULT_NAMES)
    parser.add_argument("--enums-module", default="limbuspy._gen.dc33561883ca.enums")
    parser.add_argument("--limit-members", type=int, default=0, help="每个类最多生成多少成员（调试用）")
    parser.add_argument("--enum-intenum-threshold", type=int, default=256,
                        help="成员数超过该值的大枚举生成常量类而非 IntEnum（默认 0=全部 IntEnum）")
    parser.add_argument("--audit", default=None, help="审核已生成的模块，不生成")
    args = parser.parse_args(argv)

    if args.audit:
        return audit(args.audit)

    if not args.index:
        raise SystemExit("需要 --index（methods.json）")
    index_path = args.index
    if os.path.isdir(index_path):
        index_path = os.path.join(index_path, "methods.json")
    build = os.path.basename(os.path.dirname(os.path.abspath(index_path)))

    started = time.time()
    payload = json.load(open(index_path, "r", encoding="utf-8"))
    index: Dict[str, dict] = payload.get("types") or {}
    scope = load_scope(args.scope, args.types.split(",") if args.types else None, index)
    if not scope:
        raise SystemExit("scope 为空：没有匹配到任何类型")

    generated_map = {cs: cs.split(".")[-1] for cs in scope}

    # pass 1：只为"收集引用到的枚举"，不做字符串生成（便宜）
    names_probe = NameTable()
    classes_probe = build_classes(index, scope, None, names_probe, args.limit_members)
    probe_scope = {cs: cls.name for cs, cls in classes_probe.items()}
    probe = TypeMapper(index, probe_scope, None)
    collect_type_refs(classes_probe, probe, names_probe)

    parsed_enums: Dict[str, List[Tuple[str, int]]] = {}
    if args.dump and probe.used_enums:
        parsed_enums = parse_enums(args.dump, probe.used_enums)
    enum_names = set(parsed_enums)

    # pass 2：真生成（枚举名集合已确定，缺成员的枚举退化为 int 并写进 docstring）
    big_enums = {name for name, members in parsed_enums.items()
                 if args.enum_intenum_threshold and len(members) > args.enum_intenum_threshold}
    names = NameTable()
    mapper = TypeMapper(index, generated_map, enum_names if parsed_enums else None, big_enums)
    classes = build_classes(index, scope, mapper, names, args.limit_members)
    mapper.generated = {cs_name: cls.name for cs_name, cls in classes.items()}
    defaults = parse_defaults(args.dump, set(classes)) if args.dump else {}
    notes: Dict[str, Dict[str, str]] = {}
    if args.notes and os.path.isfile(args.notes):
        notes = json.load(open(args.notes, "r", encoding="utf-8"))

    emitter = Emitter(mapper, names, defaults, notes, build)
    emitter.lines.extend([
        '"""LimbusPy 类型化绑定 —— 由 ``tools/gen_bindings.py`` 生成，不要手改。',
        "",
        "构建: %s" % build,
        "生成器: %s" % GENERATOR_VERSION,
        "类型数: %d" % len(classes),
        "",
        "每个成员都有 docstring 与完整注解；未生成的成员请用 ``obj.raw.get/call``。",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any, Dict, List, Optional, Union, overload",
        "",
        "from .base import BoundObject",
        "from . import _bindings_enums as enums",
        "",
        "__build__ = %r" % build,
        "",
        "",
    ])

    written = set()
    for cs_name, cls in classes.items():
        if cls.name in written:
            continue
        written.add(cls.name)
        emitter.emit_class(cls)

    enum_module = os.path.join(os.path.dirname(os.path.abspath(args.out)), "_bindings_enums.py")
    if parsed_enums:
        written_enums = emit_enum_module(enum_module, parsed_enums, build, args.enum_intenum_threshold)
        print("  精简枚举: %d/%d 个枚举、%d 个成员（%s）"
              % (written_enums, len(probe.used_enums),
                 sum(len(v) for v in parsed_enums.values()),
                 os.path.relpath(enum_module, ROOT)))
    else:
        # 没给 dump.cs：退化成转发全量 enums（慢，但能跑）
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(enum_module, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                '"""绑定层枚举垫片（未生成精简版）：转发全量 enums。"""\n'
                "from .._gen.%s.enums import *  # noqa: F401,F403\n" % build
            )
        print("  精简枚举: 退化为转发 _gen/%s/enums.py（给 --dump 可生成精简版）" % build)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(emitter.lines).rstrip() + "\n")

    # 反查表：老 members.py / 语义层都用它
    name_lines = [
        '"""Python 名 → (kind, IL2CPP 名) 反查表 —— 由 ``tools/gen_bindings.py`` 生成。"""',
        "",
        "__build__ = %r" % build,
        "",
        "NAME_MAP = {",
    ]
    for cs_name in classes:
        entries = names.entries.get(cs_name) or {}
        name_lines.append("    %r: {" % cs_name)
        for py_name, (kind, member) in sorted(entries.items()):
            name_lines.append("        %r: (%r, %r)," % (py_name, kind, member))
        name_lines.append("    },")
    name_lines.append("}")
    name_lines.append("")
    with open(args.names_out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(name_lines))

    members = sum(len(cls.members) for cls in classes.values())
    print("生成 %s" % os.path.relpath(args.out, ROOT))
    print("  类型 %d / 成员 %d / %.1f KB / %.2fs"
          % (len(classes), members, os.path.getsize(args.out) / 1024.0, time.time() - started))
    print("  overlay 覆盖: %d 个字面说明" % sum(len(v) for v in notes.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
