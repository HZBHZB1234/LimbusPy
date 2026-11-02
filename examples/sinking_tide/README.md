# sinking_tide —— "较复杂"的示例 mod

一个能完整跑起来的示例：把敌人身上的 **SINKING（沉溺）** 叠层，在合适的时机换成一次真实的 HP
损失，并把整场战斗记成一份可查询的报告。

它存在的目的是**演示组合**，不是演示语法糖。六条主通路同时用上：

| # | 通路 | 在这个 mod 里干什么 | 代码位置 |
|---|---|---|---|
| 1 | `@hook(时机)` 批量异步 | 命中记账、吃 buff 时点亮"潮汐已就绪" | `on_hit` / `on_sinking_gained` |
| 2 | `battle` 快照（1 次往返） | 一次看清全场，之后 `hp`/`instance_id` 都是内存读 | `_evaluate` |
| 3 | 反射调用 | `ChangeHp` 扣血、`AddPassive` 施加被动（真实签名写在注释里） | `_deal_damage` / `_add_passive` |
| 4 | Harmony 补丁 | 监听**游戏自己的每一次** HP 变化（含非本 mod 造成的） | `on_hp_changed` |
| 5 | 同步泳道（阻塞） | `ChangeTakeDamage` 返回伤害倍率；硬预算 20 ms，只读内存 | `take_damage_multiplier` |
| 6 | 静态数据表 | 从 `BuffData`/`PassiveData` 校准叠层上限、解析被动名 | `_load_statics` |

外加 `cfg = config(...)`、`on_save`/`on_restore`：**热重载（改文件即生效）不丢账本**。

## 文件

```
sinking_tide/
├─ __init__.py        ← mod 本体（约 380 行，含注释）
├─ mod.toml           ← 清单（name/version/requires）
└─ data/
   ├─ BuffData.json    ← 静态表示例（真实表用 `python -m limbuspy statics` 从 bundle 抽）
   └─ PassiveData.json
```

真实 mod 把这些放进 `BepInEx/plugins/LimbusPy/mods/<name>/`，改动存盘即在游戏里生效。

## 跑起来（不需要开游戏）

```powershell
# 完整演示：合成一场战斗 → 打印每次派发/报告/热重载结果
$env:PYTHONUTF8=1
python examples/run_sinking_tide.py

# 只跑断言
python examples/run_sinking_tide.py --quiet

# 回归测试
python -m pytest python/tests/test_example_mod.py -q
```

`run_sinking_tide.py` 里的替身用的是**真实成员名**（`Hp` / `MaxHp` / `InstanceID` /
`GetActivatedBuffModels` / `GetCurrentStack` / `ChangeHp` / `AddPassive` / `OnChangeHp`），
内核侧就是 `limbuspy._mock.MockKernel` —— 和真机走**同一套** LPCP 帧、请求关联、`SNAPSHOT`、
`PATCH_ADD`、`SYNCALL` 通路。所以这里跑通 = Python 侧接线（含补丁与热重载代际）是对的。

## 关键设计点（值得抄的）

1. **快照优先**：`gb.current(ctx.session)` 每个 hook 只调一次，之后 `unit.hp`/`unit.hp_ratio`
   零往返；需要调游戏的成员（`unit.buffs`）按会话缓存。
2. **同步泳道里只读内存**：`take_damage_multiplier` 不碰桥。任何 `ctx.call/get` 都可能撞上
   20 ms 预算然后落到 `fallback`，那会让伤害数字随机跳。
3. **`ChangeHp` 的 `value` 是"目标 HP"，不是增量**：游戏自己也用 `ChangeHp(MaxHp, ...)` 回满血。
   扣血要传 `old_hp - damage`（依据：`ChangeStat.ChangeHp(int newHp, ...)`）。
4. **枚举过桥传成员名**（`"PASSIVE"` / `"ON_SUCCESS_ATTACK"`），可选参数必须显式给全 ——
   内核按参数个数精确匹配重载，不会替脚本补默认值。
5. **补丁回调里禁止往返**：`on_hp_changed` 只 `append` 一个 dict。补丁是同步阻塞的，慢一点
   就是掉帧。
6. **`once_per_round` 是状态机**：`last_burst_round` 与账本一起持久化，热重载后不会在同一轮
   重复爆发。
7. **`dry_run` 是真开关**：只记账、不下手，用来验证判定条件而不改游戏状态。

## 目前的真实边界（别被示例骗了）

- **`@hook(时机)` 在真机上还没有事件源**：C# 内核已实现 `@expose`（`SYNCALL`）、反射、`SNAPSHOT`、
  Harmony 补丁派发，但**还没有把游戏的战斗时机采集成 `EVENTBATCH`**。所以：
  - 现在真机可用：补丁回调（如 `on_hp_changed`）、`@expose`（REPL / 其它 mod / 未来的 Modular `py()`）、
    反射读写、快照、热重载、静态表与资产（纯 Python）。
  - 现在真机不可用：`@hook("OnSucceedAttack")` 这类**时机驱动**的回调（本示例的 `on_hit` 就在这一层）。
    它们只在 mock kernel / C# harness 上跑通。
  - 事件源设计的落点已经很清楚：`BattleUnitModel.OnSucceedAttack(action, coin, target, finalDmg,
    realDmg, realHpDmg, isCritical, isWinDuel, timing)`、`OnChangeHp(...)`、`OnDie(...)`、
    `OnBreak(...)`、`OnRollOneCoin_AfterAttack(...)` 等是**中心通知方法**，Modular 的 205 KB 补丁
    正是在这些点上挂时机；我们只需在自己这边挂同样的一小组点，本地攒批、每帧发一次 `EVENTBATCH`，
    完全不依赖 Modular。
- **`is_player` 是启发式**（用 `GetOpponentFaction()` 反推），枚举语义若变由 `doctor` 报出。
- **`data/*.json` 是裁剪过的样例**，不是游戏真表；真表走 `python -m limbuspy statics`。

## 改成你自己的 mod

1. 复制 `sinking_tide/` 到 `BepInEx/plugins/LimbusPy/mods/`，改名 + 改 `mod.toml`。
2. `SinkingTide` 类名随意，但必须是该模块里**最派生**的 `Mod` 子类（加载器按它选入口）。
3. 先只留 `@expose` 与补丁那两条通路跑通，再开 `@hook`。
4. 用 `python -m limbuspy doctor` 校验成员名，用 `python -m limbuspy repl` 连运行中的内核直接调
   `sinking_tide.report()` 看现场。
