"""The bridge-backed session behind every façade object.

One :class:`Session` per connection. It owns the value wrapping rules (a wire ref becomes a ``Unit``,
a ``Skill``, ...), the optional index validation, and a per-callback cache so a hook that reads
``unit.hp`` five times does one round trip, not five.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

from ..runtime import protocol
from ..runtime.client import Bridge
from ..runtime.errors import BridgeError

__all__ = ["Session", "wrap", "wrap_many"]

_KIND_TO_FACTORY = {}


def register_wrapper(kind: str, factory) -> None:
    _KIND_TO_FACTORY[kind] = factory


def wrap(session: "Session", value: Any) -> Any:
    """Converts a wire value into a façade object when it is a typed reference."""
    if isinstance(value, dict):
        kind = value.get("t")
        if isinstance(kind, str) and "h" in value:
            factory = _KIND_TO_FACTORY.get(kind)
            if factory is not None:
                return factory(session, value)
        return {key: wrap(session, item) for key, item in value.items()}
    if isinstance(value, list):
        return [wrap(session, item) for item in value]
    return value


def wrap_many(session: "Session", values: Iterable[Any]) -> List[Any]:
    return [wrap(session, value) for value in (values or [])]


class Session:
    """Wraps a :class:`~limbuspy.runtime.client.Bridge` with game-aware helpers."""

    def __init__(self, bridge: Bridge, index=None, strict: Optional[bool] = None,
                 use_cache: bool = True) -> None:
        """``use_cache=False`` 用于**长生命周期**的会话（``Mod.session`` / ``Mod.ops``）。

        缓存只能活在"一次回调"里：``ctx.session`` 每次回调新建、回调结束即丢。挂在整个 mod
        生命周期上的会话如果缓存读值，第二回合读到的还是第一回合的 HP —— 而且不会报错。
        所以 hook 之外一律用无缓存会话（读到的是当次真值）。
        """
        self.bridge = bridge
        self.index = index
        #: When true, unknown member names raise instead of being sent to the kernel.
        self.strict = bool(os.environ.get("LIMBUSPY_STRICT")) if strict is None else bool(strict)
        self._use_cache = bool(use_cache)
        self._cache: Dict[tuple, Any] = {}
        self.warnings: List[str] = []

    # -- values ------------------------------------------------------------------------

    def get(self, ref: Any, member: str, timeout: float = 5.0) -> Any:
        key = ("get", _key(ref), member)
        if self._use_cache and key in self._cache:
            return self._cache[key]
        wire = to_wire(ref)
        value = self.bridge.request(
            protocol.MsgType.CALL,
            {"target": wire if isinstance(wire, dict) else "", "method": "reflect.get",
             "args": [wire, member]},
            timeout=timeout,
        ).get("value")
        wrapped = wrap(self, value)
        if self._use_cache:
            self._cache[key] = wrapped
        return wrapped

    def set(self, ref: Any, member: str, value: Any, timeout: float = 5.0) -> Any:
        if self._use_cache:
            self._cache.pop(("get", _key(ref), member), None)
        result = self.bridge.request(
            protocol.MsgType.CALL,
            {"target": "", "method": "reflect.set", "args": [to_wire(ref), member, to_wire(value)]},
            timeout=timeout,
        ).get("value")
        return wrap(self, result)

    def call(self, ref: Any, member: str, *args: Any, timeout: float = 5.0) -> Any:
        key = ("call", _key(ref), member, len(args))
        if self._use_cache and not args and key in self._cache:
            return self._cache[key]
        wire_args = [to_wire(arg) for arg in args]
        result = self.bridge.call("", "reflect.invoke", to_wire(ref), member, wire_args, timeout=timeout)
        wrapped = wrap(self, result)
        if self._use_cache and not args:
            self._cache[key] = wrapped
        return wrapped

    def static(self, type_and_method: str, *args: Any, timeout: float = 5.0) -> Any:
        return wrap(self, self.bridge.call("", type_and_method, *[to_wire(arg) for arg in args],
                                           timeout=timeout))

    # -- raw：类型化绑定层（limbuspy.api.bindings）专用 --------------------------------
    #
    # 这几个方法**不做** wrap()：类型化绑定自己按注解决定要不要把引用包装成绑定类。
    # 老门面（Unit/Buff/...）继续走上面的 get/set/call。

    def read(self, ref: Any, member: str, timeout: float = 5.0) -> Any:
        """读成员，返回**原始 wire 值**（引用仍是 ``{"t","h"}``）。"""
        key = ("raw", _key(ref), member)
        if self._use_cache and key in self._cache:
            return self._cache[key]
        wire = to_wire(ref)
        value = self.bridge.request(
            protocol.MsgType.CALL,
            {"target": wire if isinstance(wire, dict) else "", "method": "reflect.get",
             "args": [wire, member]},
            timeout=timeout,
        ).get("value")
        if self._use_cache and (isinstance(value, (dict, list, int, float, bool, str)) or value is None):
            self._cache[key] = value
        return value

    def write(self, ref: Any, member: str, value: Any, timeout: float = 5.0) -> Any:
        """写成员；返回内核回显的新值。"""
        if self._use_cache:
            self._cache.pop(("raw", _key(ref), member), None)
            self._cache.pop(("get", _key(ref), member), None)
        return self.bridge.request(
            protocol.MsgType.CALL,
            {"target": "", "method": "reflect.set",
             "args": [to_wire(ref), member, to_wire(value)]},
            timeout=timeout,
        ).get("value")

    def invoke(self, ref: Any, member: str, *args: Any, timeout: float = 5.0) -> Any:
        """调用方法，返回**原始 wire 值**；重载由内核按参数打分选择。"""
        wire_args = [to_wire(arg) for arg in args]
        return self.bridge.call("", "reflect.invoke", to_wire(ref), member, wire_args,
                                timeout=timeout)

    def read_many(self, pairs: Iterable, timeout: float = 5.0) -> List[Any]:
        """批量读 ``[(ref, member), ...]``，返回值与入参同序。

        内核广告 ``batch.v1`` 时是**一次往返**；否则退化成 N 次读（计数可见），
        调用方代码不变（见 docs/05-typed-api.md §5.2）。
        """
        items = [(to_wire(ref), member) for ref, member in pairs]
        if self.bridge.has_cap(protocol.Caps.BATCH_V1):
            result = self.bridge.request(
                protocol.MsgType.BATCH,
                {"ops": [[ref, "reflect.get", [ref, member]] for ref, member in items]},
                timeout=timeout,
            ).get("value")
            values = []
            for entry in result or []:
                values.append(entry.get("value") if isinstance(entry, dict) else entry)
            return values
        return [self.read(ref, member, timeout=timeout) for ref, member in items]

    def global_ref(self, name: str) -> Dict[str, Any]:
        """A ``global:Name`` handle that the kernel resolves to a singleton."""
        return self.bridge.call("", "reflect.get", "global:" + name, "__type__")

    # -- A 层封装 op --------------------------------------------------------------------

    def op(self, name: str, args: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Any:
        """调用一个 A 层 op（``unit.damage`` / ``battle.units`` …）。

        op 是内核侧的**命名参数**原生实现：没有反射、没有重载打分、没有成员名查找。
        内核不支持该 op 时抛 :class:`BridgeError`（code = ``unsupported``）。
        """
        op_call = getattr(self.bridge, "op_call", None)
        if op_call is not None:
            return op_call(name, dict(args or {}), timeout=timeout)
        result = self.bridge.request(
            protocol.MsgType.OPCALL,
            {"op": name, "args": dict(args or {})},
            timeout=timeout,
        )
        return result.get("value")

    def has_op(self, name: str) -> bool:
        """内核是否实现了这个 op（按 manifest 判断，不是靠试）。"""
        manifest = self.ops_manifest
        if not self.bridge.has_cap(protocol.Caps.OPS_V1):
            return False
        ops = manifest.get("ops")
        if not ops:
            return True  # 老内核只广告能力位，没有清单：按能力位放行
        if isinstance(ops, list):
            return any((entry.get("name") if isinstance(entry, dict) else entry) == name for entry in ops)
        return name in ops

    @property
    def ops_manifest(self) -> Dict[str, Any]:
        """内核广告的 A 层清单（HELLO 里的 ``ops``，可能为空）。"""
        hello = getattr(self.bridge, "hello", None) or {}
        manifest = hello.get("ops")
        return manifest if isinstance(manifest, dict) else {}

    @property
    def ops(self):
        """A 层入口：``session.ops.unit.damage(unit, 5)``。"""
        existing = getattr(self, "_ops_facade", None)
        if existing is not None:
            return existing
        from ..api.ops import Ops

        facade = Ops(self)
        self._ops_facade = facade
        return facade

    def snapshot(self, timeout: float = 5.0) -> dict:
        """One round trip describing the whole battle (see protocol §6.4)."""
        result = self.bridge.request(protocol.MsgType.SNAPSHOT, {}, timeout=timeout)
        return dict(result.get("value") or {})

    def coerce(self, value: Any, cls) -> Any:
        """Wraps ``value`` with ``cls`` when it is (a list of) typed references.

        The kernel's wire ``kind`` is only a hint: a type it does not classify arrives as ``obj``
        and gets wrapped as a generic ``Ref``. The caller knows the expected class from its member
        table, so references are re-labelled here - the façade therefore never depends on the
        kernel's best-effort classification.
        """
        if value is None:
            return None
        if isinstance(value, list):
            return [self.coerce(item, cls) for item in value]
        if isinstance(value, cls):
            return value
        # Already wrapped as something else (usually Ref): re-label from its target spec.
        target = getattr(value, "target", None)
        if target is not None:
            return cls(self, target)
        if isinstance(value, dict) and "h" in value:
            return cls(self, value)
        return value

    # -- validation --------------------------------------------------------------------

    def check_member(self, type_name: str, member: str) -> None:
        """Dev-time guard: an unknown member name is a typo, not a runtime surprise."""
        index = self.index
        if index is None or not index.has_type(type_name):
            return
        if index.has_member(type_name, member):
            return
        message = "%s has no member %r in this build" % (type_name, member)
        if self.strict:
            raise BridgeError(protocol.ErrorCode.NOT_FOUND, message)
        if message not in self.warnings:
            self.warnings.append(message)

    # -- cache -------------------------------------------------------------------------

    @property
    def use_cache(self) -> bool:
        return self._use_cache

    def refresh(self) -> None:
        self._cache.clear()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._cache.clear()


def _key(ref: Any) -> Any:
    if isinstance(ref, dict):
        return (ref.get("t"), ref.get("h"))
    target = getattr(ref, "target", None)
    if target is not None and isinstance(target, dict):
        return (target.get("t"), target.get("h"))
    return ref


def to_wire(value: Any) -> Any:
    """Normalises a façade object (or a list of them) back into its wire representation.

    Façade objects are ergonomic in Python but not encodable; every bridge call funnels through
    here so ``ctx.call(unit, ...)`` works as naturally as ``ctx.call(unit.wire, ...)``.
    """
    if value is None:
        return None
    target = getattr(value, "target", None)
    if target is not None and not isinstance(value, (dict, list, str, int, float, bool, bytes)):
        return target
    if isinstance(value, list):
        return [to_wire(item) for item in value]
    if isinstance(value, tuple):
        return [to_wire(item) for item in value]
    return value
