"""Static data tables: the 2 185 TextAsset JSONs the game ships.

Two-step by design:

1. **extract** (``tools/extract_statics.py``) needs UnityPy and a game install - it pulls the tables
   out of ``static_s1_0_assets_all_<hash>.bundle`` into a plain directory of JSON files;
2. **use** (this module) is pure Python over that directory - read, query, and produce override
   patches.

Keeping them apart is deliberate: mod *authoring* must not require UnityPy, and the extraction step
is the only part that depends on a game build.

    from limbuspy.game import statics
    db = statics.open("extracted/statics")        # one directory, many tables
    db.tables                                    # ["SkillData", "PassiveData", ...]
    db.rows("SkillData", id=601101)
    db.patch("SkillData", 601101, {"maxCoin": 5})  # -> an override patch you can ship
"""

from __future__ import annotations

import io
import json
import os
from typing import Any, Dict, Iterable, List, Optional

__all__ = ["StaticData", "open_tables", "Table", "Patch"]


class Table:
    """One JSON table: either a dict keyed by id or a list of records."""

    def __init__(self, name: str, payload: Any, source: str = "") -> None:
        self.name = name
        self.source = source
        self.payload = payload

    @property
    def is_mapping(self) -> bool:
        return isinstance(self.payload, dict)

    def __len__(self) -> int:
        return len(self.payload or [])

    def keys(self) -> List[Any]:
        if self.is_mapping:
            return list(self.payload.keys())
        return [row.get("id") for row in self.payload or [] if isinstance(row, dict)]

    def get(self, record_id: Any) -> Optional[dict]:
        if self.is_mapping:
            value = self.payload.get(str(record_id), self.payload.get(record_id))
            return value if isinstance(value, dict) else None
        for row in self.payload or []:
            if isinstance(row, dict) and str(row.get("id")) == str(record_id):
                return row
        return None

    def rows(self, **match: Any) -> List[dict]:
        """Filters records by field equality (``rows(tier=3)``)."""
        if self.is_mapping:
            candidates = [value for value in self.payload.values() if isinstance(value, dict)]
        else:
            candidates = [row for row in self.payload or [] if isinstance(row, dict)]
        if not match:
            return candidates
        return [
            row for row in candidates
            if all(_equals(row.get(key), expected) for key, expected in match.items())
        ]

    def __repr__(self) -> str:  # pragma: no cover
        return "<Table %s: %d>" % (self.name, len(self))


def _equals(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (list, tuple, set)):
        return actual in expected
    return str(actual) == str(expected)


class Patch:
    """A set of per-record, per-field overrides ready to be shipped with a mod."""

    def __init__(self) -> None:
        self.changes: Dict[str, Dict[str, dict]] = {}

    def add(self, table: str, record_id: Any, fields: dict) -> "Patch":
        self.changes.setdefault(table, {}).setdefault(str(record_id), {}).update(fields)
        return self

    def apply_to(self, data: "StaticData") -> int:
        """Applies the patch in memory (useful for tests and for validating a patch)."""
        applied = 0
        for table_name, records in self.changes.items():
            table = data.table(table_name)
            if table is None:
                continue
            for record_id, fields in records.items():
                if table.is_mapping:
                    record = table.payload.get(record_id)
                    if isinstance(record, dict):
                        record.update(fields)
                        applied += 1
                else:
                    for row in table.payload or []:
                        if isinstance(row, dict) and str(row.get("id")) == record_id:
                            row.update(fields)
                            applied += 1
                            break
        return applied

    def to_dict(self) -> dict:
        return {"tables": self.changes}

    @classmethod
    def from_dict(cls, payload: dict) -> "Patch":
        patch = cls()
        patch.changes = dict((payload or {}).get("tables") or {})
        return patch

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "Patch":
        with io.open(path, "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    def __len__(self) -> int:
        return sum(len(records) for records in self.changes.values())


class StaticData:
    """A directory of extracted JSON tables."""

    def __init__(self, root: str) -> None:
        self.root = root
        self._tables: Dict[str, Table] = {}
        self._scan()

    # -- discovery ---------------------------------------------------------------------

    def _scan(self) -> None:
        if not os.path.isdir(self.root):
            return
        for entry in sorted(os.listdir(self.root)):
            if not entry.lower().endswith(".json"):
                continue
            path = os.path.join(self.root, entry)
            table = self._load(entry[:-5], path)
            if table is not None:
                self._tables[table.name] = table

    def _load(self, name: str, path: str) -> Optional[Table]:
        try:
            with io.open(path, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return None
        # Unity text assets are often {"<something>": [...]} wrappers.
        if isinstance(payload, dict) and len(payload) == 1:
            (inner_key, inner), = payload.items()
            if isinstance(inner, (list, dict)) and not isinstance(inner, str):
                name = name if inner_key in ("", None) else name
                payload = inner
        return Table(name, payload, path)

    # -- access ------------------------------------------------------------------------

    @property
    def tables(self) -> List[str]:
        return sorted(self._tables)

    def table(self, name: str) -> Optional[Table]:
        if name in self._tables:
            return self._tables[name]
        lowered = name.lower()
        for key, table in self._tables.items():
            if key.lower() == lowered:
                return table
        return None

    def require(self, name: str) -> Table:
        table = self.table(name)
        if table is None:
            raise KeyError("no table %r (have: %s)" % (name, ", ".join(self.tables[:10])))
        return table

    def rows(self, table: str, **match: Any) -> List[dict]:
        return self.require(table).rows(**match) if match else self.require(table).rows()

    def get(self, table: str, record_id: Any) -> Optional[dict]:
        return self.require(table).get(record_id)

    def patch(self, table: str, record_id: Any, fields: dict, patch: Optional[Patch] = None) -> Patch:
        """Records an override; returns the (possibly new) patch object."""
        target = patch or Patch()
        target.add(table, record_id, fields)
        return target

    def __len__(self) -> int:
        return len(self._tables)

    def __repr__(self) -> str:  # pragma: no cover
        return "<StaticData %s: %d table(s)>" % (self.root, len(self))


def open_tables(root: str) -> StaticData:
    """Opens an extracted statics directory (see tools/extract_statics.py)."""
    return StaticData(root)
