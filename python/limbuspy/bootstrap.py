"""Entry point the kernel spawns (or a user runs to attach).

    python -m limbuspy.bootstrap --pipe limbuspy.12345 --mods ... --state ...

Design notes:
  * the kernel passes the pipe prefix through argv (and ``LIMBUSPY_PIPE_PREFIX``);
  * connecting is retried because the kernel may still be creating the server instances;
  * the process stays alive until the connection drops, then exits with a status the supervisor
    can act on.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback

from . import __version__
from .mod import ModRuntime
from .runtime import protocol
from .runtime.client import Bridge
from .runtime.errors import LimbusPyError, TransportError
from .runtime.transport import connect_pipe_pair, connect_tcp

DEFAULT_CONNECT_TIMEOUT = 30.0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="limbuspy.bootstrap", description="LimbusPy bridge client")
    parser.add_argument("--pipe", default=os.environ.get("LIMBUSPY_PIPE_PREFIX"),
                        help="named pipe prefix (limbuspy.<kernel pid>)")
    parser.add_argument("--tcp", default=None, help="host:port of a TCP endpoint instead of pipes")
    parser.add_argument("--mods", default=os.environ.get("LIMBUSPY_MODS_DIR"), help="mods directory")
    parser.add_argument("--state", default=os.environ.get("LIMBUSPY_STATE_DIR"), help="state directory")
    parser.add_argument("--stubs-build", default=os.environ.get("LIMBUSPY_STUBS_BUILD", ""),
                        help="game build hash the stubs were generated for")
    parser.add_argument("--frame-budget-ms", type=float, default=3.0,
                        help="time budget for hook execution per event batch")
    parser.add_argument("--no-watch", action="store_true", help="disable the hot-reload file watcher")
    parser.add_argument("--watch-interval", type=float, default=0.4)
    parser.add_argument("--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT)
    parser.add_argument("--once", action="store_true", help="connect, handshake, then exit (smoke test)")
    parser.add_argument("--version", action="store_true")
    return parser


def _log(message: str) -> None:
    print("[limbuspy] " + message, file=sys.stderr, flush=True)


def connect(args: argparse.Namespace) -> Bridge:
    deadline = time.monotonic() + max(0.1, args.connect_timeout)
    last_error: Exception = None

    while time.monotonic() < deadline:
        try:
            if args.tcp:
                host, _, port = args.tcp.partition(":")
                return Bridge.connect_tcp(host or "127.0.0.1", int(port or 0))
            if not args.pipe:
                raise SystemExit("no --pipe given and LIMBUSPY_PIPE_PREFIX is unset")
            return Bridge.connect_pipe(args.pipe, timeout=max(0.1, deadline - time.monotonic()))
        except (TransportError, OSError) as exc:
            last_error = exc
            time.sleep(0.1)

    raise SystemExit("could not connect to the LimbusPy kernel within %.0fs: %s"
                     % (args.connect_timeout, last_error))


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.version:
        print("limbuspy %s (proto %s)" % (__version__, protocol.PROTOCOL_VERSION))
        return 0

    if not args.mods:
        args.mods = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mods")
    if not args.state:
        args.state = os.path.join(os.getcwd(), ".state")

    os.makedirs(args.mods, exist_ok=True)
    os.makedirs(args.state, exist_ok=True)

    _log("limbuspy %s connecting to kernel (proto %s)" % (__version__, protocol.PROTOCOL_VERSION))
    bridge = connect(args)

    runtime = ModRuntime(
        bridge=bridge,
        mods_dir=args.mods,
        state_dir=args.state,
        logger=_log,
        frame_budget_ms=args.frame_budget_ms,
        stubs_build=args.stubs_build or "",
    )

    try:
        hello = runtime.start()
    except LimbusPyError as exc:
        _log("handshake failed: %s" % exc)
        bridge.close("handshake failed")
        return 3

    kernel = hello.get("kernel", "?")
    _log("connected to kernel %s (build %s)" % (kernel, hello.get("game_build") or "?"))

    if args.once:
        _log("smoke check complete; exiting because --once was given")
        runtime.stop()
        bridge.close("once")
        return 0

    if not args.no_watch:
        runtime.start_watching(interval=args.watch_interval)

    try:
        runtime.run_forever()
    except Exception:  # noqa: BLE001 - report and exit non-zero so the supervisor restarts us
        _log("fatal error:\n" + traceback.format_exc())
        return 4
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
