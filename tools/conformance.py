#!/usr/bin/env python
"""LPCP cross-language conformance runner.

Verifies that the C# kernel core and the Python runtime agree byte-for-byte on encoding and
speak the same protocol on a real Windows named pipe - with no game involved.

Usage:
    python tools/conformance.py                # everything
    python tools/conformance.py --golden-only  # encoding table only (fast)
    python tools/conformance.py --no-build     # reuse the last harness build
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_DIR = os.path.join(ROOT, "python")
HARNESS_DIR = os.path.join(ROOT, "tests", "LimbusPy.Bridge.Tests")
HARNESS_DLL = os.path.join(HARNESS_DIR, "bin", "Debug", "net6.0", "LimbusPy.Bridge.Tests.dll")

sys.path.insert(0, PY_DIR)
sys.path.insert(0, os.path.join(PY_DIR, "tests"))

from limbuspy.runtime import codec, protocol  # noqa: E402
from limbuspy.runtime.client import Bridge  # noqa: E402
from limbuspy.runtime.errors import (  # noqa: E402
    BridgeError,
    MarshalError,
    NotFoundError,
    ScriptError,
    StaleHandleError,
    TimeoutError_,
)
from limbuspy.runtime.protocol import MsgType  # noqa: E402
from limbuspy.runtime.transport import connect_pipe_pair  # noqa: E402

import golden_table  # noqa: E402


# ---------------------------------------------------------------------------------------
# tiny test harness
# ---------------------------------------------------------------------------------------


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures = []

    def check(self, name, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self.failed += 1
            detail = "%s: %s" % (type(exc).__name__, exc)
            self.failures.append((name, detail, traceback.format_exc()))
            print("  FAIL %-46s %s" % (name, detail))
            return False
        self.passed += 1
        print("  ok   %s" % name)
        return True

    def eq(self, name, actual, expected):
        def run():
            if actual != expected:
                raise AssertionError("expected %r, got %r" % (expected, actual))

        return self.check(name, run)

    def expect_error(self, name, fn, expected_type, expected_code=None):
        def run():
            try:
                fn()
            except expected_type as exc:
                if expected_code is not None and getattr(exc, "code", None) != expected_code:
                    raise AssertionError(
                        "expected code %r, got %r (%s)" % (expected_code, getattr(exc, "code", None), exc)
                    )
                return
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(
                    "expected %s, got %s: %s" % (expected_type.__name__, type(exc).__name__, exc)
                )
            raise AssertionError("expected %s, nothing was raised" % expected_type.__name__)

        return self.check(name, run)

    def report(self):
        total = self.passed + self.failed
        print("\n%d/%d checks passed" % (self.passed, total))
        if self.failures:
            print("\nfailures:")
            for name, detail, tb in self.failures:
                print("- %s: %s" % (name, detail))
                if os.environ.get("LIMBUSPY_CONFORMANCE_TRACEBACK"):
                    print(tb)
        return 0 if self.failed == 0 else 1


# ---------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------


def dotnet() -> str:
    exe = shutil.which("dotnet")
    if not exe:
        raise SystemExit("dotnet not found on PATH; install the .NET SDK to run conformance")
    return exe


def build_harness(rebuild: bool) -> None:
    if not rebuild and os.path.exists(HARNESS_DLL):
        return
    print("building C# harness ...")
    proc = subprocess.run(
        [dotnet(), "build", "-v", "q", "--nologo", HARNESS_DIR],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit("harness build failed:\n" + proc.stdout + proc.stderr)


# ---------------------------------------------------------------------------------------
# golden (encoding identity)
# ---------------------------------------------------------------------------------------


def run_golden(checker: Checker) -> None:
    print("[golden] canonical msgpack identity")
    proc = subprocess.run([dotnet(), HARNESS_DLL, "golden"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("golden mode failed:\n" + proc.stdout + proc.stderr)

    chs = None
    for line in proc.stdout.splitlines():
        if line.startswith("GOLDEN "):
            chs = json.loads(line[len("GOLDEN ") :])
            break
    if chs is None:
        raise SystemExit("harness did not emit a GOLDEN line:\n" + proc.stdout)

    c_hex = chs["hex"]
    c_canonical = chs["canonical"]

    labels_py = {label for label, _ in golden_table.all_cases()}
    labels_cs = set(c_hex)
    checker.eq("golden: same label set as C#", sorted(labels_cs), sorted(labels_py))

    for label, value in golden_table.all_cases():
        if label not in c_hex:
            continue
        py_hex = codec.pack(value).hex()
        checker.eq("golden[%s]: decoded C# value" % label, codec.unpack(bytes.fromhex(c_hex[label])), value)
        if c_canonical.get(label, False):
            checker.eq("golden[%s]: bytes == python" % label, c_hex[label], py_hex)

    print("  (%d cases, %d byte-compared)" % (len(labels_py), sum(1 for v in c_canonical.values() if v)))


# ---------------------------------------------------------------------------------------
# live pipe
# ---------------------------------------------------------------------------------------


class HarnessProcess:
    def __init__(self, events: int = 2, sync_fn: str = "mymod.ping", extra_args=None) -> None:
        self.pipe = "limbuspy.conf.%d" % os.getpid()
        self.tmp = tempfile.mkdtemp(prefix="limbuspy-conf-")
        self.summary_path = os.path.join(self.tmp, "summary.json")
        argv = [
            dotnet(),
            HARNESS_DLL,
            "serve",
            "--pipe",
            self.pipe,
            "--events",
            str(events),
            "--sync",
            sync_fn,
            "--out",
            self.summary_path,
            "--max-seconds",
            "90",
        ]
        argv.extend(extra_args or [])
        self.proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stderr_lines = []
        self._stdout_lines = []
        self._threads = [
            threading.Thread(target=self._drain, args=(self.proc.stdout, self._stdout_lines), daemon=True),
            threading.Thread(target=self._drain, args=(self.proc.stderr, self._stderr_lines), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    @staticmethod
    def _drain(stream, sink) -> None:
        for line in stream:
            sink.append(line.rstrip())

    def wait_ready(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(l.startswith("PIPE ") for l in self._stdout_lines):
                return
            if self.proc.poll() is not None:
                raise RuntimeError("harness exited early:\n" + "\n".join(self._stderr_lines))
            time.sleep(0.02)
        raise RuntimeError("harness did not announce its pipe in %.1fs" % timeout)

    def summary(self, timeout: float = 20.0) -> dict:
        self.proc.wait(timeout=timeout)
        for thread in self._threads:
            thread.join(2.0)
        if os.path.exists(self.summary_path):
            with open(self.summary_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        raise RuntimeError("no summary file:\n" + "\n".join(self._stderr_lines[-20:]))

    def stderr_text(self) -> str:
        return "\n".join(self._stderr_lines)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()


def run_live(checker: Checker) -> None:
    print("[live] named-pipe round trip with the C# kernel core")
    harness = HarnessProcess()
    seen = {"events": [], "sync": []}

    def on_event_batch(frame):
        payload = frame.decode()
        seen["events"].append(payload)
        actions = [
            {"event": index, "effects": [{"op": "log", "msg": "event-%d" % index}]}
            for index, _ in enumerate(payload.get("events") or [])
        ]
        bridge.notify(MsgType.ACT, {"seq": payload.get("seq"), "actions": actions})

    def on_sync(payload):
        seen["sync"].append(payload)
        fn = payload.get("fn") or ""
        context = payload.get("ctx") or {}
        if fn == "mymod.blocked":
            # Make the kernel's blocking lane actually block so its nested pump has to run.
            time.sleep(0.03)
        # Harmony patch dispatch (see run_live's patch section).
        if fn == "mymod.patch_heal":
            return (context.get("result") or 0) + 1000
        if fn == "mymod.patch_skip":
            return {"skip": True}
        if fn == "mymod.patch_boom":
            raise RuntimeError("patch hook exploded on purpose")
        return {"echoed": fn, "timing": context.get("timing")}

    bridge = None
    try:
        harness.wait_ready()
        transport = connect_pipe_pair(harness.pipe, timeout=5.0)
        bridge = Bridge(transport, name="conformance")
        bridge.on(MsgType.EVENTBATCH, on_event_batch)
        bridge.on_request(MsgType.SYNCALL, on_sync)
        bridge.start()

        hello = bridge.handshake(mods=["conformance"], stubs_build="e6312d92")
        checker.eq("live: hello.kernel", hello.get("kernel"), "0.1.0-test")
        checker.eq("live: kernel caps include hot.reload", bridge.has_cap(protocol.Caps.HOT_RELOAD), True)

        # -- basic calls -------------------------------------------------------------
        checker.eq("live: echo", bridge.call("k", "echo", 42), 42)
        checker.eq("live: add", bridge.call("k", "add", 2, 3), 5)
        checker.eq("live: concat", bridge.call("k", "concat", "边狱", "公司"), "边狱公司")
        checker.eq(
            "live: nested",
            bridge.call("k", "nested"),
            {"ok": [1, {"deep": None}], "bin": b"\x00\x01", "f": 2.5},
        )
        checker.eq("live: bytes", bridge.call("k", "bytes"), b"\x00\x01\x02\xff")
        checker.eq("live: 60KiB string", len(bridge.call("k", "big", 60000)), 60000)
        # The LPCP subset caps str/bin at 64 KiB; bigger payloads need chunking (v1.1), so the
        # kernel must fail loudly instead of truncating.
        checker.expect_error("live: >64KiB string -> marshal",
                             lambda: bridge.call("k", "big", 70000), MarshalError,
                             protocol.ErrorCode.MARSHAL)

        # -- error mapping -----------------------------------------------------------
        checker.expect_error("live: unknown method -> notfound", lambda: bridge.call("k", "nope"),
                             NotFoundError, protocol.ErrorCode.NOT_FOUND)
        checker.expect_error("live: script throw -> script", lambda: bridge.call("k", "boom"),
                             ScriptError, protocol.ErrorCode.SCRIPT)
        checker.expect_error("live: internal throw -> internal", lambda: bridge.call("k", "internal_boom"),
                             BridgeError, protocol.ErrorCode.INTERNAL)
        checker.expect_error("live: unencodable value -> marshal",
                             lambda: bridge.call("k", "marshal_bad"), MarshalError,
                             protocol.ErrorCode.MARSHAL)

        # -- timeout / orphan ---------------------------------------------------------
        checker.expect_error("live: slow reply times out",
                             lambda: bridge.call("k", "slow", 120, timeout=0.02), TimeoutError_)
        time.sleep(0.25)
        checker.eq("live: calls still work after a timeout", bridge.call("k", "echo", "alive"), "alive")

        # -- handler table / arena ----------------------------------------------------
        scope = bridge.call("k", "handles.scope.begin")
        handle = bridge.call("k", "handles.alloc", "A")
        checker.eq("live: handle tag", bridge.call("k", "handles.tag", handle), "A")
        checker.eq("live: handle describe kind", bridge.call("k", "handles.describe", handle)["kind"], "obj")
        checker.eq("live: handle pin", bridge.call("k", "handles.pin", handle), True)
        arena_only = bridge.call("k", "handles.alloc", "B")
        freed = bridge.call("k", "handles.scope.end", scope)
        checker.eq("live: scope end frees non-pinned only", freed, 1)
        checker.eq("live: pinned handle survives the arena", bridge.call("k", "handles.tag", handle), "A")
        checker.expect_error("live: arena handle is stale", lambda: bridge.call("k", "handles.tag", arena_only),
                             StaleHandleError, protocol.ErrorCode.STALE_HANDLE)
        checker.eq("live: release pinned", bridge.call("k", "handles.release", handle), True)
        checker.expect_error("live: released handle is stale", lambda: bridge.call("k", "handles.tag", handle),
                             StaleHandleError, protocol.ErrorCode.STALE_HANDLE)

        # -- reflection layer ---------------------------------------------------------
        unit = bridge.call("k", "reflect.new_unit", "Sinner", 40)
        checker.eq("reflect: new unit is a typed ref", (unit.get("t"), isinstance(unit.get("h"), int)), ("unit", True))
        checker.eq("reflect: get property", bridge.call("k", "reflect.get", unit, "Hp"), 40)
        checker.eq("reflect: set property returns new value", bridge.call("k", "reflect.set", unit, "Hp", 30), 30)
        checker.eq("reflect: invoke converts return", bridge.call("k", "reflect.invoke", unit, "Heal", [5]), 35)
        checker.eq("reflect: overload by arity (capped)", bridge.call("k", "reflect.invoke", unit, "Heal", [20, False]), 40)
        checker.eq("reflect: overload by arity (overheal)", bridge.call("k", "reflect.invoke", unit, "Heal", [20, True]), 60)
        checker.eq("reflect: string property", bridge.call("k", "reflect.set", unit, "Name", "Faust"), "Faust")
        checker.eq("reflect: double field", bridge.call("k", "reflect.set", unit, "Speed", 7.5), 7.5)
        checker.eq("reflect: enum set by name", bridge.call("k", "reflect.set", unit, "Faction", "ENEMY"), "ENEMY")
        checker.eq("reflect: read-only property", bridge.call("k", "reflect.get", unit, "IsAlive"), True)
        checker.eq("reflect: method call", bridge.call("k", "reflect.invoke", unit, "Describe"),
                   "Faust hp=60/40 buffs=0 faction=ENEMY")

        buff = bridge.call("k", "reflect.new_buff", "sinking", 2, 1)
        checker.eq("reflect: new buff kind", buff.get("t"), "buff")
        checker.eq("reflect: void overload with ref arg", bridge.call("k", "reflect.invoke", unit, "AddBuff", [buff]), None)
        created = bridge.call("k", "reflect.invoke", unit, "AddBuff", ["rupture", 3, 2])
        checker.eq("reflect: overload returning a ref", created.get("t"), "buff")
        checker.eq("reflect: LINQ-style computed member", bridge.call("k", "reflect.get", unit, "BuffCount"), 2)
        checker.eq("reflect: string arg", bridge.call("k", "reflect.invoke", unit, "CountByKeyword", ["RUPTURE"]), 1)
        checker.eq("reflect: int[] return", bridge.call("k", "reflect.invoke", unit, "HpSeries", [3]), [60, 61, 62])
        snapshot = bridge.call("k", "reflect.invoke", unit, "Snapshot")
        checker.eq("reflect: dictionary return", (snapshot["hp"], snapshot["buffs"], snapshot["faction"]),
                   (60, 2, "ENEMY"))

        checker.eq("reflect: static call via Ns.Type::Method", bridge.call(
            "k", "reflect.invoke", "", "LimbusPy.Bridge.Tests.Fixtures.TestStatics::Add", [2, 3]), 5)
        checker.eq("reflect: static double", bridge.call(
            "k", "reflect.invoke", "", "LimbusPy.Bridge.Tests.Fixtures.TestStatics::Half", [7]), 3.5)
        checker.eq("reflect: static field via type:",
                   bridge.call("k", "reflect.get", "type:LimbusPy.Bridge.Tests.Fixtures.TestStatics", "Version"),
                   "harness-1.0")

        checker.expect_error("reflect: unknown method -> notfound",
                             lambda: bridge.call("k", "reflect.invoke", unit, "Nope", []),
                             NotFoundError, protocol.ErrorCode.NOT_FOUND)
        checker.expect_error("reflect: unknown member -> notfound",
                             lambda: bridge.call("k", "reflect.get", unit, "Nope"),
                             NotFoundError, protocol.ErrorCode.NOT_FOUND)
        checker.expect_error("reflect: bad argument type -> marshal",
                             lambda: bridge.call("k", "reflect.invoke", unit, "Overloaded", [{"x": 1}]),
                             MarshalError, protocol.ErrorCode.MARSHAL)
        checker.expect_error("reflect: target threw -> internal",
                             lambda: bridge.call("k", "reflect.invoke", unit, "Explode", []),
                             BridgeError, protocol.ErrorCode.INTERNAL)

        described = bridge.call("k", "reflect.describe", unit)
        checker.eq("reflect: describe type", described.get("type"), "LimbusPy.Bridge.Tests.Fixtures.TestUnit")
        checker.eq("reflect: describe lists Hp", "Hp" in [m.get("n") for m in described.get("members") or []], True)
        types = bridge.call("k", "reflect.types", "TestUnit")
        checker.eq("reflect: type index", "LimbusPy.Bridge.Tests.Fixtures.TestUnit" in (types.get("types") or []), True)

        # -- harmony patch table ------------------------------------------------------
        target_type = "LimbusPy.Bridge.Tests.Fixtures.TestUnit"
        patch_unit = bridge.call("k", "reflect.new_unit", "Patchy", 50)

        binding = bridge.patch(target_type, "Heal", "mymod.patch_heal", kind="postfix", sig=["int"])
        checker.eq("patch: binding kind", binding.get("kind"), "postfix")
        checker.eq("patch: binding names the resolved method", "Heal" in str(binding.get("target")), True)

        healed = bridge.call("k", "patch.trigger", patch_unit, "Heal", [10])
        checker.eq("patch: postfix rewrote the result", healed.get("value"), 1060)
        checker.eq("patch: dispatch reached python", healed.get("ok"), True)

        listed = bridge.patches()
        checker.eq("patch: visible through doctor", any(p.get("id") == binding.get("id") for p in listed), True)

        boom = bridge.patch(target_type, "CountByKeyword", "mymod.patch_boom", sig=["string"])
        counted = bridge.call("k", "patch.trigger", patch_unit, "CountByKeyword", ["sinking"])
        checker.eq("patch: failing hook fails open", counted.get("value"), 0)
        checker.eq("patch: failing hook still returns", counted.get("ok"), True)
        bridge.unpatch(boom["id"])

        skip = bridge.patch(target_type, "Explode", "mymod.patch_skip", kind="prefix")
        exploded = bridge.call("k", "patch.trigger", patch_unit, "Explode", [])
        checker.eq("patch: prefix skip suppressed the exception", exploded.get("ok"), True)
        bridge.unpatch(skip["id"])

        bridge.unpatch(binding["id"])
        plain_unit = bridge.call("k", "reflect.new_unit", "Plain", 10)
        plain = bridge.call("k", "patch.trigger", plain_unit, "Heal", [5])
        checker.eq("patch: unpatch restores the original", plain.get("value"), 15)
        checker.eq("patch: table is empty again", len(bridge.patches()), 0)

        # -- concurrency / out-of-order results ---------------------------------------
        results = {}
        errors = []

        def worker(index):
            try:
                if index % 3 == 0:
                    results[index] = bridge.call("k", "slow", 5 + (index % 7), timeout=4.0)
                else:
                    results[index] = bridge.call("k", "add", index, 1000)
            except Exception as exc:  # noqa: BLE001
                errors.append((index, exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        def concurrency_ok():
            if errors:
                raise AssertionError("worker errors: %r" % (errors[:3],))
            for index in range(30):
                expected = "late" if index % 3 == 0 else index + 1000
                if results.get(index) != expected:
                    raise AssertionError("index %d: expected %r got %r" % (index, expected, results.get(index)))

        checker.check("live: 30 concurrent calls correlate", concurrency_ok)

        # -- kernel -> python scripted steps (run right after the handshake) ----------
        def wait_for_events():
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if seen["events"] and seen["sync"]:
                    return
                time.sleep(0.02)
            raise AssertionError("kernel never sent an event batch / sync call (%r, %r)"
                                 % (len(seen["events"]), len(seen["sync"])))

        checker.check("live: kernel sent EVENTBATCH + SYNCALL", wait_for_events)
        checker.eq("live: event batch size", len(seen["events"][0].get("events") or []), 2)
        checker.eq("live: sync call fn", seen["sync"][0].get("fn"), "mymod.ping")

        # -- blocking (nested-pump) lane ---------------------------------------------
        blocking = bridge.call("k", "sync.blocking", timeout=12.0)
        if not blocking.get("ok"):
            print("      blocking detail: payload=%r sync_seen=%d" % (blocking, len(seen["sync"])))
            print("      harness stderr tail:\n%s" % harness.stderr_text()[-1200:])
        checker.eq("live: blocking sync call completed", blocking.get("ok"), True)
        checker.eq("live: blocking sync call value echoed",
                   (blocking.get("value") or {}).get("echoed"), "mymod.blocked")
        checker.eq("live: nested pump ran while blocked", (blocking.get("pumps") or 0) > 0, True)

        # -- ping --------------------------------------------------------------------
        for _ in range(50):
            bridge.notify(MsgType.PING)
        checker.eq("live: pings answered", bridge.wait_for(MsgType.PONG, timeout=3.0).type, MsgType.PONG)

        # -- doctor (kernel self-report + limbuspy.devkit integration) --------------
        try:
            from limbuspy.devkit import check_kernel

            kernel_check = check_kernel(bridge, timeout=5.0)
            checker.eq("doctor: kernel check ok", kernel_check.status, "ok")
            checker.eq("doctor: kernel reports caps", bool(kernel_check.data.get("doctor", {}).get("caps")), True)
            doctor_payload = kernel_check.data.get("doctor") or {}
            checker.eq("doctor: proto matches", doctor_payload.get("proto"), protocol.PROTOCOL_VERSION)
            checker.eq("doctor: dispatcher section", isinstance(doctor_payload.get("dispatcher"), dict), True)
        except ImportError as exc:  # pragma: no cover - limbuspy must be importable here
            checker.check("doctor: import", lambda: (_ for _ in ()).throw(AssertionError(str(exc))))

        # -- limbuspy.game façade (against the game-shaped harness model) ------------
        from limbuspy.game import Session as GameSession
        from limbuspy.game import battle as game_battle

        game = GameSession(bridge)
        current = game_battle.current(game)

        checker.eq("game: round from snapshot", current.round, 3)
        checker.eq("game: wave from snapshot", current.wave, 1)
        checker.eq("game: unit count", len(current.units), 4)
        checker.eq("game: player count", len(current.player_units), 2)
        checker.eq("game: enemy count", len(current.enemy_units), 2)

        hero = current.unit(101)
        checker.eq("game: unit lookup by instance id", hero is not None, True)
        checker.eq("game: hp comes from the snapshot (no round trip)", hero.hp, 40)
        checker.eq("game: max_hp", hero.max_hp, 40)
        checker.eq("game: instance_id", hero.instance_id, 101)
        checker.eq("game: origin_id", hero.origin_id, 1001)
        checker.eq("game: hp_ratio", round(hero.hp_ratio, 3), 1.0)

        sinking = hero.buff("SINKING")
        checker.eq("game: buff lookup by keyword", sinking is not None, True)
        checker.eq("game: buff stack", sinking.stack, 3)
        checker.eq("game: buff turn", sinking.turn, 2)
        checker.eq("game: buff keyword", sinking.keyword, "SINKING")
        checker.eq("game: buff_stack helper", hero.buff_stack("CHARGE"), 5)
        checker.eq("game: missing buff returns 0", hero.buff_stack("NOPE"), 0)
        checker.eq("game: all buffs", len(hero.buffs), 2)

        checker.eq("game: manager.GetModel", current.manager().model(201).instance_id, 201)
        dead = [unit.instance_id for unit in current.units if unit.is_dead]
        checker.eq("game: dead unit flagged", dead, [202])

        from limbuspy.game import Skill

        raw_skills = bridge.call("k", "reflect.get", hero.wire, "Skills")
        first_skill = game.coerce(raw_skills[0], Skill) if raw_skills else None
        checker.eq("game: skill from reflection", first_skill.skill_id, 601101)
        checker.eq("game: skill name", first_skill.name, "Slash")
        checker.eq("game: skill coin count", first_skill.coin_count, 2)
        coins = first_skill.coins
        checker.eq("game: coins", len(coins), 2)
        checker.eq("game: coin head", coins[0].is_head(), True)
        checker.eq("game: coin tail", coins[1].is_tail(), True)
        checker.eq("game: coin scale", coins[1].scale, 4)
        checker.eq("game: coin by index", first_skill.coin(0).scale, 5)

        # -- counters were exercised -------------------------------------------------
        stats = bridge.stats()
        checker.eq("live: transport is a pipe", str(stats["transport"]).startswith("pipe:"), True)
        checker.eq("live: close flag is False while open", stats["closed"], False)

        bridge.close()
        bridge = None

        summary = harness.summary()
        checker.eq("harness: ok", summary.get("ok"), True)
        checker.eq("harness: no failures", summary.get("failures"), [])
        checker.eq("harness: saw 2 event actions", (summary.get("events") or {}).get("actions"), 2)
        checker.eq("harness: sync value echoed",
                   (summary.get("sync") or {}).get("value", {}).get("echoed"), "mymod.ping")
        checker.eq("harness: served >= 40 calls", (summary.get("calls") or 0) >= 40, True)
    finally:
        if bridge is not None:
            bridge.close()
        if not os.path.exists(harness.summary_path):
            harness.kill()
            print(harness.stderr_text()[-3000:])


def run_protocol_mismatch(checker: Checker) -> None:
    """Second harness run with a peer that lies about its protocol version."""

    print("[live] protocol mismatch is rejected fatally")
    harness = HarnessProcess(events=0, sync_fn="")
    bridge = None
    try:
        harness.wait_ready()
        transport = connect_pipe_pair(harness.pipe, timeout=5.0)
        bridge = Bridge(transport, name="conformance-mismatch")
        bridge.start()
        # Pretend to be a future client by reading HELLO and replying with proto 2.0.
        frame = bridge.wait_for(MsgType.HELLO, timeout=3.0)
        hello = frame.decode()
        bridge.notify(MsgType.READY, {
            "proto": "2.0",
            "py": "x",
            "mods": [],
            "stubs_build": "",
            "caps": [],
        })
        summary = harness.summary()
        checker.eq("mismatch: kernel exit code", harness.proc.returncode, 3)
        checker.eq("mismatch: kernel reported proto failure",
                   any("incompatible protocol" in f for f in summary.get("failures") or []), True)
        assert hello["proto"] == protocol.PROTOCOL_VERSION
    finally:
        if bridge is not None:
            bridge.close()
        if not os.path.exists(harness.summary_path):
            harness.kill()


def run_bootstrap_smoke(checker: Checker) -> None:
    """Runs the real entry point (``python -m limbuspy.bootstrap``) against the real C# kernel.

    This is the closest thing to an in-game run that can happen without the game: same protocol
    implementation, same handshake, same mod loading, same hook dispatch - only the objects behind
    the handles are ordinary C# instances instead of IL2CPP ones.
    """
    print("[spawn] real bootstrap process against the C# kernel")
    harness = HarnessProcess(events=2, sync_fn="smoke.answer", extra_args=["--exit-after-script"])
    tmp = tempfile.mkdtemp(prefix="limbuspy-spawn-")
    mods_dir = os.path.join(tmp, "mods")
    state_dir = os.path.join(tmp, "state")
    mod_dir = os.path.join(mods_dir, "smoke")
    os.makedirs(mod_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)

    with open(os.path.join(mod_dir, "__init__.py"), "w", encoding="utf-8") as handle:
        handle.write(
            "from limbuspy.mod import Mod, hook, expose\n"
            "\n"
            "class Smoke(Mod):\n"
            "    @hook('OnSucceedAttack')\n"
            "    def on_hit(self, ctx):\n"
            "        ctx.call('', 'echo', 'hook-ran')\n"
            "\n"
            "    @expose('smoke.answer')\n"
            "    def answer(self, ctx):\n"
            "        return 42\n"
        )

    env = dict(os.environ)
    env["PYTHONPATH"] = PY_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"

    log_lines = []
    bootstrap = None
    try:
        harness.wait_ready()
        bootstrap = subprocess.Popen(
            [
                sys.executable, "-u", "-m", "limbuspy.bootstrap",
                "--pipe", harness.pipe,
                "--mods", mods_dir,
                "--state", state_dir,
                "--no-watch",
                "--connect-timeout", "20",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=tmp,
        )
        drain = threading.Thread(target=lambda: [log_lines.append(line.rstrip()) for line in bootstrap.stdout],
                                 daemon=True)
        drain.start()

        summary = harness.summary(timeout=40)
        if not summary.get("ok"):
            print("      spawn harness failures: %r" % (summary.get("failures"),))
        checker.eq("spawn: kernel summary ok", summary.get("ok"), True)
        checker.eq("spawn: hook reached the kernel", summary.get("served_calls", (summary.get("calls") or 0)) >= 1, True)
        checker.eq("spawn: event batch handled", (summary.get("events") or {}).get("actions"), 2)
        checker.eq("spawn: exposed function value", (summary.get("sync") or {}).get("value"), 42)
        checker.eq("spawn: bootstrap exits cleanly when the kernel closes",
                   bootstrap.wait(timeout=20), 0)
    finally:
        if bootstrap is not None and bootstrap.poll() is None:
            bootstrap.terminate()
            try:
                bootstrap.wait(timeout=10)
            except subprocess.TimeoutExpired:
                bootstrap.kill()
        if not os.path.exists(harness.summary_path):
            harness.kill()
            print("\n".join(log_lines[-20:]))


def run_bootstrap_failure(checker: Checker) -> None:
    """A bootstrap with no kernel must exit non-zero instead of hanging forever."""
    print("[spawn] bootstrap without a kernel fails fast")
    tmp = tempfile.mkdtemp(prefix="limbuspy-nokernel-")
    env = dict(os.environ)
    env["PYTHONPATH"] = PY_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, "-u", "-m", "limbuspy.bootstrap",
         "--pipe", "limbuspy.does-not-exist.%d" % os.getpid(),
         "--mods", tmp, "--state", tmp, "--connect-timeout", "1.5"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    checker.eq("spawn: missing kernel -> exit 1", proc.returncode, 1)
    checker.eq("spawn: missing kernel reports why",
               "could not connect" in (proc.stderr or ""), True)


# ---------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden-only", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--skip-mismatch", action="store_true")
    parser.add_argument("--skip-spawn", action="store_true")
    args = parser.parse_args()

    if os.name != "nt":
        raise SystemExit("conformance requires Windows (named pipes)")

    build_harness(rebuild=not args.no_build)

    checker = Checker()
    run_golden(checker)
    if not args.golden_only:
        run_live(checker)
        if not args.skip_mismatch:
            run_protocol_mismatch(checker)
        if not args.skip_spawn:
            run_bootstrap_smoke(checker)
            run_bootstrap_failure(checker)
    return checker.report()


if __name__ == "__main__":
    sys.exit(main())



