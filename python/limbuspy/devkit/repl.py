"""Interactive REPL over a live bridge.

Why a REPL matters for this project: the whole point of running scripts out of process is that you
can poke a *running* game. ``limbuspy repl --pipe limbuspy.<pid>`` gives you the bridge, a session
and the game façade preloaded, with the same reflection API the scripts use.

``run_script`` exists so the REPL is testable: the same code path executes a script non-interactively
(``--exec file`` / ``-c "..."``), which is also how CI smoke-tests a real kernel.
"""

from __future__ import annotations

import code
import io
import os
import sys
from contextlib import redirect_stdout
from typing import Any, Dict, List, Optional

from .. import __version__
from ..runtime import protocol
from ..runtime.client import Bridge
from ..runtime.errors import LimbusPyError

__all__ = ["Repl", "build_namespace", "main"]

BANNER = "LimbusPy REPL %s (proto %s) - names: bridge, session, game, gb, Modular, help()" % (
    __version__, protocol.PROTOCOL_VERSION,
)


def build_namespace(bridge: Optional[Bridge], mods_dir: Optional[str] = None) -> Dict[str, Any]:
    """The objects a REPL line can use."""
    namespace: Dict[str, Any] = {"__name__": "__limbuspy_repl__", "bridge": bridge}

    from ..game import Session, battle as game_battle, members, objects
    from ..modular import Modular
    from ..modular import dsl as modular_dsl

    session = Session(bridge) if bridge is not None else None
    namespace.update({
        "session": session,
        "game": objects,
        "gb": game_battle,
        "game_battle": game_battle,
        "members": members,
        "Modular": Modular,
        "modular_dsl": modular_dsl,
    })

    try:
        from .. import il2cpp
        namespace["il2cpp"] = il2cpp
    except Exception:  # noqa: BLE001
        pass

    def help_() -> None:
        print("bridge      - raw LPCP client (call/get/patch/request)")
        print("session     - limbuspy.game.Session (reflect.get/set/call, snapshot)")
        print("game / gb   - façade objects / battle helpers (gb.current(session))")
        print("Modular     - one-way Modular compat layer (Modular(session).run(...))")
        print("members     - verified IL2CPP member-name tables")
        print("il2cpp      - generated index queries (type_names/has_member)")
        print("")
        print("examples:")
        print("  b = gb.current(session); [(u.instance_id, u.hp) for u in b.units]")
        print("  session.call(b.unit(101), 'GetActivatedBuffModels')")
        print("  Modular(session).run('TIMING:WhenUse/buff(MainTarget,SINKING,1,1)')")

    namespace["help"] = help_
    return namespace


class Repl:
    """Executes REPL lines against a namespace (interactive or scripted)."""

    def __init__(self, bridge: Optional[Bridge] = None, mods_dir: Optional[str] = None,
                 namespace: Optional[Dict[str, Any]] = None) -> None:
        self.bridge = bridge
        self.namespace = namespace if namespace is not None else build_namespace(bridge, mods_dir)
        self.namespace.setdefault("__builtins__", __builtins__)

    # -- scripted ----------------------------------------------------------------------

    def run_script(self, source: str) -> str:
        """Runs ``source`` and returns whatever it printed (used by tests and CI smoke)."""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exec(compile(source, "<repl>", "exec"), self.namespace)  # noqa: S102 - the whole point
        return buffer.getvalue()

    def run_file(self, path: str) -> str:
        with io.open(path, "r", encoding="utf-8") as handle:
            return self.run_script(handle.read())

    # -- interactive -------------------------------------------------------------------

    def interact(self) -> None:
        console = code.InteractiveConsole(self.namespace)
        console.interact(banner=BANNER, exitmsg="")


def main(argv=None) -> int:
    """``limbuspy repl [--pipe P | --tcp H:P] [--exec FILE] [-c CODE]``."""
    import argparse

    parser = argparse.ArgumentParser(prog="limbuspy repl", description="interactive LimbusPy console")
    parser.add_argument("--pipe", default=os.environ.get("LIMBUSPY_PIPE_PREFIX"))
    parser.add_argument("--tcp", default=None)
    parser.add_argument("--mods", default=os.environ.get("LIMBUSPY_MODS_DIR"))
    parser.add_argument("--exec", dest="exec_file", default=None, help="run a file instead of reading stdin")
    parser.add_argument("-c", dest="code", default=None, help="run a snippet instead of reading stdin")
    parser.add_argument("--no-connect", action="store_true", help="start without a kernel (offline poking)")
    args = parser.parse_args(argv)

    bridge: Optional[Bridge] = None
    if not args.no_connect:
        try:
            if args.tcp:
                host, _, port = args.tcp.partition(":")
                bridge = Bridge.connect_tcp(host or "127.0.0.1", int(port or 0))
            elif args.pipe:
                bridge = Bridge.connect_pipe(args.pipe, timeout=5.0)
            else:
                print("no --pipe/--tcp given: continuing without a kernel (use --no-connect to silence)",
                      file=sys.stderr)
        except LimbusPyError as exc:
            print("could not connect: %s" % exc, file=sys.stderr)
            return 2

    repl = Repl(bridge, args.mods)
    try:
        if args.code is not None:
            sys.stdout.write(repl.run_script(args.code))
            return 0
        if args.exec_file:
            sys.stdout.write(repl.run_file(args.exec_file))
            return 0
        repl.interact()
        return 0
    finally:
        if bridge is not None:
            try:
                bridge.close("repl exit")
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
