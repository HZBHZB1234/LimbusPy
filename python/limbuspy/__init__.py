"""LimbusPy - a Python scripting layer for Limbus Company, bridged to the game by a thin
BepInEx kernel over a named pipe (LPCP).

Import layout（对模组作者来说只有第一层是必须认识的）::

    import limbuspy as lp          # ← 门面：函数 + 值对象 + 事件，教程里只有它
    lp.unit(201).hp                # 一次往返拿快照，之后属性 0 往返
    @lp.on_hit                     # 注册一个时点回调

    limbuspy.mod         Mod 基类、hook/装饰器、热重载（有状态时才需要）
    limbuspy.api         契约层（生成的类型化封装）与惰性绑定
    limbuspy.game        底层门面（session / battle / statics / assets）
    limbuspy.runtime     传输、成帧、编解码、句柄、客户端
    limbuspy.devkit      scaffold / doctor / repl / pack

门面是**惰性**的：``import limbuspy`` 不解析 2 MB 的反射绑定，也不建连接。
"""

from __future__ import annotations

from . import facade
from .facade import (
    ActionResult,
    Battle,
    Buff,
    BuffInfo,
    BurstInfo,
    BurstResult,
    Status,
    Unit,
    UnitInfo,
    UnitState,
    add_buff,
    add_passive,
    add_shield,
    battle,
    break_unit,
    buff_stacks,
    burst,
    damage,
    heal,
    on_break,
    on_damage,
    on_damage_taken,
    on_death,
    on_hit,
    on_round_start,
    on_turn_start,
    player,
    refresh_speed,
    round,
    set_hp,
    set_sp,
    set_speed,
    status,
    unit,
    units,
)

__version__ = "0.1.0"
__protocol__ = "1.0"

# 内容注册层（问题 2 的正面解）：Buff / Passive 基类 + 声明式谓词。
# 它们是 `import limbuspy as lp` 之后直接可用的名字（例如 `lp.victim.has`）。
from .registration import (  # noqa: E402  (放在 __version__ 之后便于阅读)
    Buff as ContentBuff,
)
from .registration import (  # noqa: E402
    Content,
    Cond,
    Ego,
    Identity,
    Passive,
    Skill,
    Pred,
    PredicateCostError,
    RegistrationError,
    all_of,
    any_of,
    chance,
    cooldown,
    ev,
    none_of,
    once_per_round,
    once_per_turn,
    round_,
    self_unit,
    victim,
)

__all__ = [
    "__version__",
    "__protocol__",
    # 读
    "status", "battle", "units", "unit", "player", "round", "buff_stacks",
    # 写
    "damage", "heal", "set_hp", "set_sp", "add_shield", "set_speed",
    "refresh_speed", "break_unit", "add_buff", "add_passive", "burst",
    # 事件
    "on_hit", "on_damage", "on_damage_taken", "on_death", "on_break",
    "on_turn_start", "on_round_start",
    # 值对象
    "Status", "Battle", "Unit", "Buff", "ActionResult", "BurstResult",
    "UnitInfo", "UnitState", "BuffInfo", "BurstInfo",
    # 内容注册
    "Content", "ContentBuff", "Passive", "Identity", "Skill", "Ego", "Pred", "Cond",
    "victim", "self_unit", "ev",
    "once_per_turn", "once_per_round", "cooldown", "chance",
    "all_of", "any_of", "none_of", "round_",
    "RegistrationError", "PredicateCostError",
    # 子模块（按需导入）
    "facade",
]


def __getattr__(name: str):
    """按需暴露子模块，避免 ``import limbuspy`` 顺带拉起运行时/绑定。

    例如 ``lp.mod`` / ``lp.game`` / ``lp.runtime`` 只在真的用到时才导入。
    """
    if name in ("mod", "game", "api", "runtime", "devkit", "modular", "staticdata", "registration"):
        import importlib

        module = importlib.import_module("." + name, __name__)
        globals()[name] = module
        return module
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
