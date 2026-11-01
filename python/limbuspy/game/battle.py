"""``battle``: the entry point most mods use.

    from limbuspy.game import battle

    b = battle.current(session)          # one SNAPSHOT round trip
    for unit in b.player_units:
        if unit.hp_ratio < 0.3:
            ...
        for buff in unit.buffs:          # N round trips, cached per property
            print(buff.keyword, buff.stack)

The snapshot exists because enumerating units must not cost one round trip per unit: the kernel
walks ``BattleObjectManager._allUnitDictionary`` once and returns a flat, bridgeable description.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .members import MANAGER
from .objects import Action, Manager, Unit
from .session import Session, wrap_many

__all__ = ["Battle", "current"]


class Battle:
    """A point-in-time view of the battle plus lazily-resolved units."""

    def __init__(self, session: Session, snapshot: Optional[dict] = None) -> None:
        self.session = session
        self._snapshot: Dict[str, Any] = dict(snapshot or {})
        self._units: List[Unit] = []
        self._build_units()

    # -- snapshot ----------------------------------------------------------------------

    @property
    def round(self) -> int:
        return int(self._snapshot.get("round") or 0)

    @property
    def wave(self) -> int:
        return int(self._snapshot.get("wave") or 0)

    @property
    def raw(self) -> Dict[str, Any]:
        return dict(self._snapshot)

    def refresh(self) -> "Battle":
        self._snapshot = self.session.snapshot()
        self.session.refresh()
        self._build_units()
        return self

    def _build_units(self) -> None:
        self._units = []
        for item in self._snapshot.get("units") or []:
            if not isinstance(item, dict):
                continue
            wire = item.get("ref")
            if not isinstance(wire, dict):
                continue
            data = {key: value for key, value in item.items() if key != "ref"}
            self._units.append(Unit(self.session, wire, data))

    # -- units -------------------------------------------------------------------------

    @property
    def units(self) -> List[Unit]:
        return list(self._units)

    @property
    def player_units(self) -> List[Unit]:
        return [unit for unit in self._units if unit.data.get("is_player")]

    @property
    def enemy_units(self) -> List[Unit]:
        return [unit for unit in self._units if not unit.data.get("is_player")]

    @property
    def alive_units(self) -> List[Unit]:
        return [unit for unit in self._units if not unit.data.get("is_dead")]

    def unit(self, instance_id: int) -> Optional[Unit]:
        for unit in self._units:
            if int(unit.data.get("instance_id") or unit.handle) == int(instance_id):
                return unit
        return None

    def units_by_origin(self, origin_id: int) -> List[Unit]:
        return [unit for unit in self._units if int(unit.data.get("origin_id") or 0) == int(origin_id)]

    # -- manager / actions -------------------------------------------------------------

    def manager(self) -> Manager:
        """The ``BattleObjectManager`` singleton (needs the kernel's global registry)."""
        return Manager(self.session, "global:BattleObjectManager")

    def actions(self) -> List[Action]:
        """Actions of the current command phase, when the kernel exposes that singleton."""
        manager = Manager(self.session, "global:BattleActionModelManager")
        return self.session.coerce(manager.get("_actionList"), Action)


def current(session: Session) -> Battle:
    """Snapshots the battle now."""
    return Battle(session, session.snapshot())
