"""One-way compatibility with the Modular Lua framework.

The user-visible contract is one-directional on purpose (see docs/02-mod-authoring.md §8): **LimbusPy
uses Modular**, Modular does not need to know LimbusPy exists. Everything here therefore goes
through the reflection layer, calling Modular's own objects - no compiled reference, so Modular
upgrading cannot break the kernel's assembly load.

What it gives you::

    from limbuspy.modular import Modular
    m = Modular(ctx.session)
    if m.available:
        m.run("TIMING:WhenUse/buff(MainTarget,SINKING,3,1)", unit=ctx.target)   # DSL string
        m.consequence("healhp", "MainTarget", 25, unit=ctx.target)              # one consequence
        stacks = m.acquire("getbuffcount", "Self", "SINKING")                    # one acquirer

Verified member names come from Modular's own source (``ModularSkillScripts`` 5.1.15):
``MainClass.timingDict / consequenceDict / acquirerDict``, ``ModularSA.SetupModular``,
``ModularSA.Enact``, ``IModularAcquirer.ExecuteAcquirer``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..game.session import Session
from ..runtime.errors import BridgeError, NotFoundError
from . import dsl

__all__ = ["Modular", "dsl", "MAIN_CLASS", "SA_TYPE"]

MAIN_CLASS = "type:ModularSkillScripts.MainClass"
SA_TYPE = "ModularSkillScripts.ModularSA"

_TIMING_FALLBACK = {
    "StartBattle": 0, "WhenUse": 1, "BeforeAttack": 2, "OnSucceedAttack": 7,
    "WhenHit": 8, "EndBattle": 6, "OnGainBuff": 52, "RoundStart": -1,
}


class Modular:
    """Calls into the Modular framework that is (optionally) loaded in the game."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._timings: Optional[Dict[str, int]] = None
        self._available: Optional[bool] = None

    # -- capability --------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True when Modular is loaded (its types resolve through reflection)."""
        if self._available is None:
            try:
                types = self.session.bridge.call("", "reflect.types", "ModularSkillScripts.MainClass")
                self._available = bool(types)
            except BridgeError:
                self._available = False
        return bool(self._available)

    def require(self) -> "Modular":
        if not self.available:
            raise NotFoundError("Modular is not loaded; native hooks are available without it")
        return self

    # -- timing table ------------------------------------------------------------------

    @property
    def timings(self) -> Dict[str, int]:
        """``MainClass.timingDict`` as a plain dict (cached per session)."""
        if self._timings is None:
            try:
                raw = self.session.get(MAIN_CLASS, "timingDict")
                self._timings = {str(key): int(value) for key, value in (raw or {}).items()}
            except BridgeError:
                self._timings = dict(_TIMING_FALLBACK)
        return self._timings

    def timing_id(self, name: str) -> int:
        """Numeric actevent for a timing name (Modular's names, case-insensitive)."""
        table = self.timings
        if name in table:
            return table[name]
        for key, value in table.items():
            if key.lower() == str(name).lower():
                return value
        raise NotFoundError("unknown Modular timing %r (known: %s)"
                            % (name, ", ".join(sorted(table)[:10])))

    # -- running DSL -------------------------------------------------------------------

    def new_context(self):
        """A fresh ``ModularSA`` instance (the context Modular passes to scripts)."""
        self.require()
        return self.session.static(SA_TYPE + "::.ctor")

    def run(self, instructions: str, unit: Any = None, skill: Any = None, action: Any = None,
            opposing: Any = None, actevent: Optional[int] = None, timeout: float = 5.0) -> Any:
        """Executes a Modular instruction string right now in the given context.

        ``instructions`` is exactly what would go into a ``Modular/`` script name, e.g.
        ``"TIMING:OnSucceedAttack(Head)/buff(MainTarget,SINKING,2,1)"``.
        """
        self.require()
        context = self.new_context()
        self.session.call(context, "SetupModular", instructions, timeout=timeout)

        if actevent is None:
            # SetupModular parses TIMING: and stores the numeric event; reuse it so Enact accepts it.
            actevent = int(self.session.get(context, "activationTiming") or 0)

        self.session.set(context, "activationTiming", int(actevent), timeout=timeout)
        return self.session.call(context, "Enact", unit, skill, action, opposing, int(actevent),
                                 "ALL_TIMING", timeout=timeout)

    def consequence(self, name: str, *args: Any, unit: Any = None, timing: str = "WhenUse",
                    heads: Optional[bool] = None) -> Any:
        """Runs a single Modular consequence by name (e.g. ``healhp``, ``buff``, ``dmgmult``)."""
        built = dsl.script().timing(timing, heads=heads).consequence(name, *args)
        return self.run(built.render(), unit=unit)

    # -- acquirers ---------------------------------------------------------------------

    def acquire(self, name: str, *args: Any, heads: Optional[bool] = None) -> int:
        """Evaluates one Modular acquirer and returns its integer value."""
        self.require()
        table = self.session.get(MAIN_CLASS, "acquirerDict")
        if not isinstance(table, dict) or name not in table:
            raise NotFoundError("unknown Modular acquirer %r" % (name,))

        implementation = table[name]
        context = self.new_context()
        circles = [str(arg) for arg in args]
        return int(self.session.call(
            implementation, "ExecuteAcquirer", context, "%s(%s)" % (name, ",".join(circles)),
            ",".join(circles), circles))

    # -- catalogue ---------------------------------------------------------------------

    def consequences(self) -> List[str]:
        table = self.session.get(MAIN_CLASS, "consequenceDict")
        return sorted(table.keys()) if isinstance(table, dict) else []

    def acquirers(self) -> List[str]:
        table = self.session.get(MAIN_CLASS, "acquirerDict")
        return sorted(table.keys()) if isinstance(table, dict) else []

    def describe(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "timings": len(self.timings) if self.available else 0,
            "consequences": len(self.consequences()) if self.available else 0,
            "acquirers": len(self.acquirers()) if self.available else 0,
        }
