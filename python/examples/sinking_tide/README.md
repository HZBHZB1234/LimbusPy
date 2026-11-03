# sinking_tide —— 「深水区」示例 mod

一个能完整跑起来的示例：把敌人身上的 **SINKING（沉溺）** 叠层，在合适的时机换成一次真实的
HP 损失，并把整场战斗记成一份可查询的报告。

它存在的目的是**演示组合**，不是演示语法糖。这个示例特意**走到底**：既用作者门面，
也用到门面覆盖不到的两层，从而把「门面到哪儿为止」画清楚。

| # | 通路 | 在这个 mod 里干什么 | 代码位置 |
|---|---|---|---|
| 1 | `@lp.on_hit` / `@lp.on_damage` | 命中记账；只收 `keyword == SINKING` 的伤害 | `on_hit` / `on_sinking_tick` |
| 2 | `lp.unit(...)` 快照 | 一次往返看清一个单位，之后 `hp` / `hp_ratio` 全是内存读 | `on_hit` / `_evaluate` |
| 3 | `lp.damage(...)` | 叠层换伤害（收的是"扣多少"，不是 `ChangeHp` 的"目标 HP 值"） | `_burst` |
| 4 | Harmony 补丁 | 监听**游戏自己的每一次** HP 变化（含非本 mod 造成的） | `on_hp_changed` |
| 5 | 同步泳道（阻塞） | `ChangeTakeDamage` 返回伤害倍率；硬预算 20 ms，只读内存 | `take_damage_multiplier` |
| 6 | 静态数据表 | 从 `BuffData` 校准叠层上限 | `_load_statics` |
| 7 | `config` / `on_save` / `on_restore` | **热重载（改文件即生效）不丢账本** | `cfg` / `on_save` |

**为什么 4 和 5 不在门面里**：它们不是"更长一点的 API"，而是**不同的成本模型**。
补丁跑在游戏方法内部（属于"改游戏行为"），同步泳道会阻塞游戏主线程等 Python 回包。
把它们混进 `lp.*` 会让"这里可能有坑"的信号消失。

## 文件

```
sinking_tide/
├─ __init__.py        ← mod 本体（约 370 行，含注释）
├─ mod.toml           ← 清单（name/version/requires）
└─ data/
   ├─ BuffData.json    ← 静态表示例（真实表用 `python -m limbuspy statics` 从 bundle 抽）
   └─ PassiveData.json ← 表格式样例（本 mod 目前只用 BuffData）
```

真实 mod 把这些放进 `BepInEx/plugins/LimbusPy/mods/<name>/`，改动存盘即在游戏里生效。

## 跑起来（不需要开游戏）

```powershell
# 完整演示：合成一场战斗 → 打印每次派发/报告/热重载结果
$env:PYTHONUTF8=1
python LimbusPy/python/examples/run_sinking_tide.py

# 只跑断言
python LimbusPy/python/examples/run_sinking_tide.py --quiet

# 回归测试（14 项）
python -m pytest LimbusPy/python/tests/test_example_mod.py -q
```

`run_sinking_tide.py` 复用 `api_harness.ApiHarness`：内核侧是 `limbuspy._mock.MockKernel`
（扮演 C# 侧，含 `PATCH_ADD` / `SYNCALL` / `OPCALL`），动作语义是 `MockOps`（契约的
Python 参考实现）—— 和真机走**同一套** LPCP 帧与请求关联。所以这里跑通 = Python 侧接线
（含补丁、同步泳道、热重载代际）是对的；剩下只需在真机上确认 IL2CPP 反射与 Unity 生命周期。

## 关键设计点（值得抄的）

1. **快照优先**：`lp.unit(...)` 每个 hook 只调一次，之后 `u.hp` / `u.hp_ratio` / `u.is_dead`
   零往返。需要 N 个单位的全景就用 `lp.battle()`（1 次批量），**不要**循环 `lp.unit(id)`。
2. **同步泳道里只读内存**：`take_damage_multiplier` 不碰桥。任何 `ctx.call` 或 op 调用都可能
   撞上 20 ms 预算然后落到 `fallback`，那会让伤害数字随机跳。
3. **别自己调 `ChangeHp`**：它的第一个参数是**目标 HP 值**而不是增量
   （游戏自己用 `ChangeHp(MaxHp, ...)` 回满血），还要补齐枚举与关键字哨兵。
   `lp.damage(target, amount)` 收的就是"扣多少"，语义在 C# 侧。
4. **枚举过桥传成员名**（`"PASSIVE"` / `"ON_SUCCESS_ATTACK"`），可选参数必须显式给全 ——
   内核按参数个数精确匹配重载，不会替脚本补默认值。
5. **补丁回调里禁止往返**：`on_hp_changed` 只 `append` 一个 dict。补丁是同步阻塞的，慢一点
   就是掉帧。
6. **`once_per_round` 是状态机**：`last_burst_round` 与账本一起持久化，热重载后不会在同一轮
   重复爆发。
7. **`dry_run` 是真开关**：只记账、不下手，用来验证判定条件而不改游戏状态。
8. **时点名用 A 层的**（`AttackSucceeded`，不是高级层的 `OnSucceedAttack`）。写错了回调
   一次都不会被调用，而且没有任何报错 —— 这条踩过。

## 真实的边界（别被示例骗了）

- **R7–R10 还没在真机上验过**（见 `docs/plans/04-implementation-status.md` §9）：
  自定义 Buff 的枚举注入能否被 `Enum.TryParse` 认出来、`content.register` 之后
  `add_buff("<自定义key>")` 是否施加成功、`define()` 的行能不能被游戏接受。
- **`is_player` 是启发式**（用 `GetOpponentFaction()` 反推），枚举语义若变由 `doctor` 报出。
- **`data/*.json` 是裁剪过的样例**，不是游戏真表；真表走 `python -m limbuspy statics`。
- **人格/技能/EGO 只到模板声明**，没有接管游戏加载器（写错会崩选人界面，没游戏无法验证）。

## 改成你自己的 mod

1. 复制 `sinking_tide/` 到 `BepInEx/plugins/LimbusPy/mods/`，改名 + 改 `mod.toml`。
2. `SinkingTide` 类名随意，但必须是该模块里**最派生**的 `Mod` 子类（加载器按它选入口）。
   不写 `Mod` 子类也能跑（`hello_passive` 就是纯模块级写法），只是没有 `config` / `self.state`。
3. 先只留 `@lp.on_*` 跑通，再加补丁与同步泳道 —— 后两者的成本模型完全不同。
4. 用 `python -m limbuspy doctor` 校验成员名，用 `python -m limbuspy repl` 连运行中的内核
   直接调 `sinking_tide.report()` 看现场（REPL 里 `lp` 已经挂好了）。
