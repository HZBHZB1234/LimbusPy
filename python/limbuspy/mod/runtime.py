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
    import_mod,
    instantiate_mod,
    load_config,
    load_mod,
    prepare,
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


def _build_cond(event: dict, round_: int = 0):
    """为一个事件构造共享的求值上下文（``registration.Cond``）。

    要点：
    * payload 换成**类型化对象**，这样 ``ev.hp_damage`` 在谓词里能读；
    * ``mask_victim`` / ``mask_self`` 是 C# 侧预填的位图 —— 谓词判断 buff 只做一次按位与，
      **0 次跨进程往返**；
    * ``round`` 优先读 payload（内核给了就用它的），否则用运行时自己数的回合数 ——
      ``round_()`` / ``once_per_turn()`` / ``cooldown()`` 都靠它，且同样 0 往返。
    """
    from ..registration import Cond

    timing = event.get("timing") or ""
    raw = event.get("payload")
    payload: Any = raw
    try:
        from ..api.hooks import PAYLOAD_BY_TIMING

        payload_cls = PAYLOAD_BY_TIMING.get(timing)
        if payload_cls is not None and isinstance(raw, dict):
            payload = payload_cls.from_wire(raw)
    except Exception:  # noqa: BLE001 - 没有生成物时退化为原始 dict
        payload = raw

    data = raw if isinstance(raw, dict) else {}
    return Cond(
        payload=payload,
        mask_victim=int(data.get("mask_victim") or 0),
        mask_self=int(data.get("mask_self") or 0),
        round_=int(data.get("round") or round_ or 0),
    )


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
        #: 自己数的回合数（``RoundStarted`` 一次 +1，``BattleStarted`` 归零）。
        #: 供 ``round_()`` / ``once_per_turn()`` / ``cooldown()`` 用 —— 谓词求值不能有往返。
        self._round = 0

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

        # 把作者门面（``import limbuspy as lp``）接到这条连接上。门面自己不管连接，
        # 这样它才能在测试里被换成一个 mock bridge。
        try:
            from .. import facade

            facade.attach(self.bridge)
        except Exception as exc:  # noqa: BLE001 - 门面挂不上不该阻止 mod 加载
            self._log_error("facade attach failed: %s" % (describe_failure(exc),))

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
        try:
            from .. import facade

            facade.detach()
        except Exception:  # noqa: BLE001 - 关闭路径不许抛
            pass
        for name in list(self.mods):
            loaded = self.mods.get(name)
            if loaded is None:
                continue
            self._save_state(name, loaded)
            try:
                removed = loaded.instance.unpatch_all()
                if removed:
                    self.log("unpatched %d route(s) from %s" % (removed, name))
            except Exception as exc:  # noqa: BLE001
                self._log_error("unpatch failed for %s: %s" % (name, describe_failure(exc)))
            try:
                loaded.instance.on_unload()
            except Exception as exc:  # noqa: BLE001
                self._log_error("on_unload failed for %s: %s" % (name, describe_failure(exc)))

    # -- loading ------------------------------------------------------------------------

    def load_all(self) -> Dict[str, str]:
        self.load_errors.clear()
        try:
            prepared = prepare(self.mods_dir)
        except ManifestError as exc:
            self._log_error("mod discovery failed: %s" % exc)
            return dict(self.load_errors)

        for name, error in prepared.errors.items():
            self.load_errors[name] = error
            self._log_error("failed to import %s: %s" % (name, error))

        try:
            self.order = resolve_order([manifest for manifest, _module, _instance in prepared.imported])
        except ManifestError as exc:
            self._log_error("dependency resolution failed: %s" % exc)
            for manifest, _module, _instance in prepared.imported:
                unload_module(manifest)
            return dict(self.load_errors)

        by_name = {manifest.name: (manifest, module, instance)
                   for manifest, module, instance in prepared.imported}
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

            # 内容注册：先收集并分配位序号，再注册钩子（谓词要用到位序号）
            from .loader import collect_contents

            contents, problems = collect_contents(module, manifest.name)
            instance.contents = contents
            instance.registration_errors = list(instance.registration_errors or []) + problems

            gen = self.registry.generation(manifest.name)
            loaded = register_instance(instance, manifest.name, self.registry, gen, module=module)
            loaded.contents = contents
            loaded.module = module
            loaded.manifest = manifest
            self._restore_state(manifest.name, loaded)
            self.mods[manifest.name] = loaded
            self._publish_plan(loaded)
            try:
                loaded.instance.on_load()
            except Exception as exc:  # noqa: BLE001
                self._log_error("on_load failed for %s: %s" % (manifest.name, describe_failure(exc)))
            self.log("loaded %s %s (%d hook(s), %d content(s))"
                     % (manifest.name, loaded.instance.version, len(loaded.hooks),
                        len(loaded.contents)))
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
            # 重载也要重发内容计划：否则谓词的位图在第一次重载之后就永远是 0。
            self._publish_plan(loaded)
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
                    removed = loaded.instance.unpatch_all()
                    if removed:
                        self.log("unpatched %d route(s) from %s" % (removed, name))
                except Exception as exc:  # noqa: BLE001
                    self._log_error("unpatch failed for %s: %s" % (name, describe_failure(exc)))
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

    def _publish_plan(self, loaded) -> None:
        """把「这个 mod 关心哪些 buff、哪些时点要位图」告诉内核（``content.plan``）。

        这一步是"零往返 buff 判断"的前提：C# 侧按同一份 ``watch`` 顺序算位图，
        Python 侧只做按位与。老内核没有这个 op 时静默跳过 —— 谓词退化为恒假而不是报错。
        """
        self._inject_custom_buffs(loaded)

        try:
            from ..registration import plan_for

            plan = plan_for(loaded.hooks, loaded.contents)
            if not plan["watch"]:
                return
            payload = {"mod": loaded.manifest.name}
            payload.update(plan)
            self.bridge.op_call("content.plan", payload, timeout=2.0)
            self.counters["content_plans"] = self.counters.get("content_plans", 0) + 1
        except Exception as exc:  # noqa: BLE001 - 老内核/未实现：静默降级
            self._log_error("content.plan unavailable for %s: %s"
                            % (loaded.manifest.name, describe_failure(exc)))

    def _inject_custom_buffs(self, loaded) -> None:
        """给「没有复用官方 id」的 Buff 做真注入（``content.register``）。

        只处理 ``game_id == 0`` 的内容 —— 复用官方 Buff 的模组一行注入都不用做，
        这正是我们推荐的默认路线（见 docs/plans/02 §4.6）。
        """
        try:
            from ..registration import CUSTOM_BUFF_BASE_ID

            custom = [c for c in loaded.contents
                      if str(getattr(c, "kind", "")) == "buff" and not int(getattr(c, "game_id", 0) or 0)]
            if not custom:
                return

            payload = {
                "keywords": [c.keyword() for c in custom],
                "icons": [str(getattr(c, "icon", "") or "") for c in custom],
                "icon_root": "",
            }
            ok = self.bridge.op_call("content.register", payload, timeout=2.0)
            if ok:
                # C# 侧从同一基址顺序分配，所以这里镜像出 game_id ——
                # 两边都从 CUSTOM_BUFF_BASE_ID 起、按同一顺序、跳过已占用的值。
                for index, content in enumerate(custom):
                    content.game_id = CUSTOM_BUFF_BASE_ID + index
                self.counters["content_injected"] = self.counters.get("content_injected", 0) + len(custom)
        except Exception as exc:  # noqa: BLE001 - 注入不可用不该让 mod 起不来
            self._log_error("content.register unavailable for %s: %s"
                            % (loaded.manifest.name, describe_failure(exc)))

    def _track_round(self, event: dict) -> None:
        """维护一个**不花往返**的回合数。

        ``RoundStarted`` 的 payload 里没有回合号（它是"这个单位的新回合开始了"），
        所以这里自己数：一次 ``RoundStarted`` = 一个新回合，``BattleStarted`` 归零。
        这样 ``round_() >= 3`` / ``once_per_turn()`` / ``cooldown(n)`` 才真的能用。
        """
        timing = event.get("timing")
        if timing == "RoundStarted":
            self._round += 1
        elif timing == "BattleStarted":
            self._round = 0
        else:
            return
        payload = event.get("payload")
        if isinstance(payload, dict) and payload.get("round"):
            self._round = int(payload["round"])

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
            cond = None  # 惰性：没有任何 hook 声明 when 时，一个 Cond 都不建
            if not self._paused and isinstance(event, dict):
                self._track_round(event)
                for entry in self.registry.hooks_for(event.get("timing") or ""):
                    if not entry.matches(event):
                        continue
                    if over_budget:
                        self.counters["hooks_skipped"] += 1
                        continue

                    # 一个事件里 N 个被动只构造 1 个求值上下文（Cond）——
                    # 位图/回合这些公共输入读一次，N 个谓词复用。
                    if entry.when is not None:
                        if cond is None:
                            cond = _build_cond(event, self._round)
                        try:
                            if not entry.when(cond):
                                self.counters["hooks_skipped"] += 1
                                continue
                        except Exception as exc:  # noqa: BLE001 - 谓词出错不该炸整个批次
                            self.counters["hooks_skipped"] += 1
                            entry.last_error = describe_failure(exc)
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
            "round": self._round,
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

