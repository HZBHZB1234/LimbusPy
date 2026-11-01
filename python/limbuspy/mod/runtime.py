"""The mod runtime: dispatch, hot reload and lifecycle.

This is the engine that turns kernel events into hook calls. It has no game knowledge - everything
game-specific goes through :meth:`Context.call` - so it can be driven by the mock kernel in tests.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

from .. import __version__ as limbuspy_version
from ..runtime import protocol
from ..runtime.client import Bridge
from ..runtime.errors import BridgeError
from ..runtime.protocol import MsgType
from .base import Mod
from .context import Context
from .loader import (
    LoadedMod,
    ManifestError,
    ModManifest,
    describe_failure,
    discover,
    import_mod,
    instantiate_mod,
    load_config,
    load_mod,
    register_instance,
    resolve_order,
    save_config,
    unload_module,
)
from .registry import Registry

__all__ = ["ModRuntime", "ReloadResult"]


class ReloadResult(dict):
    """``{"reloaded": [...], "failed": {...}, "generation": {...}}`` with attribute access."""

    @property
    def ok(self) -> bool:
        return not self.get("failed")

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class ModRuntime:
    def __init__(
        self,
        bridge: Bridge,
        mods_dir: str,
        state_dir: str,
        logger=None,
        frame_budget_ms: float = 3.0,
        autoload: bool = True,
        stubs_build: str = "",
    ) -> None:
        self.bridge = bridge
        self.mods_dir = mods_dir
        self.state_dir = state_dir
        self.frame_budget_ms = float(frame_budget_ms)
        self.autoload = autoload
        self.stubs_build = stubs_build

        self.registry = Registry()
        self.mods: Dict[str, LoadedMod] = {}
        self.order: List[ModManifest] = []
        self.load_errors: Dict[str, str] = {}

        self._logger = logger or (lambda message: print(message, file=sys.stderr, flush=True))
        self._hook_state: Dict[str, Dict[str, dict]] = {}
        self._paused = False
        self._watcher = None
        self._closed = False

        self.counters: Dict[str, int] = {
            "event_batches": 0,
            "events": 0,
            "hooks_run": 0,
            "hooks_failed": 0,
            "hooks_skipped": 0,
            "sync_calls": 0,
            "sync_failed": 0,
            "reloads": 0,
            "over_budget_frames": 0,
        }

    # -- logging ------------------------------------------------------------------------

    def log(self, message: str) -> None:
        self._logger(message)
        try:
            if not self.bridge.closed:
                self.bridge.notify(MsgType.LOG, {"level": "info", "mod": "limbuspy", "msg": message})
        except Exception:  # noqa: BLE001 - logging must never raise
            pass

    def _log_error(self, message: str) -> None:
        self._logger(message)
        try:
            if not self.bridge.closed:
                self.bridge.notify(MsgType.LOG, {"level": "error", "mod": "limbuspy", "msg": message})
        except Exception:  # noqa: BLE001
            pass

    # -- lifecycle ----------------------------------------------------------------------

    def register_handlers(self) -> None:
        self.bridge.on(MsgType.EVENTBATCH, self._on_event_batch)
        self.bridge.on(MsgType.RELOAD, self._on_reload)
        self.bridge.on(MsgType.STATS, self._on_stats)
        self.bridge.on_request(MsgType.SYNCALL, self._on_sync_call)
        self.bridge.on_request(MsgType.STATE, self._on_state_request)

    def start(self) -> dict:
        """Loads mods, then performs the HELLO/READY handshake. Returns the kernel's HELLO."""
        self.bridge.py_caps = [
            protocol.Caps.HOT_RELOAD,
            protocol.Caps.REFLECT_MANAGED,
            protocol.Caps.HANDLE_ARENA,
        ]
        self.register_handlers()

        if self.autoload:
            self.load_all()

        hello = self.bridge.handshake(
            mods=sorted(self.mods),
            stubs_build=self.stubs_build,
        )
        self.log("limbuspy %s ready (%d mod(s), %d hook(s))"
                 % (limbuspy_version, len(self.mods), self.registry.stats()["hooks"]))
        return hello

    def run_forever(self, poll: float = 0.25) -> None:
        """Blocks until the kernel goes away. Safe to call from the bootstrap entry point."""
        try:
            while not self.bridge.wait_closed(poll):
                pass
        except KeyboardInterrupt:  # pragma: no cover - interactive
            self.log("interrupted; shutting down")
        finally:
            self.stop()

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop_watching()
        for name in list(self.mods):
            loaded = self.mods.get(name)
            if loaded is None:
                continue
            self._save_state(name, loaded)
            try:
                loaded.instance.on_unload()
            except Exception as exc:  # noqa: BLE001
                self._log_error("on_unload failed for %s: %s" % (name, describe_failure(exc)))

    # -- loading ------------------------------------------------------------------------

    def load_all(self) -> Dict[str, str]:
        self.load_errors.clear()
        try:
            manifests = discover(self.mods_dir)
        except ManifestError as exc:
            self._log_error("mod discovery failed: %s" % exc)
            return dict(self.load_errors)

        # Phase 1: import everything so code-declared `requires` participates in ordering.
        prepared = []
        for manifest in manifests:
            try:
                module = import_mod(manifest)
                instance = instantiate_mod(module, manifest)
            except Exception as exc:  # noqa: BLE001 - one broken mod must not stop discovery
                self.load_errors[manifest.name] = describe_failure(exc)
                self._log_error("failed to import %s: %s" % (manifest.name, describe_failure(exc)))
                unload_module(manifest)
                continue

            if instance.requires:
                merged = list(manifest.requires)
                for requirement in instance.requires:
                    if requirement not in merged:
                        merged.append(requirement)
                manifest.requires = merged

            prepared.append((manifest, module, instance))

        try:
            self.order = resolve_order([manifest for manifest, _module, _instance in prepared])
        except ManifestError as exc:
            self._log_error("dependency resolution failed: %s" % exc)
            for manifest, _module, _instance in prepared:
                unload_module(manifest)
            return dict(self.load_errors)

        # Phase 2: register in dependency order.
        by_name = {manifest.name: (manifest, module, instance)
                   for manifest, module, instance in prepared}
        for manifest in self.order:
            entry = by_name.get(manifest.name)
            if entry is None:
                continue
            _manifest, module, instance = entry
            self._register_prepared(manifest, module, instance)
        return dict(self.load_errors)

    def _register_prepared(self, manifest: ModManifest, module, instance: Mod) -> Optional[LoadedMod]:
        try:
            self.registry.bump(manifest.name)  # first load starts at generation 2
            instance.name = instance.name or manifest.name
            instance.bridge = self.bridge
            instance.logger = self._logger
            gen = self.registry.generation(manifest.name)
            loaded = register_instance(instance, manifest.name, self.registry, gen)
            loaded.module = module
            loaded.manifest = manifest
            self._restore_state(manifest.name, loaded)
            self.mods[manifest.name] = loaded
            try:
                loaded.instance.on_load()
            except Exception as exc:  # noqa: BLE001
                self._log_error("on_load failed for %s: %s" % (manifest.name, describe_failure(exc)))
            self.log("loaded %s %s (%d hook(s))"
                     % (manifest.name, loaded.instance.version, len(loaded.hooks)))
            return loaded
        except Exception as exc:  # noqa: BLE001
            self.load_errors[manifest.name] = describe_failure(exc)
            self._log_error("failed to register %s: %s" % (manifest.name, describe_failure(exc)))
            unload_module(manifest)
            return None

    def _load_one(self, manifest: ModManifest) -> Optional[LoadedMod]:
        try:
            self.registry.bump(manifest.name)  # first load starts at generation 2
            loaded = load_mod(manifest, self.registry, self.bridge, self._logger)
            self._restore_state(manifest.name, loaded)
            self.mods[manifest.name] = loaded
            try:
                loaded.instance.on_load()
            except Exception as exc:  # noqa: BLE001
                self._log_error("on_load failed for %s: %s" % (manifest.name, describe_failure(exc)))
            self.log("loaded %s %s (%d hook(s))"
                     % (manifest.name, loaded.instance.version, len(loaded.hooks)))
            return loaded
        except Exception as exc:  # noqa: BLE001 - one broken mod must not stop the rest
            self.load_errors[manifest.name] = describe_failure(exc)
            self._log_error("failed to load %s: %s" % (manifest.name, describe_failure(exc)))
            unload_module(manifest)
            return None

    # -- state --------------------------------------------------------------------------

    def _state_for(self, mod_name: str) -> dict:
        return self._hook_state.setdefault(mod_name, {})

    def _save_state(self, mod_name: str, loaded: LoadedMod) -> dict:
        extra: Optional[dict] = None
        try:
            extra = loaded.instance.on_save()
        except Exception as exc:  # noqa: BLE001
            self._log_error("on_save failed for %s: %s" % (mod_name, describe_failure(exc)))

        state: Dict[str, Any] = {}
        if isinstance(extra, dict):
            state.update(extra)
        if loaded.instance.state:
            state.update(loaded.instance.state)
        # per-hook scratch state (counters etc.) survives a reload
        hook_state = self._hook_state.get(mod_name) or {}
        if hook_state:
            state["__hooks__"] = {name: dict(values) for name, values in hook_state.items()}

        if isinstance(loaded.instance.cfg, object) and hasattr(loaded.instance.cfg, "schema"):
            save_config(loaded.instance, self.state_dir, state)
        else:
            save_config(loaded.instance, self.state_dir, state)
        return state

    def _restore_state(self, mod_name: str, loaded: LoadedMod) -> None:
        data = load_config(loaded.instance, self.state_dir)
        config = data.get("config")
        if config and hasattr(loaded.instance.cfg, "load"):
            loaded.instance.cfg.load(config)

        state = data.get("state") or {}
        if not isinstance(state, dict):
            return

        hooks = state.get("__hooks__")
        if isinstance(hooks, dict):
            bucket = self._state_for(mod_name)
            for name, values in hooks.items():
                if isinstance(values, dict):
                    bucket[name] = dict(values)

        payload = {key: value for key, value in state.items() if key != "__hooks__"}
        if payload:
            loaded.instance.state.update(payload)
            try:
                loaded.instance.on_restore(payload)
            except Exception as exc:  # noqa: BLE001
                self._log_error("on_restore failed for %s: %s" % (mod_name, describe_failure(exc)))

    # -- hot reload ---------------------------------------------------------------------

    def reload(self, names: Optional[List[str]] = None, reason: str = "manual") -> ReloadResult:
        """Reloads the named mods (default: all), preserving config + state."""
        target_names = list(names) if names else [manifest.name for manifest in self.order]
        target_names = [name for name in target_names if name in self.mods or name in self.load_errors]

        order_index = {manifest.name: index for index, manifest in enumerate(self.order)}
        target_names.sort(key=lambda name: order_index.get(name, 1 << 30))

        result = ReloadResult(reloaded=[], failed={}, generation={})
        manifests = {manifest.name: manifest for manifest in self.order}

        for name in target_names:
            manifest = manifests.get(name)
            if manifest is None:
                continue

            loaded = self.mods.get(name)
            if loaded is not None:
                self._save_state(name, loaded)
                try:
                    loaded.instance.on_unload()
                except Exception as exc:  # noqa: BLE001
                    self._log_error("on_unload failed for %s: %s" % (name, describe_failure(exc)))

            self.registry.clear_mod(name)
            unload_module(manifest)
            self.mods.pop(name, None)
            self.load_errors.pop(name, None)

            gen = self.registry.bump(name)
            result.generation[name] = gen
            reloaded = self._load_one(manifest)
            if reloaded is None:
                result.failed[name] = self.load_errors.get(name, "unknown error")
            else:
                result.reloaded.append(name)

        self.counters["reloads"] += 1
        if result.ok:
            self.log("reloaded %s (%s)" % (", ".join(result.reloaded) or "nothing", reason))
        else:
            self._log_error("reload finished with failures: %s (%s)"
                            % (", ".join(sorted(result.failed)), reason))
        return result

    def _on_reload(self, frame) -> None:
        payload = frame.decode()
        phase = payload.get("phase") or "resume"
        if phase == "quiesce":
            self._paused = True
            return
        self._paused = False
        names = payload.get("mods") or []
        self.reload(names or None, reason="kernel request")

    # -- dispatch -----------------------------------------------------------------------

    def _on_event_batch(self, frame) -> None:
        payload = frame.decode()
        events = payload.get("events") or []
        self.counters["event_batches"] += 1
        self.counters["events"] += len(events)

        started = time.perf_counter()
        budget = self.frame_budget_ms / 1000.0
        over_budget = False
        actions: List[dict] = []

        for index, event in enumerate(events):
            effects: List[dict] = []
            if not self._paused and isinstance(event, dict):
                for entry in self.registry.hooks_for(event.get("timing") or ""):
                    if not entry.matches(event):
                        continue
                    if over_budget:
                        self.counters["hooks_skipped"] += 1
                        continue

                    context = Context(
                        bridge=self.bridge,
                        mod=entry.mod,
                        timing=entry.timing,
                        gen=entry.gen,
                        event=event,
                        state=self._state_for(entry.mod).setdefault(entry.name, {}),
                        logger=self._logger,
                    )
                    try:
                        entry.calls += 1
                        self.counters["hooks_run"] += 1
                        entry.fn(context)
                    except BridgeError as exc:
                        entry.failures += 1
                        entry.last_error = str(exc)
                        self.counters["hooks_failed"] += 1
                        self._log_error("hook %s.%s failed: %s" % (entry.mod, entry.name, exc))
                    except Exception as exc:  # noqa: BLE001
                        entry.failures += 1
                        entry.last_error = describe_failure(exc)
                        self.counters["hooks_failed"] += 1
                        self._log_error("hook %s.%s raised: %s\n%s"
                                        % (entry.mod, entry.name, describe_failure(exc),
                                           "".join(traceback.format_exc(limit=4))))
                    effects.extend(context.effects)

                    if time.perf_counter() - started > budget:
                        over_budget = True
                        self.counters["over_budget_frames"] += 1
                        self._log_error("frame budget exceeded (%.1f ms); skipping remaining hooks"
                                        % (self.frame_budget_ms))

            actions.append({"event": index, "effects": effects})

        try:
            self.bridge.notify(MsgType.ACT, {"seq": payload.get("seq"), "actions": actions})
        except Exception:  # noqa: BLE001 - kernel may be gone
            pass

    def _on_sync_call(self, payload: dict) -> Any:
        name = payload.get("fn") or ""
        entry = self.registry.get_exposed(name)
        if entry is None:
            raise BridgeError(protocol.ErrorCode.NOT_FOUND,
                              "no exposed function %r (known: %s)"
                              % (name, ", ".join(self.registry.exposed_names()[:8])))

        context = Context(
            bridge=self.bridge,
            mod=entry.mod,
            timing=payload.get("timing") or "",
            gen=entry.gen,
            event=payload.get("ctx") if isinstance(payload.get("ctx"), dict) else {},
            state=self._state_for(entry.mod).setdefault(entry.name, {}),
            logger=self._logger,
        )
        self.counters["sync_calls"] += 1
        try:
            entry.calls += 1
            return entry.fn(context)
        except BridgeError:
            entry.failures += 1
            self.counters["sync_failed"] += 1
            raise
        except Exception as exc:  # noqa: BLE001
            entry.failures += 1
            entry.last_error = describe_failure(exc)
            self.counters["sync_failed"] += 1
            raise BridgeError(protocol.ErrorCode.SCRIPT,
                              "%s.%s raised %s" % (entry.mod, entry.name, describe_failure(exc))) from exc

    # -- introspection ------------------------------------------------------------------

    def _on_stats(self, frame) -> None:
        try:
            self.bridge.notify(MsgType.STATS, self.stats())
        except Exception:  # noqa: BLE001
            pass

    def _on_state_request(self, payload: dict) -> Any:
        name = payload.get("mod") or ""
        operation = payload.get("op") or "save"
        loaded = self.mods.get(name)
        if loaded is None:
            raise BridgeError(protocol.ErrorCode.NOT_FOUND, "no such mod %r" % name)
        if operation == "save":
            return self._save_state(name, loaded)
        self._restore_state(name, loaded)
        return {"ok": True}

    def stats(self) -> Dict[str, Any]:
        hooks = []
        for entry in self.registry.all_hooks():
            hooks.append({
                "mod": entry.mod,
                "name": entry.name,
                "timing": entry.timing,
                "gen": entry.gen,
                "calls": entry.calls,
                "failures": entry.failures,
                "last_error": entry.last_error,
            })
        return {
            "limbuspy": limbuspy_version,
            "mods": sorted(self.mods),
            "load_errors": dict(self.load_errors),
            "registry": self.registry.stats(),
            "counters": dict(self.counters),
            "hooks": hooks,
            "paused": self._paused,
            "watching": self._watcher is not None,
        }

    # -- file watching ------------------------------------------------------------------

    def start_watching(self, interval: float = 0.4) -> None:
        if self._watcher is not None:
            return
        from .watch import ModWatcher

        def on_change(names: List[str]) -> None:
            try:
                self.reload(names or None, reason="file changed")
            except Exception as exc:  # noqa: BLE001
                self._log_error("automatic reload failed: %s" % describe_failure(exc))

        self._watcher = ModWatcher(self.mods_dir, on_change, interval=interval)
        self._watcher.start()
        self.log("watching %s for changes" % self.mods_dir)

    def stop_watching(self) -> None:
        watcher = self._watcher
        self._watcher = None
        if watcher is not None:
            watcher.stop()

