"""The ``Mod`` base class.

A mod is a Python package directory::

    mods/sinking_overload/
        __init__.py      # defines `class MyMod(Mod)` (must be importable as a package)
        mod.toml         # optional manifest
        assets/          # anything the mod needs

Inheritance is first class: hooks and exposed functions are collected across the MRO, so a shared
base class can publish behaviour that several mods reuse.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..runtime.client import Bridge
from .decorators import ConfigSpec

__all__ = ["Mod"]


class Mod:
    #: Directory name (injected by the loader when not overridden).
    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    author: str = ""

    #: Other mods this one needs: ``["core_lib>=1.0.0"]``.
    requires: List[str] = []

    #: Optional ``config(...)`` spec; persisted next to the state.
    cfg: Optional[ConfigSpec] = None

    def __init__(self) -> None:
        self.state: Dict[str, Any] = {}
        self.bridge: Optional[Bridge] = None
        self.logger = None

    # -- lifecycle ----------------------------------------------------------------------

    def on_load(self) -> None:
        """Called after registration, before events are dispatched."""

    def on_unload(self) -> None:
        """Called before the mod is torn down (hot reload or shutdown). Keep it quick."""

    def on_save(self) -> Optional[dict]:
        """Returns extra state to persist across a reload. Keep it JSON-serialisable."""
        return None

    def on_restore(self, state: dict) -> None:
        """Restores what ``on_save`` returned."""

    # -- helpers ------------------------------------------------------------------------

    def log(self, message: str) -> None:
        if self.logger is not None:
            self.logger("[%s] %s" % (self.name or "mod", message))

    def __repr__(self) -> str:  # pragma: no cover
        return "<Mod %s %s>" % (self.name or "?", self.version)
