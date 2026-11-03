"""示例 0：``hello_passive`` —— 十行的 Hello World。

整个 mod 就是下面这十行。没有 ``Mod`` 子类、没有 ``config``、没有 manifest、
没有 session / bridge / handle / ctx —— 这正是「作者看不到交互层」的样子。

它做的事：每次命中结算之后，对被击者再追加 3 点真实伤害。

三个值得注意的点：

1. ``@lp.on_hit`` 直接写在**模块顶层**。门面会把这个函数登记成 ``AttackSucceeded``
   的回调，回调参数是**归一化的 payload**（``ev.target`` / ``ev.hp_damage`` 有补全），
   不是游戏方法的参数数组。
2. ``lp.damage(ev.target, 3)`` —— 不用把"扣血"翻译成 ``ChangeHp`` 的
   「目标 HP 值 + 来源枚举 + 时机枚举 + 关键字哨兵」。那套语义在 C# 侧。
3. 成本：``ev.*`` 全部 0 次往返（C# 在补丁回调里已经装好），
   ``lp.damage`` 是 1 次（写操作必然是一次往返）。
"""

from __future__ import annotations

import limbuspy as lp

#: 每次命中追加的伤害（真想改就改这个常量）。
EXTRA_DAMAGE = 3


@lp.on_hit
def punish(ev) -> None:
    """命中之后追加伤害。

    Args:
        ev: ``AttackSucceededPayload`` —— ``ev.unit`` 是攻击方，``ev.target`` 是受击方，
            两者都是单位引用（想读数值就 ``lp.unit(ev.target)``，一次往返拿全）。
    """
    if ev.target is None:            # 有些命中没有受击者（AOE 打空气、反击落空）
        return
    lp.damage(ev.target, EXTRA_DAMAGE)


__all__ = ["EXTRA_DAMAGE", "punish"]
