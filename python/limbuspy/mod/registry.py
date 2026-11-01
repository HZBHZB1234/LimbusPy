"""Registries for hooks, exposed functions and Modular-table entries.

Everything is generation-tagged: a mod owns entries at generation *N*, and a reload bumps that
mod's generation. Dispatch ignores entries whose generation is not current, which is what makes
a reload safe while events are still arriving (a late callback from the old code can never run
against the new state).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Iterable, List, Optional

__all__ = ["HookEntry", "ExposedEntry", "Registry"]


class HookEntry:
    """One ``@hook`` registration."""

    __slots__ = ("mod", "name", "timing", "fn", "gen", "priority", "filters", "sync", "fallback",
                 "calls", "failures", "last_error")

    def __init__(self, mod: str, name: str, timing: str, fn: Callable, gen: int,
                 priority: int = 0, filters: Optional[dict] = None, sync: bool = False,
                 fallback: Any = None) -> None:
        self.mod = mod
        self.name = name
        self.timing = timing
        self.fn = fn
        self.gen = gen
        self.priority = priority
        self.filters = dict(filters or {})
        self.sync = sync
        self.fallback = fallback
        self.calls = 0
        self.failures = 0
        self.last_error: Optional[str] = None

    # -- matching ----------------------------------------------------------------------

    def matches(self, event: dict) -> bool:
        """Applies the declarative filters against the event payload.

        The in-game kernel can filter earlier (Modular style) but applying them here keeps the
        semantics identical for recorded/replayed events and for the mock kernel.
        """
        if event.get("timing") != self.timing:
            return False
        for key, expected in self.filters.items():
            if expected is None:
                continue
            actual = event.get(key)
            if key == "keyword":
                keywords = actual if isinstance(actual, (list, tuple)) else [actual]
                if expected not in keywords:
                    return False
            elif bool(actual) != bool(expected):
                return False
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Hook(%s:%s@%s gen=%d)" % (self.mod, self.name, self.timing, self.gen)


class ExposedEntry:
    """A function callable from the kernel by name (``SYNCALL`` and Modular's ``py()``)."""

    __slots__ = ("mod", "name", "fn", "gen", "fallback", "calls", "failures", "last_error")

    def __init__(self, mod: str, name: str, fn: Callable, gen: int, fallback: Any = None) -> None:
        self.mod = mod
        self.name = name
        self.fn = fn
        self.gen = gen
        self.fallback = fallback
        self.calls = 0
        self.failures = 0
        self.last_error: Optional[str] = None


class Registry:
    """Thread-safe registry of everything a mod publishes."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._hooks: List[HookEntry] = []
        self._exposed: Dict[str, ExposedEntry] = {}
        self._consequences: Dict[str, ExposedEntry] = {}
        self._acquirers: Dict[str, ExposedEntry] = {}
        self._lua_functions: Dict[str, ExposedEntry] = {}
        self._generations: Dict[str, int] = {}

    # -- generation --------------------------------------------------------------------

    def generation(self, mod: str) -> int:
        with self._lock:
            return self._generations.get(mod, 1)

    def bump(self, mod: str) -> int:
        with self._lock:
            gen = self._generations.get(mod, 1) + 1
            self._generations[mod] = gen
            return gen

    def mods(self) -> List[str]:
        with self._lock:
            return sorted(self._generations)

    # -- hooks -------------------------------------------------------------------------

    def add_hook(self, entry: HookEntry) -> HookEntry:
        with self._lock:
            self._hooks.append(entry)
        return entry

    def hooks_for(self, timing: str) -> List[HookEntry]:
        """Current-generation hooks for a timing, in dispatch order (priority desc, then FIFO)."""
        with self._lock:
            entries = [
                entry for entry in self._hooks
                if entry.timing == timing and entry.gen == self._generations.get(entry.mod, 1)
            ]
        entries.sort(key=lambda entry: (-entry.priority,))
        return entries

    def all_hooks(self) -> List[HookEntry]:
        with self._lock:
            return list(self._hooks)

    # -- exposed functions -------------------------------------------------------------

    def expose(self, entry: ExposedEntry) -> ExposedEntry:
        with self._lock:
            self._exposed[entry.name] = entry
        return entry

    def get_exposed(self, name: str) -> Optional[ExposedEntry]:
        with self._lock:
            entry = self._exposed.get(name)
        if entry is None:
            return None
        if entry.gen != self._generations.get(entry.mod, 1):
            return None
        return entry

    def exposed_names(self) -> List[str]:
        with self._lock:
            return sorted(self._exposed)

    # -- modular tables ----------------------------------------------------------------

    def add_table_entry(self, table: str, entry: ExposedEntry) -> ExposedEntry:
        with self._lock:
            self._table(table)[entry.name] = entry
        return entry

    def table_entries(self, table: str) -> List[ExposedEntry]:
        with self._lock:
            return list(self._table(table).values())

    def _table(self, table: str) -> Dict[str, ExposedEntry]:
        if table == "consequence":
            return self._consequences
        if table == "acquirer":
            return self._acquirers
        if table == "lua":
            return self._lua_functions
        raise ValueError("unknown registry table %r" % (table,))

    # -- removal -----------------------------------------------------------------------

    def clear_mod(self, mod: str) -> int:
        """Drops every entry owned by ``mod``. Returns how many were removed."""
        with self._lock:
            before = len(self._hooks)
            self._hooks = [entry for entry in self._hooks if entry.mod != mod]
            removed = before - len(self._hooks)

            for collection in (self._exposed, self._consequences, self._acquirers, self._lua_functions):
                for key in [key for key, entry in collection.items() if entry.mod == mod]:
                    del collection[key]
                    removed += 1
            return removed

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            current = {
                mod: gen for mod, gen in self._generations.items()
            }
            hooks = [entry for entry in self._hooks if entry.gen == current.get(entry.mod, 1)]
            return {
                "mods": sorted(current),
                "generations": current,
                "hooks": len(hooks),
                "exposed": len([name for name, entry in self._exposed.items()
                                if entry.gen == current.get(entry.mod, 1)]),
                "consequences": len(self._consequences),
                "acquirers": len(self._acquirers),
                "lua_functions": len(self._lua_functions),
            }

    def iter_all(self) -> Iterable[Any]:
        with self._lock:
            yield from self._hooks
            yield from self._exposed.values()
            yield from self._consequences.values()
            yield from self._acquirers.values()
            yield from self._lua_functions.values()
