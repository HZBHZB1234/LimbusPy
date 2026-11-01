"""``limbuspy`` command line.

    python -m limbuspy doctor [--pipe P | --tcp H:P] [--mods DIR] [--build B] [--json]
    python -m limbuspy stubs --dump PATH [--out DIR] [--requirements PATH] [--stubs curated|all|off]
    python -m limbuspy mods [--mods DIR]
    python -m limbuspy index [PATTERN]           # search the IL2CPP index
    python -m limbuspy version

Design: every command is importable as a function too (``limbuspy.cli.doctor``), so tools and
tests do not have to shell out.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from .il2cpp import available_builds, current_build, default_index
from .runtime import protocol
from .runtime.client import Bridge
from .runtime.errors import LimbusPyError


def _repo_tools_dir() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "tools"))


def _connect(args) -> Optional[Bridge]:
    if not getattr(args, "pipe", None) and not getattr(args, "tcp", None):
        return None
    try:
        if getattr(args, "tcp", None):
            host, _, port = args.tcp.partition(":")
            return Bridge.connect_tcp(host or "127.0.0.1", int(port or 0))
        return Bridge.connect_pipe(args.pipe, timeout=3.0)
    except LimbusPyError as exc:
        print("could not connect: %s" % exc, file=sys.stderr)
        return None


# --------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------


def doctor(args) -> int:
    from .devkit import diagnose

    bridge = _connect(args)
    report = diagnose(bridge=bridge, build=args.build, mods_dir=args.mods)
    if bridge is not None:
        bridge.close("doctor done")

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print("LimbusPy doctor - %s (proto %s)" % (__version__, protocol.PROTOCOL_VERSION))
        print(report.to_text())
    return 0 if report.ok else 1


def stubs(args) -> int:
    tools = _repo_tools_dir()
    script = os.path.join(tools, "gen_limbuspy_stubs.py")
    if not os.path.isfile(script):
        print("generator not found: %s" % script, file=sys.stderr)
        return 2

    sys.path.insert(0, tools)
    import gen_limbuspy_stubs as generator  # noqa: PLC0415

    argv = ["--dump", args.dump, "--out", args.out, "--stubs", args.stubs]
    if args.build:
        argv += ["--build", args.build]
    if args.requirements:
        argv += ["--requirements", args.requirements]
    if args.all_images:
        argv += ["--all-images"]
    return generator.main(argv)


def mods(args) -> int:
    from .mod.loader import ManifestError, discover, resolve_order

    if not os.path.isdir(args.mods):
        print("mods directory does not exist: %s" % args.mods, file=sys.stderr)
        return 2
    try:
        manifests = discover(args.mods)
        ordered = resolve_order(manifests)
    except ManifestError as exc:
        print("broken: %s" % exc, file=sys.stderr)
        return 1

    if not ordered:
        print("no mods found in %s" % args.mods)
        return 0
    for manifest in ordered:
        requires = (" requires " + ", ".join(manifest.requires)) if manifest.requires else ""
        print("%-24s %-10s %s%s" % (manifest.name, manifest.version, manifest.directory, requires))
    return 0


def index(args) -> int:
    build = args.build or current_build()
    if not build:
        print("no generated index; run: python -m limbuspy stubs --dump <dump.cs> --out <dir>",
              file=sys.stderr)
        return 2

    type_index = default_index(build)
    if type_index is None:
        print("index for build %r is incomplete" % build, file=sys.stderr)
        return 2

    if args.pattern:
        names = type_index.type_names(args.pattern, limit=args.limit)
        for name in names:
            print(name)
        if not names:
            print("no type matches %r" % args.pattern, file=sys.stderr)
            return 1
        return 0

    print("build %s: %d types (%d builds available: %s)"
          % (build, len(type_index), len(available_builds()), ", ".join(available_builds())))
    return 0


def repl(args) -> int:
    """Interactive console over a (possibly running) kernel."""
    from .devkit import repl as repl_module

    argv = []
    for flag in ("pipe", "tcp", "code", "exec_file", "mods"):
        value = getattr(args, flag, None)
        if value:
            argv += ["--" + flag.replace("_", "-"), str(value)]
    if getattr(args, "no_connect", False):
        argv.append("--no-connect")
    return repl_module.main(argv)


def statics(args) -> int:
    """Extracts (or lists) the game's static data tables."""
    tools = _repo_tools_dir()
    script = os.path.join(tools, "extract_statics.py")
    if not os.path.isfile(script):
        print("extractor not found: %s" % script, file=sys.stderr)
        return 2
    sys.path.insert(0, tools)
    import extract_statics  # noqa: PLC0415

    argv = ["--out", args.out]
    if args.bundle:
        argv += ["--bundle", args.bundle]
    if args.catalog:
        argv += ["--catalog", args.catalog]
    return extract_statics.main(argv)


def pack(args) -> int:
    """Packages the distributable layouts (delegates to tools/pack.py)."""
    tools = _repo_tools_dir()
    script = os.path.join(tools, "pack.py")
    if not os.path.isfile(script):
        print("packer not found: %s" % script, file=sys.stderr)
        return 2

    sys.path.insert(0, tools)
    import pack as packer  # noqa: PLC0415

    argv = ["--out", args.out, "--variant", args.variant]
    if args.python_embed:
        argv += ["--python-embed", args.python_embed]
    if args.no_build:
        argv += ["--no-build"]
    return packer.main(argv)


def version(args) -> int:
    print("limbuspy %s (proto %s)" % (__version__, protocol.PROTOCOL_VERSION))
    build = current_build()
    print("generated index: %s" % (build or "none"))
    return 0


# --------------------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="limbuspy", description="LimbusPy developer CLI")
    sub = parser.add_subparsers(dest="command")

    doctor_parser = sub.add_parser("doctor", help="check stubs, python, mods and a running kernel")
    doctor_parser.add_argument("--pipe", default=os.environ.get("LIMBUSPY_PIPE_PREFIX"))
    doctor_parser.add_argument("--tcp", default=None)
    doctor_parser.add_argument("--mods", default=os.environ.get("LIMBUSPY_MODS_DIR"))
    doctor_parser.add_argument("--build", default=None)
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(func=doctor)

    stubs_parser = sub.add_parser("stubs", help="generate the IL2CPP index from dump.cs")
    stubs_parser.add_argument("--dump", required=True)
    stubs_parser.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "limbuspy", "_gen"))
    stubs_parser.add_argument("--requirements", default=None)
    stubs_parser.add_argument("--stubs", choices=("curated", "all", "off"), default="curated")
    stubs_parser.add_argument("--build", default=None)
    stubs_parser.add_argument("--all-images", action="store_true")
    stubs_parser.set_defaults(func=stubs)

    mods_parser = sub.add_parser("mods", help="list installed mods in dependency order")
    mods_parser.add_argument("--mods", default=os.environ.get("LIMBUSPY_MODS_DIR") or "mods")
    mods_parser.set_defaults(func=mods)

    index_parser = sub.add_parser("index", help="inspect/search the generated IL2CPP index")
    index_parser.add_argument("pattern", nargs="?", default=None)
    index_parser.add_argument("--build", default=None)
    index_parser.add_argument("--limit", type=int, default=50)
    index_parser.set_defaults(func=index)

    repl_parser = sub.add_parser("repl", help="interactive console over a running kernel")
    repl_parser.add_argument("--pipe", default=os.environ.get("LIMBUSPY_PIPE_PREFIX"))
    repl_parser.add_argument("--tcp", default=None)
    repl_parser.add_argument("--mods", default=os.environ.get("LIMBUSPY_MODS_DIR"))
    repl_parser.add_argument("-c", dest="code", default=None, help="run a snippet and exit")
    repl_parser.add_argument("--exec", dest="exec_file", default=None, help="run a file and exit")
    repl_parser.add_argument("--no-connect", action="store_true")
    repl_parser.set_defaults(func=repl)

    statics_parser = sub.add_parser("statics", help="extract the static data tables (needs UnityPy)")
    statics_parser.add_argument("--out", default=os.path.join("extracted", "statics"))
    statics_parser.add_argument("--bundle", default=None)
    statics_parser.add_argument("--catalog", default=None)
    statics_parser.set_defaults(func=statics)

    pack_parser = sub.add_parser("pack", help="build the system/bundled distribution layouts")
    pack_parser.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "dist"))
    pack_parser.add_argument("--variant", choices=("system", "bundled", "both"), default="both")
    pack_parser.add_argument("--python-embed", default=None)
    pack_parser.add_argument("--no-build", action="store_true")
    pack_parser.set_defaults(func=pack)

    version_parser = sub.add_parser("version", help="print versions")
    version_parser.set_defaults(func=version)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
