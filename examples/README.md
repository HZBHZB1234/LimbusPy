# examples

放"能跑"的完整示例。和 `python/tests/` 的区别：测试验证**部件**，示例验证**组合**——
一个示例必须同时让多条通路协同工作，否则它就没有存在价值。

| 目录 | 说明 | 怎么跑 |
|---|---|---|
| `sinking_tide/` | 战斗 mod：沉溺叠层 → 真实伤害 + 战斗报告。用上 hook / 快照 / 反射 / Harmony 补丁 / 同步泳道 / 静态表 / 配置 / 热重载 | `python examples/run_sinking_tide.py`（见该目录 `README.md`） |
| `run_sinking_tide.py` | 上面那个示例的驱动器 + "游戏形状"替身（真实成员名 + mock kernel）。测试复用它的替身 | `python examples/run_sinking_tide.py --quiet` |

示例同时是**回归测试**：`python/tests/test_example_mod.py` 把 `sinking_tide` 当 mod 加载，
断言爆发数值、轮次门控、`dry_run`、补丁观测、同步倍率、报告可过桥、热重载不丢账本。
示例代码改坏了，CI 会红。
