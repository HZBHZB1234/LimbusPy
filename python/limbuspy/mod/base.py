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
        #: Patch ids this mod installed; removed automatically before a reload.
        self._patches: List[str] = []
        #: 本 mod 注册的 ``Content`` 子类（Buff / Passive / ...），由 loader 回填。
        self.contents: List[Any] = []
        #: 注册期发现的问题（非法内容、被拒绝的谓词）。只跳过出问题的项，不拖垮整个 mod。
        self.registration_errors: List[str] = []
        self._session = None
        self._ops_session = None
        self._ops_facade = None

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

    def _new_session(self):
        """hook 之外的会话：**关掉缓存**（读到的是当次真值）。"""
        if self.bridge is None:
            return None
        try:
            from ..game import Session

            return Session(self.bridge, use_cache=False)
        except Exception:  # noqa: BLE001
            return None

    @property
    def session(self):
        """hook 之外的只读/诊断入口（``on_load``、后台线程、REPL 式操作）。

        **无缓存**：``Mod.session`` 活在整个 mod 生命周期上，如果缓存读值，第二回合读到的还是
        第一回合的 HP。hook 里请用 ``ctx.session``（每次回调新建、缓存只活一次回调）。
        """
        if self._session is None:
            self._session = self._new_session()
        return self._session

    @property
    def ops(self):
        """A 层（封装 op）入口。

        hook 里请用 ``ctx.ops``（绑在本次回调的 fresh session 上）；``self.ops`` 用于
        ``on_load`` / 后台线程等**没有 ctx** 的场景。op 本身无状态，且这里刻意用**无缓存**
        会话，避免 A 层入口继承 B 层读缓存的语义。
        """
        if self._ops_facade is None:
            if self._ops_session is None:
                self._ops_session = self._new_session()
            if self._ops_session is None:
                raise RuntimeError("mod %r 还没有连接到内核，拿不到 ops" % (self.name or "?",))
            from ..api.ops import Ops

            self._ops_facade = Ops(self._ops_session)
        return self._ops_facade

    # -- harmony patches ----------------------------------------------------------------

    def patch(self, owner: str, method: str, fn: Optional[str] = None, kind: str = "postfix",
              sig: Optional[list] = None, fallback: Any = None) -> str:
        """Patches ``owner.method`` and dispatches to an exposed function of this mod.

        ``fn`` defaults to ``"<mod>.<method>"``; a bare name is prefixed with the mod name. The id
        is remembered so a hot reload can remove the patch before the old code disappears.
        """
        if self.bridge is None:
            raise RuntimeError("mod %r is not attached to a bridge" % (self.name or "?"))
        target_fn = fn or method
        if "." not in target_fn:
            target_fn = "%s.%s" % (self.name or "mod", target_fn)
        binding = self.bridge.patch(owner, method, target_fn, kind=kind, sig=sig, fallback=fallback)
        patch_id = binding.get("id") if isinstance(binding, dict) else None
        if patch_id:
            self._patches.append(patch_id)
        return patch_id

    def unpatch(self, patch_id: str) -> bool:
        if self.bridge is None or not patch_id:
            return False
        try:
            removed = self.bridge.unpatch(patch_id)
        finally:
            if patch_id in self._patches:
                self._patches.remove(patch_id)
        return removed

    def unpatch_all(self) -> int:
        """Removes every patch this mod installed. Called by the runtime before a reload."""
        removed = 0
        for patch_id in list(self._patches):
            try:
                if self.unpatch(patch_id):
                    removed += 1
            except Exception:  # noqa: BLE001 - best effort during teardown
                self._patches.remove(patch_id) if patch_id in self._patches else None
        return removed

    def log(self, message: str) -> None:
        if self.logger is not None:
            self.logger("[%s] %s" % (self.name or "mod", message))

    def __repr__(self) -> str:  # pragma: no cover
        return "<Mod %s %s>" % (self.name or "?", self.version)
