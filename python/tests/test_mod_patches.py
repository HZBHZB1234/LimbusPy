"""Mod-level Harmony patch bookkeeping (mock kernel, no game)."""

from __future__ import annotations

import pytest

from test_mod_runtime import Env, SIMPLE_MOD, env_factory  # noqa: F401 - fixtures

PATCHING_MOD = """
from limbuspy.mod import Mod, expose

class Patcher(Mod):
    def on_load(self):
        self.patch_id = self.patch(
            "LimbusPy.Bridge.Tests.Fixtures.TestUnit", "Heal", "on_heal", sig=["int"]
        )

    @expose("on_heal")
    def on_heal(self, ctx):
        return (ctx.event.get("result") or 0) + 1
"""


def test_mod_patch_registers_with_the_kernel(env_factory):
    env = env_factory({"patcher": PATCHING_MOD})
    kernel_patches = env.kernel.patches
    assert len(kernel_patches) == 1, kernel_patches

    (patch_id, spec), = kernel_patches.items()
    assert spec["owner"] == "LimbusPy.Bridge.Tests.Fixtures.TestUnit"
    assert spec["method"] == "Heal"
    assert spec["fn"] == "patcher.on_heal"
    assert spec["sig"] == ["int"]
    assert env.runtime.mods["patcher"].instance.patch_id == patch_id


def test_reload_unpatches_before_unloading(env_factory):
    env = env_factory({"patcher": PATCHING_MOD})
    assert len(env.kernel.patches) == 1

    result = env.runtime.reload(["patcher"], reason="test")
    assert result.reloaded == ["patcher"]

    # Exactly one live patch again: the old binding was removed, the new on_load installed one.
    assert len(env.kernel.patches) == 1
    (spec,) = env.kernel.patches.values()
    assert spec["fn"] == "patcher.on_heal"


def test_shutdown_removes_patches(env_factory):
    env = env_factory({"patcher": PATCHING_MOD})
    assert len(env.kernel.patches) == 1
    env.runtime.stop()
    assert env.kernel.patches == {}


def test_patch_failure_is_reported_but_does_not_break_loading(env_factory):
    mod = """
    from limbuspy.mod import Mod

    class Broken(Mod):
        def on_load(self):
            try:
                self.patch("", "", "x")
            except Exception as exc:
                self.state["error"] = type(exc).__name__
    """
    env = env_factory({"broken_patch": mod})
    assert "broken_patch" in env.runtime.mods
    assert env.runtime.mods["broken_patch"].instance.state.get("error") in ("MarshalError", "BridgeError")


def test_bare_function_name_is_namespaced(env_factory):
    mod = """
    from limbuspy.mod import Mod, expose

    class N(Mod):
        def on_load(self):
            self.patch("LimbusPy.Bridge.Tests.Fixtures.TestUnit", "Describe", "renamed")

        @expose("renamed")
        def handler(self, ctx):
            return "patched"
    """
    env = env_factory({"namer": mod})
    (spec,) = env.kernel.patches.values()
    assert spec["fn"] == "namer.renamed"
