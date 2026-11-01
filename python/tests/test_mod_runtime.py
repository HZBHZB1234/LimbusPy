"""Mod layer tests: discovery, dispatch, hot reload, state preservation, error isolation.

Driven by the mock kernel, so nothing here needs the game.
"""

from __future__ import annotations

import os
import sys
import textwrap
import threading
import time

import pytest

from conftest import make_transport_pair
from limbuspy._mock import MockKernel
from limbuspy.mod import ModRuntime, ModWatcher
from limbuspy.mod.decorators import config
from limbuspy.runtime import protocol
from limbuspy.runtime.client import Bridge
from limbuspy.runtime.protocol import MsgType


# --------------------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------------------


class Env:
    def __init__(self, tmp_path, mods, kernel_handlers=None, frame_budget_ms=50.0):
        self.tmp_path = tmp_path
        self.mods_dir = str(tmp_path / "mods")
        self.state_dir = str(tmp_path / "state")
        os.makedirs(self.mods_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)

        self.logs = []
        self.calls = []
        self._write_mods(mods)

        client_t, kernel_t = make_transport_pair()
        self.kernel = MockKernel(kernel_t, handlers=dict(kernel_handlers or {}, **self._default_handlers()))
        self.bridge = Bridge(client_t, name="mod-test").start()
        self.runtime = ModRuntime(
            self.bridge,
            self.mods_dir,
            self.state_dir,
            logger=self.logs.append,
            frame_budget_ms=frame_budget_ms,
        )
        self.hello = None
        self._thread = threading.Thread(target=self._kernel_start, daemon=True)

    def _default_handlers(self):
        return {
            "mark": lambda *args: self._mark(*args),
            "echo": lambda value: value,
            "boom": self._boom,
        }

    def _mark(self, *args):
        self.calls.append(args)
        return len(self.calls)

    def _boom(self):
        raise ValueError("kernel handler exploded")

    def _kernel_start(self):
        self.kernel.start(timeout=5.0)

    def _write_mods(self, mods):
        for name, source in mods.items():
            directory = os.path.join(self.mods_dir, name)
            os.makedirs(directory, exist_ok=True)
            with open(os.path.join(directory, "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(source))

    def write_mod(self, name, source):
        self._write_mods({name: source})

    def start(self):
        self._thread.start()
        self.hello = self.runtime.start()
        self._thread.join(5)
        return self

    def emit(self, events):
        return self.kernel.emit_event_batch(events)

    def sync(self, fn, ctx=None):
        return self.kernel.sync_call(fn, ctx or {})

    def close(self):
        try:
            self.runtime.stop()
        finally:
            self.bridge.close()
            self.kernel.close()


@pytest.fixture()
def env_factory(tmp_path):
    created = []

    def build(mods, handlers=None, frame_budget_ms=50.0):
        env = Env(tmp_path, mods, handlers, frame_budget_ms).start()
        created.append(env)
        return env

    yield build
    for env in created:
        env.close()


# --------------------------------------------------------------------------------------
# mods used by the tests
# --------------------------------------------------------------------------------------

SIMPLE_MOD = """
from limbuspy.mod import Mod, hook, expose

class Simple(Mod):
    @hook("OnSucceedAttack")
    def on_hit(self, ctx):
        ctx.call("", "mark", "hit")

    @expose("simple.double")
    def double(self, ctx):
        return ctx.event.get("value", 0) * 2
"""


# --------------------------------------------------------------------------------------
# discovery / dispatch
# --------------------------------------------------------------------------------------


def test_mod_is_discovered_and_labelled(env_factory):
    env = env_factory({"simple": SIMPLE_MOD})
    assert "simple" in env.runtime.mods
    assert env.runtime.mods["simple"].instance.name == "simple"
    assert env.runtime.registry.stats()["hooks"] == 1
    assert env.kernel.ready["mods"] == ["simple"]


def test_hook_receives_events(env_factory):
    env = env_factory({"simple": SIMPLE_MOD})
    actions = env.emit([
        {"timing": "OnSucceedAttack", "gen": 1},
        {"timing": "OnSucceedAttack", "gen": 1},
    ])
    assert len(actions) == 2
    assert env.calls == [("hit",), ("hit",)]
    assert env.runtime.counters["hooks_run"] == 2


def test_hook_timing_filter(env_factory):
    env = env_factory({"simple": SIMPLE_MOD})
    env.emit([{"timing": "WhenUse"}])
    assert env.calls == []


def test_hook_coin_filter(env_factory):
    mod = """
    from limbuspy.mod import Mod, hook

    class Heads(Mod):
        @hook("OnSucceedAttack", heads=True)
        def on_heads(self, ctx):
            ctx.call("", "mark", "heads")
    """
    env = env_factory({"heads": mod})
    env.emit([{"timing": "OnSucceedAttack", "head": False}])
    assert env.calls == []
    env.emit([{"timing": "OnSucceedAttack", "head": True}])
    assert env.calls == [("heads",)]


def test_expose_is_callable_from_kernel(env_factory):
    env = env_factory({"simple": SIMPLE_MOD})
    assert env.sync("simple.double", {"value": 21}) == 42


def test_unknown_exposed_function(env_factory):
    env = env_factory({"simple": SIMPLE_MOD})
    from limbuspy.runtime.errors import NotFoundError

    with pytest.raises(NotFoundError):
        env.sync("simple.nope")


def test_sync_hook_is_also_exposed(env_factory):
    mod = """
    from limbuspy.mod import Mod, hook

    class Sync(Mod):
        @hook("ChangeTakeDamage", sync=True, fallback=7)
        def bonus(self, ctx):
            return 99
    """
    env = env_factory({"syncmod": mod})
    assert env.sync("syncmod.bonus") == 99


def test_hook_exception_is_isolated(env_factory):
    mod = """
    from limbuspy.mod import Mod, hook

    class Broken(Mod):
        @hook("OnSucceedAttack")
        def blow_up(self, ctx):
            raise RuntimeError("boom")
    """
    env = env_factory({"broken": mod})
    actions = env.emit([{"timing": "OnSucceedAttack"}])
    assert len(actions) == 1
    assert env.runtime.counters["hooks_failed"] == 1
    assert any("boom" in line for line in env.logs)


def test_kernel_error_propagates_as_hook_failure(env_factory):
    mod = """
    from limbuspy.mod import Mod, hook

    class Calls(Mod):
        @hook("OnSucceedAttack")
        def call_boom(self, ctx):
            ctx.call("", "boom")
    """
    env = env_factory({"calls": mod})
    env.emit([{"timing": "OnSucceedAttack"}])
    assert env.runtime.counters["hooks_failed"] == 1


def test_class_inheritance_collects_base_hooks(env_factory):
    mod = """
    from limbuspy.mod import Mod, hook

    class BaseBehaviour(Mod):
        @hook("OnSucceedAttack")
        def from_base(self, ctx):
            ctx.call("", "mark", "base")

    class Derived(BaseBehaviour):
        @hook("OnSucceedAttack", priority=10)
        def from_derived(self, ctx):
            ctx.call("", "mark", "derived")
    """
    env = env_factory({"inherit": mod})
    env.emit([{"timing": "OnSucceedAttack"}])
    assert env.calls == [("derived",), ("base",)]


# --------------------------------------------------------------------------------------
# hot reload
# --------------------------------------------------------------------------------------

V1 = """
from limbuspy.mod import Mod, hook

class V(Mod):
    @hook("OnSucceedAttack")
    def on_hit(self, ctx):
        ctx.call("", "mark", "v1")
"""

V2 = """
from limbuspy.mod import Mod, hook

class V(Mod):
    @hook("OnSucceedAttack")
    def on_hit(self, ctx):
        ctx.call("", "mark", "v2")
"""


def test_hot_reload_replaces_behaviour(env_factory):
    env = env_factory({"hot": V1})
    env.emit([{"timing": "OnSucceedAttack"}])
    assert env.calls == [("v1",)]

    env.write_mod("hot", V2)
    result = env.runtime.reload(["hot"], reason="test")
    assert result.reloaded == ["hot"]
    assert result.generation["hot"] > 1

    env.emit([{"timing": "OnSucceedAttack"}])
    assert env.calls[-1] == ("v2",)
    assert len(env.calls) == 2


def test_hot_reload_preserves_state(env_factory):
    v1 = """
    from limbuspy.mod import Mod, hook

    class Counter(Mod):
        def on_load(self):
            self.state.setdefault("hits", 0)

        @hook("OnSucceedAttack")
        def count(self, ctx):
            self.state["hits"] += 1
            ctx.call("", "mark", self.state["hits"])
    """
    v2 = v1.replace('self.state["hits"] += 1', 'self.state["hits"] += 1  # v2')
    env = env_factory({"counter": v1})

    env.emit([{"timing": "OnSucceedAttack"}])
    env.emit([{"timing": "OnSucceedAttack"}])
    assert env.calls == [(1,), (2,)]

    env.write_mod("counter", v2)
    env.runtime.reload(["counter"], reason="test")
    env.emit([{"timing": "OnSucceedAttack"}])
    assert env.calls[-1] == (3,), "hook state must survive the reload"


def test_reload_failure_is_isolated(env_factory):
    broken = """
    from limbuspy.mod import Mod
    this is not valid python
    """
    env = env_factory({"simple": SIMPLE_MOD})
    env.write_mod("simple", broken)

    result = env.runtime.reload(["simple"], reason="test")
    assert "simple" in result.failed
    assert env.runtime.mods == {}
    # The runtime keeps working: no exception leaked, no hook left registered.
    env.emit([{"timing": "OnSucceedAttack"}])
    assert env.calls == []


def test_new_mod_appears_without_restart(env_factory):
    env = env_factory({"simple": SIMPLE_MOD})
    env.write_mod("late", V1)

    # rediscover by reloading everything
    env.runtime.order = []
    env.runtime.load_all()

    assert "late" in env.runtime.mods
    env.emit([{"timing": "OnSucceedAttack"}])
    assert ("v1",) in env.calls


def test_watcher_detects_change(env_factory):
    env = env_factory({"hot": V1})
    reports = []
    watcher = ModWatcher(env.mods_dir, reports.append, interval=0.05, debounce=0.0)
    watcher.poll_once()  # establishes the snapshot

    env.write_mod("hot", V2)
    os.utime(os.path.join(env.mods_dir, "hot", "__init__.py"), None)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not reports:
        watcher.poll_once()
        time.sleep(0.02)

    assert reports and reports[0] == ["hot"]


# --------------------------------------------------------------------------------------
# failure modes
# --------------------------------------------------------------------------------------


def test_missing_dependency_is_reported(env_factory):
    mod = """
    from limbuspy.mod import Mod
    class Needs(Mod):
        requires = ["nope>=1.0"]
    """
    env = env_factory({"needs": mod})
    assert env.runtime.mods == {}
    assert any("missing dependency" in line for line in env.logs)


def test_dependency_order(env_factory):
    base = """
    from limbuspy.mod import Mod
    class Base(Mod):
        pass
    """
    dependent = """
    from limbuspy.mod import Mod
    class Dependent(Mod):
        requires = ["base"]
    """
    env = env_factory({"dependent": dependent, "base": base})
    names = [manifest.name for manifest in env.runtime.order]
    assert names.index("base") < names.index("dependent")


def test_config_defaults_and_persistence(env_factory):
    mod = """
    from limbuspy.mod import Mod, hook, config

    class Configured(Mod):
        cfg = config(gain=2, label="hi")

        @hook("OnSucceedAttack")
        def on_hit(self, ctx):
            ctx.call("", "mark", self.cfg.gain, self.cfg.label)
    """
    env = env_factory({"configured": mod})
    env.emit([{"timing": "OnSucceedAttack"}])
    assert env.calls == [(2, "hi")]


def test_frame_budget_skips_remaining_hooks(env_factory):
    mod = """
    from limbuspy.mod import Mod, hook
    import time

    class Slow(Mod):
        @hook("OnSucceedAttack")
        def slow(self, ctx):
            time.sleep(0.02)
            ctx.call("", "mark", "slow")
    """
    env = env_factory({"slow": mod}, frame_budget_ms=1.0)
    env.emit([{"timing": "OnSucceedAttack"}, {"timing": "OnSucceedAttack"}])
    assert env.runtime.counters["over_budget_frames"] >= 1
    assert env.runtime.counters["hooks_skipped"] >= 1


def test_stats_are_serialisable(env_factory):
    env = env_factory({"simple": SIMPLE_MOD})
    stats = env.runtime.stats()
    assert stats["mods"] == ["simple"]
    assert stats["registry"]["hooks"] == 1
    assert isinstance(stats["counters"], dict)
    # must survive the wire (no exotic objects)
    from limbuspy.runtime import codec

    assert codec.unpack(codec.pack(stats))["mods"] == ["simple"]


