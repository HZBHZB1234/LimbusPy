#!/usr/bin/env python
"""校验 A 层契约里声明的挂点签名，与游戏真实签名是否一致。

**为什么要这个工具**：仓库里有三份不同日期的静态产物，按方法名 grep 会核错。

    Il2CppDumper-release/Dump.cs           2026-10-23   签名可能已漂移
    Il2CppDumper-release/script.json       2026-10-29   同上
    il2csharp/out/limbus/full0/.../*.cs    2026-11-02   最新，带方法体（唯一真源）

而且同名方法跨类型重复很多（`OnSucceedAttack` 有四种签名共存），
「搜到第一条就当真」会把参数核到别的重载上。

所以本工具做三件事：

1. **先限定类型**：在 ``full0`` 里找到 ``class <Target>`` 的那一段，只在段内搜方法；
2. **逐字比对参数名**：把契约 ``params`` 与实际参数名逐个比较（不是看数量对得上就算过）；
3. **报告歧义**：同一类型里同名方法有多个且参数名表不同 → 明确指出，因为
   ``AccessTools.Method(type, name)`` 在这种情况下会选错。

用法::

    python tools/check_hook_bindings.py                 # 检查全部时点
    python tools/check_hook_bindings.py --timing DamageDealt
    python tools/check_hook_bindings.py --full0 <目录>   # 指定 il2csharp 导出目录

退出码：0 = 全部一致；1 = 有漂移/找不到（CI 门禁用）。
"""

from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IL2CSHARP = os.path.join(os.path.dirname(ROOT), "il2csharp", "out", "limbus")

#: 默认真源：最新全量导出（带方法体）。发现新版本时改这里或传 --full0。
DEFAULT_FULL0 = os.path.join(IL2CSHARP, "full0")


def load_specs() -> dict:
    """读 ops/spec/*.toml（与 gen_contract.py 同源，保证校验的是同一份契约）。"""
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        raise SystemExit("需要 Python 3.11+（tomllib）")

    spec_dir = os.path.join(ROOT, "ops", "spec")
    merged: dict = {"hooks": []}
    for name in sorted(os.listdir(spec_dir)):
        if not name.endswith(".toml"):
            continue
        with open(os.path.join(spec_dir, name), "rb") as handle:
            data = tomllib.load(handle)
        merged["hooks"].extend(data.get("hook") or [])
    return merged


def find_class_file(full0: str, class_name: str, index: dict | None = None) -> str | None:
    """在 full0 里找 ``<class_name>.cs``。

    优先按文件名精确匹配；找不到时返回 None。``index`` 是 ``{文件名: 路径}``，
    由 <see cref="build_index"/> 建一次后跨时点复用（目录里有 3.5 万个文件）。
    """
    if not os.path.isdir(full0):
        return None
    if index is None:
        index = build_index(full0)
    return index.get(class_name + ".cs")


def build_index(full0: str) -> dict:
    """``{文件名: 完整路径}``；同名文件取路径最短的那个（排序稳定）。"""
    index: dict = {}
    for base, _dirs, files in os.walk(full0):
        for name in files:
            if not name.endswith(".cs"):
                continue
            path = os.path.join(base, name)
            current = index.get(name)
            if current is None or len(path) < len(current):
                index[name] = path
    return index


#: 方法声明行前缀（简单锚定，无回溯风险 —— 大文件上不能用嵌套量词的正则）。
_DECL_PREFIX = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|sealed|new|"
    r"extern|unsafe|abstract|partial)\s+)+[\w\.<>\[\],\?\s]+\s*$"
)

_TEXT_CACHE: dict = {}


def _read(path: str) -> str:
    text = _TEXT_CACHE.get(path)
    if text is None:
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            text = ""
        _TEXT_CACHE[path] = text
    return text


def split_params(raw: str) -> list[str]:
    """``int a, bool b`` → ``["a", "b"]``（去掉类型/default，只留名字）。"""
    names: list[str] = []
    depth = 0
    current = ""
    for ch in raw:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        if ch == "," and depth == 0:
            names.append(current)
            current = ""
            continue
        current += ch
    if current.strip():
        names.append(current)

    result: list[str] = []
    for piece in names:
        piece = piece.split("=")[0].strip()
        if not piece:
            continue
        # 最后一个标识符就是参数名（类型里可能有空格与泛型）
        ident = re.findall(r"[A-Za-z_]\w*", piece)
        if ident:
            result.append(ident[-1])
    return result


def methods_of(path: str, name: str) -> list[list[str]]:
    """返回该文件里所有名为 ``name`` 的**声明**的参数名表（排除调用点）。

    纯线性扫描：按 ``name(`` 定位 → 向左看这一行是不是声明 → 向右配平括号取参数表。
    大文件（BattleUnitModel.cs 有 1.5 MB）上也能秒出。
    """
    text = _read(path)
    if not text:
        return []

    found: list[list[str]] = []
    needle = name + "("
    cursor = 0
    while True:
        index = text.find(needle, cursor)
        if index < 0:
            break
        cursor = index + len(needle)

        # 向左取本行前缀，判断是不是"方法声明"而不是"方法调用"
        line_start = text.rfind("\n", 0, index) + 1
        prefix = text[line_start:index]
        if not _DECL_PREFIX.match(prefix):
            continue

        # 向右配平括号，取参数表原文
        depth = 0
        end = -1
        for offset in range(index + len(name), min(len(text), index + 4000)):
            ch = text[offset]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = offset
                    break
        if end < 0:
            continue

        found.append(split_params(text[index + len(needle):end]))
    return found


def check_hook(hook: dict, full0: str, index: dict) -> list[str]:
    timing = hook["timing"]
    csharp = hook.get("csharp") or {}
    target = csharp.get("target") or ""
    method = csharp.get("method") or ""
    declared = hook.get("params") or []

    problems: list[str] = []
    if not target or not method:
        return ["%s: csharp.target / csharp.method 缺失" % timing]
    # 用 "params" in hook 而不是真值判断：零参数时点（OnZeroHp 这类）的 params 是空列表，
    # 空列表本身是合法的契约，缺键才是问题。
    if "params" not in hook:
        return ["%s: 没有声明 params（无法校验签名）" % timing]
    declared = hook["params"]

    path = find_class_file(full0, target, index)
    if path is None:
        return ["%s: 在 %s 里找不到 %s.cs" % (timing, os.path.relpath(full0, ROOT), target)]

    candidates = methods_of(path, method)
    if not candidates:
        return ["%s: %s 里没有名为 %s 的方法" % (timing, target, method)]

    matches = [c for c in candidates if c == list(declared)]
    if matches:
        return []

    # 有候选但参数名对不上 → 漂移；并说明是否存在重载歧义
    problems.append("%s: 签名漂移。契约 [%s]" % (timing, ", ".join(declared)))
    for index, candidate in enumerate(candidates):
        problems.append("    实际 #%d: [%s]" % (index, ", ".join(candidate)))

    if len(candidates) > 1:
        problems.append(
            "    该类型里 %s 有 %d 个同名重载，靠参数名表定位；若都对不上，"
            "安装期会拒绝安装（避免静默取错位）" % (method, len(candidates)))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="校验 A 层挂点签名与游戏真实签名是否一致")
    parser.add_argument("--timing", help="只检查某一个时点")
    parser.add_argument("--full0", default=DEFAULT_FULL0, help="il2csharp 全量导出目录")
    parser.add_argument("--quiet", action="store_true", help="只输出失败项")
    args = parser.parse_args(argv)

    hooks = load_specs()["hooks"]
    if args.timing:
        hooks = [h for h in hooks if h["timing"] == args.timing]
        if not hooks:
            print("没有时点 %r" % args.timing, file=sys.stderr)
            return 1

    if not os.path.isdir(args.full0):
        print("找不到真源目录：%s" % args.full0, file=sys.stderr)
        print("用 --full0 指定 il2csharp 的全量导出目录（带方法体的那个）。", file=sys.stderr)
        return 1

    failures = 0
    index = build_index(args.full0)
    for hook in hooks:
        problems = check_hook(hook, args.full0, index)
        if problems:
            failures += 1
            for line in problems:
                print("FAIL " + line)
        elif not args.quiet:
            print("PASS %s（%s.%s，%d 个参数）"
                  % (hook["timing"], hook["csharp"]["target"], hook["csharp"]["method"],
                     len(hook.get("params") or [])))

    total = len(hooks)
    print("\n%d/%d 个时点签名一致" % (total - failures, total))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
