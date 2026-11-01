"""Game-facing API: typed wrappers over the reflection bridge.

Two layers:

* **raw** - ``session``/``Ref``: ``get``/``set``/``call`` for anything;
* **typed** - ``Unit`` / ``Skill`` / ``Coin`` / ``Buff`` / ``Action`` / ``Battle``: the 90% a mod
  actually touches, with member names verified against a real ``dump.cs``.

Everything degrades gracefully: a missing typed helper never blocks you from using ``Ref.call``.
"""

from __future__ import annotations

from . import battle, members, objects, session
from .battle import Battle
from .members import validate_against_index
from .objects import Action, Buff, Coin, Manager, Ref, Skill, Unit
from .session import Session, wrap, wrap_many

__all__ = [
    "Battle",
    "Session",
    "Ref",
    "Unit",
    "Skill",
    "Coin",
    "Buff",
    "Action",
    "Manager",
    "battle",
    "members",
    "objects",
    "session",
    "validate_against_index",
    "wrap",
    "wrap_many",
    "session_for",
]


def session_for(bridge, index=None, strict=None) -> Session:
    """Convenience: builds a :class:`Session` around an existing bridge."""
    return Session(bridge, index=index, strict=strict)
