"""`limbuspy.modular`: DSL builder + driving Modular through reflection (mock kernel)."""

from __future__ import annotations

import threading

import pytest

from conftest import make_transport_pair
from limbuspy._mock import MockKernel
from limbuspy.game import Session
from limbuspy.modular import Modular, dsl
from limbuspy.runtime.client import Bridge
from limbuspy.runtime.errors import NotFoundError

# --------------------------------------------------------------------------------------
# DSL builder (pure)
# --------------------------------------------------------------------------------------


def test_dsl_render_full_script():
    text = (
        dsl.script()
        .timing("OnSucceedAttack", heads=True)
        .value(0, "getdmg")
        .if_("getbuffcount", "Self", "SINKING")
        .buff("MainTarget", "SINKING", 3, 1)
        .render()
    )
    assert text == (
        "TIMING:OnSucceedAttack(Head)/VALUE_0(getdmg())/"
        "IF(getbuffcount(Self,SINKING))/buff(MainTarget,SINKING,3,1)"
    )


def test_dsl_filters():
    assert dsl.script().timing("WhenUse", tails=True).render() == "TIMING:WhenUse(Tail)"
    assert dsl.script().timing("WhenUse", crit=True).render() == "TIMING:WhenUse(Crit)"
    assert dsl.script().timing("WhenUse", crit=False).render() == "TIMING:WhenUse(NoCrit)"
    assert dsl.script().timing("OnGainBuff", keyword="SINKING").render() == "TIMING:OnGainBuff(SINKING)"
    assert dsl.script().timing("WhenUse").render() == "TIMING:WhenUse"


def test_dsl_batches():
    assert dsl.script().loop("AllEnemy").buff("MainTarget", "CHARGE", 1, 1).render() == (
        "LOOP:AllEnemy/buff(MainTarget,CHARGE,1,1)"
    )
    assert dsl.script().if_not("isdead", "Self").render() == "IFNOT(isdead(Self))"
    assert dsl.script().stop_if("gethp", "Self", 10).render() == "STOPIF(gethp(Self,10))"
    assert dsl.script().mobj(1, "unit", "MainTarget").render() == "MOBJ1(unit(MainTarget))"
    assert dsl.script().raw("CLEARVALUES").render() == "CLEARVALUES"


def test_dsl_argument_formatting():
    assert dsl.script().consequence("setdata", 1, True, False).render() == "setdata(1,1,0)"
    assert dsl.script().consequence("setlevel", 2.0).render() == "setlevel(2)"
    assert dsl.script().consequence("setlevel", 2.5).render() == "setlevel(2.5)"


# --------------------------------------------------------------------------------------
# driving Modular through reflection
# --------------------------------------------------------------------------------------


def _calls(mod) -> list:
    return mod._fake.calls


class FakeModular:
    """Emulates Modular's object shape and records every call the bridge sees."""

    def __init__(self, loaded: bool = True) -> None:
        self.loaded = loaded
        self.calls = []
        self.next_handle = 5000
        self.objects = {}

    # -- object helpers ----------------------------------------------------------------

    def _new(self, kind, members):
        handle = self.next_handle
        self.next_handle += 1
        self.objects[handle] = {"kind": kind, "members": dict(members)}
        return {"t": kind, "h": handle}

    def _entry(self, target):
        if not isinstance(target, dict):
            raise KeyError("bad target %r" % (target,))
        entry = self.objects.get(int(target.get("h") or 0))
        if entry is None:
            raise KeyError("stale target")
        return entry

    # -- kernel surface ----------------------------------------------------------------

    def handlers(self):
        return {
            "reflect.types": lambda pattern: ["ModularSkillScripts.MainClass"] if self.loaded else [],
            "reflect.get": self.get,
            "reflect.set": self.set,
            "reflect.invoke": self.invoke,
            "ModularSkillScripts.ModularSA::.ctor": self.ctor,
        }

    def ctor(self):
        self.calls.append(("ctor",))
        return self._new("sa", {"activationTiming": 0})

    def get(self, target, member):
        self.calls.append(("get", _name(target), member))
        if isinstance(target, str) and "MainClass" in target:
            if member == "timingDict":
                return {"WhenUse": 1, "OnSucceedAttack": 7, "RoundStart": -1} if self.loaded else {}
            if member == "acquirerDict":
                impl = self._new("acquirer", {"value": 3})
                return {"getbuffcount": impl} if self.loaded else {}
            if member == "consequenceDict":
                return {"buff": self._new("consequence", {}), "healhp": self._new("consequence", {})}
        entry = self._entry(target)
        value = entry["members"].get(member)
        if callable(value):
            return value()
        return value

    def set(self, target, member, value):
        self.calls.append(("set", _name(target), member, value))
        self._entry(target)["members"][member] = value
        return value

    def invoke(self, target, member, args):
        self.calls.append(("invoke", _name(target), member, list(args)))
        if member == "SetupModular":
            self._entry(target)["members"]["activationTiming"] = 7
            return None
        if member == "Enact":
            return "enacted"
        if member == "ExecuteAcquirer":
            return 3
        entry = self._entry(target) if isinstance(target, dict) else None
        if entry and isinstance(entry["members"].get(member), int):
            return entry["members"][member]
        raise KeyError("no member %s" % member)


def _name(target):
    if isinstance(target, dict):
        return "h:%d" % int(target.get("h") or 0)
    return str(target)


class Env:
    def __init__(self, loaded: bool = True):
        self.fake = FakeModular(loaded)
        client, kernel = make_transport_pair()
        self.kernel = MockKernel(kernel, handlers=self.fake.handlers())
        self.bridge = Bridge(client, name="modular-test").start()
        self._thread = threading.Thread(target=lambda: self.kernel.start(5.0), daemon=True)

    def start(self):
        self._thread.start()
        self.bridge.handshake(mods=[], stubs_build="")
        modular = Modular(Session(self.bridge))
        modular._fake = self.fake          # test-only: lets assertions inspect the call log
        return modular

    def close(self):
        self.bridge.close()
        self.kernel.close()


@pytest.fixture()
def modular():
    env = Env()
    try:
        yield env.start()
    finally:
        env.close()


def test_availability_and_catalogue(modular):
    assert modular.available is True
    assert modular.timing_id("WhenUse") == 1
    assert modular.timing_id("onsucceedattack") == 7  # case-insensitive
    assert modular.consequences() == ["buff", "healhp"]
    assert modular.acquirers() == ["getbuffcount"]
    described = modular.describe()
    assert described["available"] is True
    assert described["consequences"] == 2


def test_unknown_timing_raises(modular):
    with pytest.raises(NotFoundError):
        modular.timing_id("NotATiming")


def test_run_executes_the_dsl(modular):
    result = modular.run("TIMING:OnSucceedAttack(Head)/buff(MainTarget,SINKING,2,1)",
                         unit={"t": "unit", "h": 11})
    assert result == "enacted"

    kinds = [call[0] for call in modular.session and _calls(modular) or []]
    calls = _calls(modular)
    # ctor -> SetupModular -> read activationTiming -> pin it -> Enact
    assert calls[0][0] == "ctor"
    assert calls[1] == ("invoke", calls[1][1], "SetupModular",
                        ["TIMING:OnSucceedAttack(Head)/buff(MainTarget,SINKING,2,1)"])
    assert calls[2] == ("get", calls[2][1], "activationTiming")
    assert calls[3] == ("set", calls[3][1], "activationTiming", 7)
    assert calls[4][2] == "Enact"
    assert calls[4][3][4] == 7
    assert calls[4][3][5] == "ALL_TIMING"


def test_consequence_wraps_a_single_call(modular):
    modular.consequence("healhp", "MainTarget", 25, unit={"t": "unit", "h": 12})
    calls = _calls(modular)
    setup = [call for call in calls if call[0] == "invoke" and call[2] == "SetupModular"]
    assert setup[0][3] == ["TIMING:WhenUse/healhp(MainTarget,25)"]


def test_acquire_returns_the_value(modular):
    assert modular.acquire("getbuffcount", "Self", "SINKING") == 3
    calls = _calls(modular)
    executed = [call for call in calls if call[0] == "invoke" and call[2] == "ExecuteAcquirer"]
    assert executed, calls
    args = executed[0][3]
    assert args[1] == "getbuffcount(Self,SINKING)"
    assert args[2] == "Self,SINKING"
    assert args[3] == ["Self", "SINKING"]


def test_unknown_acquirer_raises(modular):
    with pytest.raises(NotFoundError):
        modular.acquire("nope")


def test_unavailable_modular_degrades():
    env = Env(loaded=False)
    try:
        mod = env.start()
        assert mod.available is False
        with pytest.raises(NotFoundError):
            mod.run("TIMING:WhenUse/buff(MainTarget,SINKING,1,1)")
        with pytest.raises(NotFoundError):
            mod.require()
    finally:
        env.close()
