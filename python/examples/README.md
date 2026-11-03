# examples

放**能跑**的完整示例。和 `python/tests/` 的区别：测试验证**部件**，示例验证**组合** ——
一个示例必须同时让多条通路协同工作，否则它就没有存在价值。

全部示例都不需要开游戏、不需要 C#：内核角色由 `MockKernel` 扮演，动作语义由 `MockOps`
（契约的 Python 参考实现）扮演 —— 但走的是**真实 LPCP 通路**，所以 mod 里写的每一行都
跑在真机上会走的那条路径上。同时扮演 C# 侧补上 `mask_victim` / `mask_self` / `round`，
于是 `lp.victim.has(...)` 这类谓词在无游戏环境下也是真的在跑同一套逻辑。

## 怎么跑

```bash
python python/examples/run_examples.py              # 全部示例 + 成本实测
python python/examples/run_examples.py --quiet      # 只打印失败项与汇总
python python/examples/run_examples.py --only drowning_tide
python python/examples/run_sinking_tide.py          # 深水区示例（单独驱动器）
```

## 从哪读起

按这个顺序读，一个问题只占一个示例：

| 目录 | 说明 | 它证明了什么 | 往返次数 |
|---|---|---|---|
| `hello_passive/` | 十行的 Hello World：模块顶层 `@lp.on_hit`，没有 `Mod` 类 | 门面的入口有多薄 | 事件 0 + 1 次写 |
| `damage_ledger/` | 记录全场伤害/死亡/崩溃 | **只读事件**能完全零往返 | 0 |
| `field_medic/` | 回合开始按血量治疗 | 读一次 + 写一次 = 2 次 | 2 / 单位 |
| `burst_on_hit/` | 叠层到阈值 → 一次 `lp.burst` | 复合动作必然在同一个时间片 | 1（整笔结算） |
| `drowning_tide/` | 注册 Buff + 被动，条件 `victim.has(Buff) & once_per_round()` | **零往返条件**：位图在 C# 算，Python 只做按位与 | 谓词 0 / 结算 1 |
| `shield_warden/` | 重击后补盾、抢速、反打 | 条件写在装饰器里，不满足时函数体不跑 | 每次触发 3 |
| `battle_scribe/` | 每回合最多读一次全场，事件全部记账 | **快照经济学**：获取花钱，读属性免费 | 10 个事件 0 / 一次报告 2 |
| `sinking_tide/` | 沉溺叠层 → 伤害 + 战斗报告 | 门面覆盖不到的两层（Harmony 补丁、同步泳道）+ 静态表 + 热重载 | 见该目录 README |

## 三条公理，示例里都能实测

1. **往返发生在「获取」的时候，不发生在「读属性」的时候。**
   `lp.unit(...)` 之后读多少属性都是 0 次 —— `run_examples.py` 的 `contrast` 场景用
   `frames_sent` 把「门面 1 次拿 6 个字段」和「反射 4 个字段 = 4 次」摆在一起。
2. **不需要跨进程的判断，就不该跨进程。**
   `drowning_tide` 的 `gated_op_calls` 断言：条件命中时只花结算的 1 次，判断本身是 0 次。
3. **写操作必然要过桥。** 游戏状态只能由游戏线程改，所以 `lp.damage` / `lp.heal` /
   `lp.add_shield` 每次都是 1 次往返 —— 这条没法也不该隐藏，示例把它写进注释。

## 文件

| 文件 | 作用 |
|---|---|
| `api_harness.py` | 公共驱动器：mock 战斗 + mock 内核 + 位图/回合补全 + `attack` / `emit` / `start_round` |
| `run_examples.py` | 统一入口：9 个场景（8 个示例 + A/B 成本对照 + 全动作可达性），62 项断言 |
| `run_sinking_tide.py` | 深水区示例的驱动器（补丁、同步泳道、热重载） |

## 它们同时是回归测试

- `python/tests/test_examples.py` 把 `run_examples.SCENARIOS` 全部跑一遍，另外单独断言
  机制层的东西（模块级注册、位图回填、字符串关键字的位序号、热重载后内容计划仍在、
  `lp.unit(ev.target)` 的句柄形态）。
- `python/tests/test_example_mod.py` 把 `sinking_tide` 当 mod 加载，断言爆发数值、轮次门控、
  `dry_run`、补丁观测、同步倍率、报告可过桥、热重载不丢账本。

示例代码改坏了，CI 会红。
