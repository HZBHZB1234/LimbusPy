#!/usr/bin/env python
"""Build the two LimbusPy distributions.

    python tools/pack.py --out dist --variant both
    python tools/pack.py --out dist --variant bundled --python-embed C:\\python-3.12-embed

The kernel has one layout contract (``kernel/LimbusPy.Bridge/Config/BridgeSettings.cs``):

    <plugin dir>/
      LimbusPy.Bridge*.dll        kernel assemblies
    LimbusPy.Ops.dll            A-layer (curated) ops
      python/                     PYTHONPATH root; a bundled interpreter lives here as python.exe
      mods/                       user mods
      .state/                     hot-reload state (created on first run)

Two variants differ only in whether ``python/python.exe`` exists:

* **system**  - relies on a Python on PATH (or ``python.Executable`` in the config);
* **bundled** - ships a CPython embeddable so the user needs nothing installed.

``--variant bundled`` needs ``--python-embed <dir>`` pointing at an extracted embeddable
distribution; without it the layout is staged and marked ``bundled: incomplete`` in the manifest
instead of silently producing a package that cannot run.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Optional

KIT_VERSION = "0.1.0"
PACKAGE = "limbuspy"
KERNEL_PROJECTS = (
    ("kernel/LimbusPy.Bridge/LimbusPy.Bridge.csproj", "LimbusPy.Bridge.dll"),
    ("kernel/LimbusPy.Bridge.Core/LimbusPy.Bridge.Core.csproj", "LimbusPy.Bridge.Core.dll"),
    ("kernel/LimbusPy.Bridge.Harmony/LimbusPy.Bridge.Harmony.csproj", "LimbusPy.Bridge.Harmony.dll"),
    # A 层（封装 API）：独立程序集，缺失/加载失败时内核自动降级到 B 层反射
    ("kernel/LimbusPy.Ops/LimbusPy.Ops.csproj", "LimbusPy.Ops.dll"),
)
#: Files BepInEx must not load twice; the plugin references these from BepInEx/core.
EXTERNAL_ASSEMBLIES = {"0Harmony.dll", "MonoMod.RuntimeDetour.dll", "MonoMod.Utils.dll",
                       "MonoMod.Backports.dll", "MonoMod.ILHelpers.dll", "Mono.Cecil.dll"}

README = """# LimbusPy ({variant} build)

1. Copy this folder to `BepInEx/plugins/LimbusPy/`.
2. Start the game once: the kernel writes `BepInEx/config/LimbusPy.Bridge.cfg` and starts Python.
3. Put mods into `mods/<name>/__init__.py`.
4. Check the install from a terminal:

   ```
   {python_hint}
   ```

Sample mod: `mods/_samples/sinking_tide/` (a complete, tested example - hooks, snapshot, reflection,
Harmony patch, sync lane, statics). Folders starting with `_` are ignored by the loader, so it stays
inert until you rename it to `mods/sinking_tide/`.

Python mode: **{variant}** - {python_mode}
"""


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_kernel(root: str, log) -> None:
    for project, _dll in KERNEL_PROJECTS:
        path = os.path.join(root, project)
        log("building %s" % os.path.relpath(path, root))
        result = subprocess.run(["dotnet", "build", "-v", "q", "--nologo", path],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit("build failed: %s\n%s%s" % (path, result.stdout, result.stderr))


def kernel_output(root: str, project: str = "kernel/LimbusPy.Bridge") -> str:
    """每个 csproj 有自己的 bin 目录；A 层（LimbusPy.Ops）刻意不引用内核，单独构建。"""
    name = os.path.basename(os.path.dirname(project))
    return os.path.join(root, "kernel", name, "bin", "Debug", "net6.0")


def copy_package(root: str, layout: str) -> int:
    source = os.path.join(root, "python", PACKAGE)
    target = os.path.join(layout, "python", PACKAGE)
    os.makedirs(target, exist_ok=True)
    count = 0
    for entry in os.listdir(source):
        if entry == "__pycache__" or entry == "_gen":
            continue
        src = os.path.join(source, entry)
        dst = os.path.join(target, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            count += sum(len(files) for _r, _d, files in os.walk(dst))
        else:
            shutil.copy2(src, dst)
            count += 1
    return count


def copy_generated_index(root: str, layout: str) -> Optional[str]:
    source = os.path.join(root, "python", PACKAGE, "_gen")
    if not os.path.isdir(source):
        return None
    target = os.path.join(layout, "python", PACKAGE, "_gen")
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("methods.json"))  # 34 MB: generated, not shipped
    builds = [name for name in os.listdir(source)
              if os.path.isdir(os.path.join(source, name))]
    return builds[0] if builds else None


def stage_common(root: str, layout: str, log) -> Dict[str, object]:
    os.makedirs(layout, exist_ok=True)
    copied: List[str] = []
    for project, dll in KERNEL_PROJECTS:
        source = os.path.join(kernel_output(root, project), dll)
        if not os.path.isfile(source):
            # 兼容老布局：三个内核 dll 都输出到 Bridge 的 bin 下
            fallback = os.path.join(kernel_output(root), dll)
            if os.path.isfile(fallback):
                source = fallback
            else:
                raise SystemExit("missing kernel assembly: %s (build first)" % source)
        shutil.copy2(source, os.path.join(layout, dll))
        copied.append(dll)

    # 0Harmony/MonoMod come from BepInEx/core at runtime; shipping copies breaks Harmony's
    # assembly identity (see docs/03-architecture.md §8).
    for name in EXTERNAL_ASSEMBLIES:
        stray = os.path.join(layout, name)
        if os.path.isfile(stray):
            os.remove(stray)
            log("removed duplicate runtime assembly %s" % name)

    package_files = copy_package(root, layout)
    build = copy_generated_index(root, layout)

    os.makedirs(os.path.join(layout, "mods"), exist_ok=True)
    example = os.path.join(layout, "mods", "_example")
    os.makedirs(example, exist_ok=True)
    with io.open(os.path.join(example, "__init__.py"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "from limbuspy.mod import Mod, hook\n"
            "\n"
            "class Example(Mod):\n"
            "    version = \"1.0.0\"\n"
            "\n"
            "    @hook(\"OnSucceedAttack\")\n"
            "    def on_hit(self, ctx):\n"
            "        self.log(\"hook fired; target=%r\" % (ctx.target,))\n"
        )

    sample = copy_sample_mods(root, layout, log)

    return {"assemblies": copied, "package_files": package_files, "generated_build": build,
            "sample_mod": os.path.relpath(sample, layout) if sample else None}


def copy_sample_mods(root: str, layout: str, log) -> Optional[str]:
    """Ships the tested example mod as an inert sample.

    It lands under ``mods/_samples/`` because the loader ignores entries whose name starts with
    ``_``: the user gets something runnable to read and copy, but an install never changes game
    behaviour on first launch.
    """
    source = os.path.join(root, "python", "examples", "sinking_tide")
    if not os.path.isdir(source):
        return None
    target = os.path.join(layout, "mods", "_samples", "sinking_tide")
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    log("staged sample mod -> mods/_samples/sinking_tide (rename to activate)")
    return target


def stage_bundled_python(embed_dir: str, layout: str, log) -> bool:
    target = os.path.join(layout, "python")
    os.makedirs(target, exist_ok=True)
    if not embed_dir:
        log("no --python-embed given: bundled layout staged WITHOUT an interpreter (incomplete)")
        return False
    if not os.path.isdir(embed_dir):
        raise SystemExit("--python-embed is not a directory: %s" % embed_dir)
    if not os.path.isfile(os.path.join(embed_dir, "python.exe")):
        raise SystemExit("--python-embed has no python.exe: %s" % embed_dir)

    for entry in os.listdir(embed_dir):
        src = os.path.join(embed_dir, entry)
        dst = os.path.join(target, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    log("bundled interpreter staged from %s" % embed_dir)
    return True


def write_manifest(layout: str, variant: str, payload: dict, complete: bool) -> None:
    manifest = {
        "kit": "limbuspy",
        "kit_version": KIT_VERSION,
        "variant": variant,
        "complete": complete,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layout": "BepInEx/plugins/LimbusPy",
        **payload,
    }
    with io.open(os.path.join(layout, "manifest.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    readme_path = os.path.join(layout, "README.md")
    with io.open(readme_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(README.format(
            variant=variant,
            python_hint="python -m limbuspy doctor" if variant == "system"
                        else ".\\python\\python.exe -m limbuspy doctor",
            python_mode=("a CPython embeddable ships in ./python" if complete
                         else "**incomplete**: re-run with --python-embed <dir>"),
        ))


def pack(root: str, out: str, variant: str, embed_dir: Optional[str], build: bool, log) -> Dict[str, str]:
    if build:
        build_kernel(root, log)

    results: Dict[str, str] = {}
    variants = ["system", "bundled"] if variant == "both" else [variant]

    for name in variants:
        layout = os.path.join(out, name)
        if os.path.isdir(layout):
            shutil.rmtree(layout)
        os.makedirs(layout, exist_ok=True)
        log("staging %s -> %s" % (name, layout))
        payload = stage_common(root, layout, log)
        complete = True
        if name == "bundled":
            complete = stage_bundled_python(embed_dir, layout, log)
        write_manifest(layout, name, payload, complete)
        results[name] = layout
        total = sum(len(files) for _r, _d, files in os.walk(layout))
        log("  %s: %d files, %d %s" % (name, total,
                                        int(sum(os.path.getsize(os.path.join(r, f))
                                                for r, _d, fs in os.walk(layout) for f in fs) / 1024),
                                        "KB"))
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Package the LimbusPy distributions")
    parser.add_argument("--out", default=os.path.join(repo_root(), "dist"))
    parser.add_argument("--variant", choices=("system", "bundled", "both"), default="both")
    parser.add_argument("--python-embed", default=None,
                        help="extracted CPython embeddable directory for the bundled variant")
    parser.add_argument("--no-build", action="store_true", help="reuse existing kernel assemblies")
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        print("[pack] " + message)

    root = repo_root()
    if not os.path.isdir(os.path.join(root, "kernel")):
        raise SystemExit("run this from the LimbusPy checkout (kernel/ not found): %s" % root)

    results = pack(root, args.out, args.variant, args.python_embed, not args.no_build, log)
    log("done: " + ", ".join("%s=%s" % (k, v) for k, v in results.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
