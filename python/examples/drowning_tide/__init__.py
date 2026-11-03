"""示例 4：``drowning_tide`` —— 注册层（Buff / 被动 / 零往返条件）。

这是问题 2 的正面答案：**写一个类就是注册一个内容**，条件用声明式谓词写，
而谓词的求值成本是「一次整数按位与」。

它做三件事：

1. 注册一个自定义 Buff（``Drowning`` / 溺水）；
2. 注册一个被动（``TideCrush`` / 潮涌），条件写成人话：

   ``when=lp.victim.has(Drowning) & lp.once_per_round()``

   ——「受击者身上有溺水，且本回合还没触发过」。
3. 在报告里展示 ``define()``：这个 Buff 会被生成成 ``BuffStaticData`` 的一整行
   （字段名逐字来自游戏导出，不是猜的）。

为什么这个条件**不花钱**：

* 注册时本 mod 把「关心哪些 Buff」发给了内核（``content.plan``）；
* 之后 C# 在补丁回调里把「受击者身上有没有这些 Buff」算成一个整数位图，
  直接塞进 payload 的 ``mask_victim``；
* Python 侧 ``victim.has(Drowning)`` 只是 ``bool(mask & (1 << bit))``。

于是「只在受击者有 X 时才触发」从"每个事件都要回查一次游戏状态"变成了 0 次往返。
这正是 Lethe 时代做不到、而 Modular 用户要靠 Lua 硬编码 id 才能近似的事。

需要跨进程才能判断的谓词会在**注册期**被拒绝（``PredicateCostError``），
而不是在真机上每帧偷偷多跑几次 IPC。
"""

from __future__ import annotations

from typing import Any, Dict

import limbuspy as lp
from limbuspy.mod import Mod, config, expose


# ======================================================================================
# 1. 自定义 Buff：类体即声明
# ======================================================================================
class Drowning(lp.ContentBuff):
    """溺水：每层让「潮涌」多打一点，最多 10 层。

    类体里的字段就是它的静态数据（不需要 ``__init__``，也不需要手工填 id）。
    ``game_id`` 留 0 = "这个 Buff 由本 mod 独占" → 内核会用
    ``CUSTOM_BUFF_BASE_ID``（24576）起分配真枚举值并注入游戏。
    """

    local_id = "drowning"
    key = "DROWNING"
    name = "溺水"
    max_stacks = 10
    max_turns = 5
    buff_class = "Negative"     # BUFF_CLASS：负面
    buff_type = "Neutral"
    attribute = "GLOOM"         # 罪孽属性：忧郁
    can_be_despelled = True


# ======================================================================================
# 2. 被动：一组带条件的时点回调
# ======================================================================================
class TideCrush(lp.Passive):
    """潮涌：受击者身上有溺水时，把叠层换成一次真实伤害。

    被动不是第二套触发机制 —— 它就是「内容的身份 + 若干个 ``@lp.on_*`` 处理器」，
    所以它和普通钩子走同一条 dispatch 路径。

    账本放在**类属性**上：loader 只会实例化这个类一次，而 ``@expose`` 在另一个对象上，
    类属性是两个对象都能读到的位置。

    > 注意：类属性**不会**跨热重载保留（重载会重新 import 模块，类是新造的）。
    > 真实 mod 要把账本放 ``self.state``（loader 会持久化）——
    > ``sinking_tide`` 示例演示的是那套写法；这里为了保持"一个文件讲一件事"才简化。
    """

    local_id = "tide_crush"
    name = "潮涌"
    #: 想复用某个官方被动就填它的 id（配合 ``attach()`` 直接挂到单位身上）
    passive_id = 0

    crushes = 0
    total_damage = 0

    @lp.on_hit(when=lp.victim.has(Drowning) & lp.once_per_round())
    def crush(self, ev) -> None:
        """受击者溺水 → 叠层换伤害。

        谓词已经在 C# 侧判过了，所以这个函数体只有**真正要动手**的时候才会跑。
        这里 ``lp.burst`` 是 1 次往返（复合动作）。
        """
        if ev.target is None:                     # 防御性：没有受击者就没有叠层
            return
        result = lp.burst(ev.target, Drowning.keyword(), per_stack=3, max_stacks=6)
        if not result.ok:
            return
        type(self).crushes += 1
        type(self).total_damage += result.damage


# ======================================================================================
# 3. mod 本体：清单 + 报告
# ======================================================================================
class DrowningTide(Mod):
    name = "drowning_tide"
    version = "1.0.0"
    description = "注册一个 Buff + 一个被动，用零往返条件把它们连起来"

    cfg = config(
        per_stack=3,
        max_stacks=6,
        verbose=True,
    )

    @expose("drowning.report")
    def report(self, ctx) -> Dict[str, Any]:
        """注册结果 + 结算账本（纯内存，0 次往返）。"""
        row = Drowning.define()
        return {
            "mod": self.name,
            "buffs": [Drowning.describe()],
            "passives": [TideCrush.describe()],
            "drowning": {
                "keyword": Drowning.keyword(),
                #: 位序号：谓词就是判断这一位
                "bit": int(Drowning.bit),
                #: 内核分配的真枚举值（content.register 之后回填）
                "game_id": int(Drowning.game_id),
                "row_fields": len(row),
                "row": row,
            },
            "tide_crush": {"crushes": TideCrush.crushes, "damage": TideCrush.total_damage},
            "config": self.cfg.as_dict(),
        }


__all__ = ["Drowning", "TideCrush", "DrowningTide"]
