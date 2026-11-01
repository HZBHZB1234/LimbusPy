"""File watching for hot reload.

Deliberately dependency-free: ``watchdog`` is nice but not required, and polling mtimes of ``*.py``
files is cheap (a few hundred files) and predictable. The watcher only reports *which mods* changed;
the runtime decides what to do about it.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Dict, List, Set

__all__ = ["ModWatcher", "scan_mod_timestamps"]


def scan_mod_timestamps(mods_dir: str) -> Dict[str, float]:
    """Maps ``"<mod>/<relative path>"`` to its mtime for every Python file under a mods dir."""
    snapshot: Dict[str, float] = {}
    if not os.path.isdir(mods_dir):
        return snapshot

    for entry in os.scandir(mods_dir):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        try:
            for root, _dirs, files in os.walk(entry.path):
                for name in files:
                    if not name.endswith((".py", ".toml", ".json")):
                        continue
                    full = os.path.join(root, name)
                    relative = os.path.relpath(full, mods_dir).replace("\\", "/")
                    try:
                        snapshot[relative] = os.stat(full).st_mtime
                    except OSError:
                        continue
        except OSError:
            continue
    return snapshot


class ModWatcher(threading.Thread):
    """Polls mtimes and calls ``on_change([mod names])`` after a short debounce."""

    def __init__(self, mods_dir: str, on_change: Callable[[List[str]], None],
                 interval: float = 0.4, debounce: float = 0.15) -> None:
        super().__init__(name="limbuspy-watcher", daemon=True)
        self.mods_dir = mods_dir
        self.on_change = on_change
        self.interval = max(0.05, float(interval))
        self.debounce = max(0.0, float(debounce))

        self._stop = threading.Event()
        self._snapshot = scan_mod_timestamps(mods_dir)
        self._pending: Set[str] = set()
        self._last_change = 0.0

    def stop(self) -> None:
        self._stop.set()

    def poll_once(self) -> List[str]:
        """One polling step. Returns the mod names that were reported (empty if none)."""
        current = scan_mod_timestamps(self.mods_dir)
        changed: Set[str] = set()
        for path, stamp in current.items():
            if self._snapshot.get(path) != stamp:
                changed.add(path.split("/", 1)[0])
        for path in self._snapshot:
            if path not in current:
                changed.add(path.split("/", 1)[0])

        self._snapshot = current
        if not changed:
            return []

        self._pending.update(changed)
        now = time.monotonic()
        if now - self._last_change < self.debounce:
            self._last_change = now
            return []

        self._last_change = now
        names = sorted(self._pending)
        self._pending.clear()
        try:
            self.on_change(names)
        except Exception:  # noqa: BLE001 - a failed reload must not kill the watcher
            pass
        return names

    def run(self) -> None:  # pragma: no cover - exercised through poll_once in tests
        while not self._stop.wait(self.interval):
            self.poll_once()
