"""LimbusPy 的类型化 API 运行时基座（手写，随包分发）。

设计约束（与 ``docs/05-typed-api.md`` 对应）：

* **生成的绑定类是真代码**：每个成员都是显式的 ``@property`` / ``def``，带完整注解与
  docstring；不用 ``__getattr__`` 伪属性，静态检查器（mypy / pyright）能完整推导。
* **wire 细节全部收敛在 ``BoundObject``**：绑定类只说"读 ``Hp``"、"调 ``Heal``"，
  引用 ``{"t","h"}``、枚举名、列表、空值的转换由基座负责。
* **逃生口是显式的**：``obj.raw.get("某成员")`` / ``obj.raw.call("某方法", ...)``。
  正常路径上不出现 IL2CPP 成员名字符串的手工拼接。
* **批量读取**：``select()`` 优先走一次往返（内核 ``batch.v1``），否则退化为 N 次，
  调用方代码不变。
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional, Protocol, Sequence, Tuple, Type, TypeVar

__all__ = [
    "BoundObject",
    "RawView",
    "UnsupportedReturn",
    "StaleHandle",
    "MissingCapability",
]

T = TypeVar("T", bound="BoundObject")


class UnsupportedReturn(RuntimeError):
    """成员存在，但返回值/参数类型无法过桥（struct、协程、泛型等）。"""


class StaleHandle(RuntimeError):
    """引用已失效（对象被回收、或 arena 作用域已结束）。"""


class MissingCapability(RuntimeError):
    """内核不支持该能力（例如没有 ``batch.v1`` 却要求批量读）。"""


class _SessionLike(Protocol):
    """绑定类只依赖这几个方法；实现见 ``limbuspy.game.session.Session``。"""

    def read(self, ref: Any, member: str) -> Any: ...

    def write(self, ref: Any, member: str, value: Any) -> Any: ...

    def invoke(self, ref: Any, member: str, *args: Any) -> Any: ...


# --------------------------------------------------------------------------------------
# 引用
# --------------------------------------------------------------------------------------


class BoundObject:
    """一个游戏对象的类型化视图。子类由 ``tools/gen_bindings.py`` 生成。"""

    __slots__ = ("_session", "_wire", "_data")

    #: 对应的 IL2CPP 类型名；生成的子类会覆盖它。
    __il2cpp_type__: ClassVar[str] = ""

    def __init__(self, session: Any, wire: Any, data: Optional[dict] = None) -> None:
        self._session = session
        self._wire = wire
        self._data = dict(data or {})

    # -- 身份 ---------------------------------------------------------------------------

    @property
    def session(self) -> Any:
        return self._session

    @property
    def wire(self) -> Any:
        """底层引用（``{"t","h"}`` / ``"global:Name"`` / ``"type:Ns.Type"``）。"""
        return dict(self._wire) if isinstance(self._wire, dict) else self._wire

    @property
    def target(self) -> Any:
        """``to_wire()`` 认的名字：绑定对象可以直接当参数传给另一个绑定调用。"""
        return self._wire

    @property
    def handle(self) -> int:
        return int(self._wire.get("h") or 0) if isinstance(self._wire, dict) else 0

    @property
    def data(self) -> Dict[str, Any]:
        """snapshot 附带的字段（可能为空）；**不要当实时值用**。"""
        return dict(self._data)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, BoundObject)
            and other.__il2cpp_type__ == self.__il2cpp_type__
            and other.handle == self.handle
        )

    def __hash__(self) -> int:
        return hash((self.__il2cpp_type__, self.handle))

    def __repr__(self) -> str:
        return "%s(#%d)" % (type(self).__name__, self.handle)

    # -- 逃生口 -------------------------------------------------------------------------

    @property
    def raw(self) -> "RawView":
        """显式的反射逃生口：生成的 API 没覆盖的成员从这里走。"""
        return RawView(self)

    # -- 绑定类内部用：读写/调用 --------------------------------------------------------

    def _read(self, member: str) -> Any:
        session = self._session
        read = getattr(session, "read", None)
        if read is not None:
            return read(self._wire, member)
        return session.get(self._wire, member)

    def _write(self, member: str, value: Any) -> Any:
        session = self._session
        write = getattr(session, "write", None)
        if write is not None:
            return write(self._wire, member, value)
        return session.set(self._wire, member, value)

    def _invoke(self, member: str, *args: Any) -> Any:
        session = self._session
        invoke = getattr(session, "invoke", None)
        if invoke is not None:
            return invoke(self._wire, member, *args)
        return session.call(self._wire, member, *args)

    # -- 绑定类内部用：值转换 -----------------------------------------------------------

    def _as(self, value: Any, cls: Optional[Type[T]]) -> Optional[T]:
        """被引用的对象 → 绑定类实例；``None`` 与已包装的值原样返回。"""
        if value is None or cls is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, dict) and "h" in value:
            return cls(self._session, value)
        return value

    def _as_list(self, values: Any, cls: Optional[Type[T]]) -> List[Any]:
        if not values:
            return []
        if not isinstance(values, (list, tuple)):
            return [values]
        return [self._as(item, cls) for item in values]

    def _as_dict(self, values: Any, cls: Optional[Type[T]] = None) -> Dict[Any, Any]:
        if not isinstance(values, dict):
            return {}
        if cls is None:
            return dict(values)

        def convert(item: Any) -> Any:
            if isinstance(item, (list, tuple)):
                return [self._as(entry, cls) for entry in item]
            return self._as(item, cls)

        return {key: convert(value) for key, value in values.items()}

    @staticmethod
    def _as_enum(value: Any, enum_cls: Any) -> Any:
        """内核按名回传枚举 → IntEnum；回传数字时按值构造。

        大枚举在生成时可能降级为"常量类"（没有 ``__members__``），这里一并兼容：
        常量类按 ``getattr(cls, name)`` 取数值。
        """
        if value is None or enum_cls is None:
            return value
        if isinstance(value, enum_cls):
            return value
        is_enum = hasattr(enum_cls, "__members__")
        if isinstance(value, str):
            if is_enum:
                try:
                    return enum_cls[value]
                except KeyError:
                    pass
            member = getattr(enum_cls, value, None)
            return member if member is not None else value
        if is_enum:
            try:
                return enum_cls(int(value))
            except (ValueError, TypeError):
                return value
        return value

    # -- 批量读取 -----------------------------------------------------------------------

    @classmethod
    def select(
        cls: Type[T],
        session: Any,
        refs: Sequence[Any],
        *members: str,
    ) -> List[Dict[str, Any]]:
        """一次往返读一批对象的若干成员（内核 ``batch.v1`` 能力）。

        ``refs`` 可以是绑定对象、wire 引用或 ``global:`` 说明；返回与 ``refs`` 同序的 dict 列表。
        单点失败不会让整批失败——失败位置的值是 ``None``，具体错误看内核 trace。
        """
        wires = [ref.target if isinstance(ref, BoundObject) else ref for ref in refs]
        pairs: List[Tuple[Any, str]] = [(wire, member) for wire in wires for member in members]
        read_many = getattr(session, "read_many", None)
        if read_many is not None:
            values = list(read_many(pairs))
        else:
            if len(pairs) > 8:
                raise MissingCapability(
                    "内核没有 batch.v1：%d 个字段读取会退化成 %d 次往返，"
                    "请升级内核或改用 snapshot/mirror" % (len(pairs), len(pairs))
                )
            values = [session.read(wire, member) for wire, member in pairs]

        rows: List[Dict[str, Any]] = [dict() for _ in refs]
        index = 0
        for row in rows:
            for member in members:
                row[member] = values[index]
                index += 1
        return rows


class RawView:
    """显式逃生口：名字就是 IL2CPP 成员名，没有类型信息，拼错在运行期才报错。"""

    __slots__ = ("_object",)

    def __init__(self, obj: BoundObject) -> None:
        self._object = obj

    def __repr__(self) -> str:  # pragma: no cover
        return "<raw %s>" % (type(self._object).__name__,)

    def get(self, member: str) -> Any:
        return self._object._read(member)

    def set(self, member: str, value: Any) -> Any:
        return self._object._write(member, value)

    def call(self, member: str, *args: Any) -> Any:
        return self._object._invoke(member, *args)

    def describe(self) -> Dict[str, Any]:
        """内核 ``reflect.describe``：这个类型在这个 build 里到底有什么成员。"""
        session = self._object._session
        bridge = getattr(session, "bridge", None)
        if bridge is None:
            raise MissingCapability("session 没有 bridge，describe 不可用")
        return dict(bridge.call("", "reflect.describe", self._object._wire) or {})
