"""Programmatic access to tools/pack.py (the distribution packer)."""

from __future__ import annotations

import os
import sys
from typing import List, Optional

__all__ = ["pack", "repo_root"]


def repo_root() -> str:
    """The LimbusPy checkout root (this file is python/limbuspy/devkit/pack.py)."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def pack(out: Optional[str] = None, variant: str = "both", python_embed: Optional[str] = None,
         build: bool = False, argv: Optional[List[str]] = None) -> int:
    """Runs the packer in-process; returns its exit code."""
    tools = os.path.join(repo_root(), "tools")
    if not os.path.isdir(tools):
        print("tools/ not found next to the package: %s" % tools, file=sys.stderr)
        return 2

    sys.path.insert(0, tools)
    import importlib

    importlib.invalidate_caches()
    packer = importlib.import_module("pack")

    args = list(argv or [])
    if not args:
        args = ["--out", out or os.path.join(repo_root(), "dist"), "--variant", variant]
        if python_embed:
            args += ["--python-embed", python_embed]
        if not build:
            args += ["--no-build"]
    return packer.main(args)
