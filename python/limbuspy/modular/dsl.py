"""Modular instruction-string builder.

LimbusPy's primary API is native hooks; Modular's DSL is the compatibility/escape hatch. When a
script *does* need a Modular command, building it as a string is error prone (the `/` and `:`
separators, the ``TIMING:Name(Head)`` prefix, math operators). This builder renders the same string
Modular parses, so it can be unit tested without the game.

    from limbuspy.modular.dsl import script
    script().timing("OnSucceedAttack", heads=True).value(0, "getdmg").buff("MainTarget", "SINKING", 3, 1).render()
    # "TIMING:OnSucceedAttack(Head)/VALUE_0(getdmg)/buff(MainTarget,SINKING,3,1)"
"""

from __future__ import annotations

from typing import Any, List, Optional

__all__ = ["script", "Script", "MODULAR_TIMINGS"]

#: Timing names Modular knows by default (subset that matters for scripting; the kernel reads the
#: authoritative list from ``MainClass.timingDict`` when it is available).
MODULAR_TIMINGS = (
    "StartBattle", "WhenUse", "BeforeAttack", "StartDuel", "WinDuel", "DefeatDuel", "EndBattle",
    "OnSucceedAttack", "WhenHit", "EndSkill", "BeforeDefense", "OnDie", "OnOtherDie", "DuelClash",
    "AfterDuelClash", "OnStartBehaviour", "BeforeBehaviour", "OnEndBehaviour", "EnemyKill",
    "OnBreak", "OnOtherBreak", "OnGainBuff", "OnUseBuff", "RoundStart", "OnRoundStart",
    "ChangeTakeDamage", "OnRetreat", "CanDuel", "OnCoinToss", "OnCoinAfterAttack",
)


def _join_args(args) -> str:
    parts: List[str] = []
    for arg in args:
        if isinstance(arg, bool):
            parts.append("1" if arg else "0")
        elif isinstance(arg, float) and arg.is_integer():
            parts.append(str(int(arg)))
        else:
            parts.append(str(arg))
    return ",".join(parts)


class Script:
    """Incremental builder for one Modular instruction string."""

    def __init__(self) -> None:
        self._batches: List[str] = []
        self._timing: Optional[str] = None
        self._timing_args: List[str] = []

    # -- header ------------------------------------------------------------------------

    def timing(self, name: str, heads: Optional[bool] = None, tails: Optional[bool] = None,
               crit: Optional[bool] = None, keyword: Optional[str] = None) -> "Script":
        """``TIMING:<name>(<filters>)`` - Modular's first batch."""
        self._timing = str(name)
        tokens: List[str] = []
        if heads is True:
            tokens.append("Head")
        elif tails is True:
            tokens.append("Tail")
        if crit is True:
            tokens.append("Crit")
        elif crit is False:
            tokens.append("NoCrit")
        if keyword:
            tokens.append(str(keyword))
        self._timing_args = tokens
        return self

    # -- batches -----------------------------------------------------------------------

    def raw(self, text: str) -> "Script":
        """Appends a raw batch (anything Modular's parser accepts)."""
        self._batches.append(str(text))
        return self

    def value(self, slot: int, acquirer: str, *args: Any) -> "Script":
        """``VALUE_<slot>(<acquirer>(<args>))``."""
        return self.raw("VALUE_%d(%s(%s))" % (int(slot), acquirer, _join_args(args)))

    def mobj(self, slot: int, acquirer: str, *args: Any) -> "Script":
        return self.raw("MOBJ%d(%s(%s))" % (int(slot), acquirer, _join_args(args)))

    def if_(self, acquirer: str, *args: Any) -> "Script":
        return self.raw("IF(%s(%s))" % (acquirer, _join_args(args)))

    def if_not(self, acquirer: str, *args: Any) -> "Script":
        return self.raw("IFNOT(%s(%s))" % (acquirer, _join_args(args)))

    def stop_if(self, acquirer: str, *args: Any) -> "Script":
        return self.raw("STOPIF(%s(%s))" % (acquirer, _join_args(args)))

    def consequence(self, name: str, *args: Any) -> "Script":
        """Any Modular consequence, e.g. ``buff(...)`` / ``dmgmult(...)``."""
        return self.raw("%s(%s)" % (name, _join_args(args)))

    def buff(self, target: str, keyword: str, stack: int, turn: int) -> "Script":
        """Shortcut for Modular's most used consequence."""
        return self.consequence("buff", target, keyword, stack, turn)

    def loop(self, targeting: str) -> "Script":
        return self.raw("LOOP:" + str(targeting))

    # -- render ------------------------------------------------------------------------

    def render(self) -> str:
        batches: List[str] = []
        if self._timing:
            header = "TIMING:" + self._timing
            if self._timing_args:
                header += "(" + ",".join(self._timing_args) + ")"
            batches.append(header)
        batches.extend(self._batches)
        return "/".join(batches)

    def __str__(self) -> str:  # pragma: no cover
        return self.render()


def script() -> Script:
    return Script()
