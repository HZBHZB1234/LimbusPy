"""LimbusPy 类型化 API。

三层结构（详见 ``docs/05-typed-api.md``）：

1. **门面层** ``limbuspy.facade`` —— 模组作者真正用的那一层（``import limbuspy as lp``）。
   值对象 + 动作函数 + 事件注册，往返发生在"获取"时，读属性 0 往返。

2. **契约层** ``limbuspy.api.ops`` / ``limbuspy.api.hooks`` —— 由 ``tools/gen_contract.py``
   从 ``ops/spec/*.toml`` 生成的类型化封装：一次调用 = 一次 crossing，语义在 C# 侧。

3. **绑定层** ``limbuspy.api.bindings`` —— 1:1 反射绑定（5000+ 成员，2 MB），
   是"什么都能调"的保底。**它是惰性导入的**：不主动碰就不会付那 2 MB 的代价。
   没生成过时会给出生成命令，而不是让整个包 import 失败。
"""

from __future__ import annotations

from .base import (
    BoundObject,
    MissingCapability,
    RawView,
    StaleHandle,
    UnsupportedReturn,
)

__all__ = [
    "BoundObject",
    "RawView",
    "UnsupportedReturn",
    "StaleHandle",
    "MissingCapability",
    "bindings",
    "load_bindings",
]


def load_bindings():
    """导入（必要时报错）生成好的 1:1 绑定模块。

    为什么不让 ``api/__init__.py`` 直接 ``from . import bindings``：那会让任何
    ``import limbuspy.api.types`` 都顺带解析 2 MB 的生成代码（约 0.6 秒、140 个模块）。
    绝大多数模组一行反射都不写，不该付这个成本。

    Returns:
        ``limbuspy.api.bindings`` 模块。

    Raises:
        ImportError: 还没生成过绑定（提示信息里给命令）。
    """
    # 必须用 importlib 而不是 ``from . import bindings``：后者会对包对象做一次
    # ``getattr(pkg, "bindings")``，又掉回下面的 __getattr__，形成无限递归。
    import importlib

    module = importlib.import_module(".bindings", __name__)
    globals()["bindings"] = module
    return module


def __getattr__(name: str):
    """让 ``from limbuspy.api import bindings`` 仍然可用，但只在真的用到时才导入。"""
    if name == "bindings":
        module = load_bindings()
        globals()["bindings"] = module
        return module
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
