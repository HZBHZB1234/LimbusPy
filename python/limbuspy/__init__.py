"""LimbusPy - a Python scripting layer for Limbus Company, bridged to the game by a thin
BepInEx kernel over a named pipe (LPCP).

Import layout (stable surface for mod authors):

    limbuspy.mod        Mod base class, hooks, decorators, hot reload
    limbuspy.game       game facades (battle / statics / assets / locale / ...)
    limbuspy.modular    one-way compatibility with the Modular Lua framework
    limbuspy.runtime    transport, framing, codec, handles, client
    limbuspy.devkit     scaffold / doctor / repl / pack

Only ``limbuspy.runtime`` is needed to talk to the kernel; everything else builds on it.
"""

from __future__ import annotations

__version__ = "0.1.0"
__protocol__ = "1.0"

__all__ = ["__version__", "__protocol__"]
