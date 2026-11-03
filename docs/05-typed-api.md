# 双层 API：封装层（A）与高级层（B）

> 状态：**A 层已实现并有可运行产物**；B 层在此前原型上补齐了共享反射路由与批量读。
> 本文是设计 + 现状，不是计划书：所有 ✅ 都有对应代码与可复现命令。

LimbusPy 对外只有**两种** API。它们不是"简单版 / 困难版"，而是**分工不同的两层**：

| | A 层：封装 API | B 层：高级自定义 API |
|---|---|---|
| 谁写 | 框架作者（一份契约源，三向生成） | mod 作者 |
| 游戏知识在谁那里 | **C# 原生实现里** | mod 作者脑子里 |
| 典型写法 | `ctx.ops.unit.damage(unit, 18)` | `unit.call("ChangeHp", hp-18, "PASSIVE", ...)` |
| 单次成本 | 1 次 crossing，**0 反射** | 1 次 crossing（可批量），每次反射 |
| 参数 | 领域参数 + 命名参数 | 完整 C# 签名 |
| 失败模式 | 参数校验/前置条件 → 明确报错 | 名字/签名/重载选错 → `notfound` / `marshal` |
| 覆盖 | 常用 hook 与调用（可增长） | 全量 IL2CPP 表面 |
| 适合 | 90% 的 mod 逻辑 | 调试、冷门类型、完全控制 |

**判断标准（一句话）**：写 mod 时**不该出现游戏成员名字符串**；出现了就是 B 层，应当有理由。

---

## 1. A 层：契约驱动、C# 原生、Python 类型化

### 1.1 单一契约源

`ops/spec/api.toml` 是唯一真源。`tools/gen_contract.py` 从它生成四份产物：

```
ops/spec/api.toml
  ├─ python/limbuspy/api/ops.py                类型化封装（真类 + docstring + 默认值）
  ├─ python/limbuspy/api/hooks.py              时机 payload dataclass + on() 的 overload
  ├─ kernel/LimbusPy.Ops/OpsManifest.g.cs      C# 契约清单（启动时与实现比对）
  └─ python/limbuspy/api/_contract_manifest.json  运行时协商 + 测试比对
```

命令：

```powershell
python tools/gen_contract.py          # 生成
python tools/gen_contract.py --check  # CI 门禁：产物是否最新
```

**为什么要这么绕**：手写两遍必然漂移。契约源保证 "Python 看到的签名" 与 "C# 实现的东西"
是同一个东西；`OpsEntry` 构造时还会做一次 `清单 ↔ 注册表` 自检（缺实现直接记进 problems）。

### 1.2 Python 侧长什么样（生成物，静态可查）

```python
# python/limbuspy/api/ops.py（生成）
class UnitOps:
    def damage(self, unit: UnitRefLike, amount: int,
               source: str = "PASSIVE", timing: str = "ON_SUCCESS_ATTACK") -> UnitInfo:
        """对单位造成伤害（内部处理 ChangeHp 的语义与枚举）。

        **这是打伤害的唯一推荐入口。** ChangeHp 的 value 是「目标 HP 值」而不是增量……
        """
        return UnitInfo.from_wire(self._session.op('unit.damage', {...}))
```

* 真类 + 真注解：mypy / pyright / IDE 补全直接工作；
* 默认值来自 spec（`source="PASSIVE"`），调用方不用背枚举名；
* 返回值是 `UnitInfo`（frozen dataclass，`.hp` / `.hp_ratio` 有补全），不是裸 dict。

### 1.3 时机：payload 归一化，`@on("Timing")` 的类型可推导

```python
from limbuspy.api.hooks import AttackSucceededPayload, on

@on("AttackSucceeded")
def on_attack(self, ev: AttackSucceededPayload, ctx) -> None:   # payload 类型由 overload 推导
    if ev.target is None:
        return
    # hook 里用 ctx.ops / ctx.session（绑本次回调）；self.ops 留给 on_load 等无 ctx 场景
    stacks = ctx.ops.unit.buff_stacks(ev.target, "SINKING")     # 1 crossing
    if stacks >= 6:
        self.ops.unit.burst_by_buff(ev.target, "SINKING", per_stack=3, max_stacks=8)
```

* 回调签名两种都可以：`(self, ev)` 或 `(self, ev, ctx)` —— 需要 `ctx.ops` / `ctx.session`
  时加第三个参数（`inspect` 判断，不需要额外装饰器）；
* payload 字段来自 spec，C# 侧在补丁回调里**只做拆包**（O(1)，不阻塞主线程），帧末一次
  `EVENTBATCH` 推过来；
* 于是"只读事件里已有信息"的 mod 是 **0 crossing**（见示例 `damage_ledger`）。

当前 A 层时机（5 个，全部对着 `dump.cs` 验证过签名）：

| timing | IL2CPP 挂点 | payload |
|---|---|---|
| `DamageDealt` | `BattleUnitModel.OnChangeHp` | unit / attacker / old_hp / new_hp / source / timing / keyword |
| `AttackSucceeded` | `BattleUnitModel.OnSucceedAttack` | unit / target / final_damage / real_damage / hp_damage / is_critical / timing |
| `UnitDied` | `BattleUnitModel.OnDie` | unit / killer / source / timing |
| `UnitBroken` | `BattleUnitModel.OnBreak` | unit / attacker / is_forced / timing |
| `TurnStarted` | `BattleUnitModel.OnStartTurn_AfterLog` | unit / timing |

### 1.4 op 清单（13 个）

| op | 语义 | stability |
|---|---|---|
| `unit.info` | 单位扁平状态（一次拿 6 个字段） | stable |
| `unit.buff_stacks` / `unit.buff_turns` | 按主关键字读层数 / 回合 | stable |
| `unit.buffs` | 全部 Buff（扁平列表） | stable |
| `unit.has_keyword` | 单位关键字判定 | stable |
| `battle.units` | 全场单位扁平快照 | stable |
| `battle.round` | 回合数（拿不到 → `None`） | stable |
| `unit.damage` | 造成伤害（内部算目标 HP） | stable |
| `unit.heal` | 治疗（同一根 HP 管线） | stable |
| `unit.set_hp` | 直接设 HP（走 OnChangeHp） | stable |
| `unit.add_passive` | 按 id 施加被动 | stable |
| `unit.burst_by_buff` | **复合 op**：读层数→算伤害→改血，一个时间片完成 | stable |
| `unit.add_buff` | 走 `BuffDetail.AddBuff` 施加 Buff | **experimental** |

`experimental` 是明确的等级：Python 侧 docstring 标注、真机未验证、失败返回 `False` 不抛错。

### 1.5 为什么必须在 C#

同一个需求（SINKING ≥ 6 → 扣血）在两层的实现差异：

```python
# B 层：3 次 crossing，且要自己知道 "value 是目标 HP"
stacks = unit.call("GetActivatedBuffStack", "SINKING", False)
unit.call("ChangeHp", hp - stacks * 3, "PASSIVE", "ON_SUCCESS_ATTACK", None, None, None, 2123, False, False)

# A 层：1 次 crossing，语义在 C# 里
ops.unit.burst_by_buff(unit, "SINKING", per_stack=3, max_stacks=6)
```

C# 侧还收掉了 Python 拿不到的东西：私有成员（`_buffDetail`）、枚举/哨兵值、Il2CppInterop
把 `ref/out` 改写后的真实签名（例如 `BuffDetail.AddBuff` 的后 4 个 int 是 `out`）。

### 1.6 可失效性（fail-open）

A 层是**独立程序集** `LimbusPy.Ops.dll`，内核用 `Assembly.LoadFrom` 动态加载
（`kernel/LimbusPy.Bridge/Runtime/OpsLoader.cs`）：

* 缺失 / 加载失败 / 自检失败 → 日志一行说明 → **不广告 `ops.v1`** → Python 侧 `has_op()` 为假；
* 内核其余部分（B 层、补丁、快照）完全不受影响。

### 1.7 顺带修掉的两个真机缺口

1. **`reflect.*` 有了唯一实现**（`Core/Reflect/ReflectOps.cs`）：之前只有 C# 测试 harness 实现了
   `reflect.get/set/invoke`，真内核没有这条路由 —— Python 门面在真机上会 `notfound`。
   现在 `KernelHost` 与 `TestKernel` 走同一份代码（conformance 201 项覆盖）。
2. **全局单例注册**：内核此前 `Globals = new GlobalRegistry()` 之后从未 `Register`，
   于是 `global:BattleObjectManager` 和 SNAPSHOT 在真机上是死路。A 层加载时顺手注册。

---

## 2. B 层：反射 + 批量 + 生成式 1:1 绑定

B 层是"什么都能做"的那一半，此前原型保留，并补上了两处：

| 能力 | 位置 | 说明 |
|---|---|---|
| `reflect.get/set/invoke/types/describe` | `Core/Reflect/ReflectOps.cs` | 内核与 harness 共用（见 §1.7） |
| `BATCH`（`0x17`） | `Core/Bridge/RequestRouter.cs` | 一次 `Post` 执行 N 个 op，一次回包，逐项 `ok/err` 隔离 |
| `Session.read_many` | `python/limbuspy/game/session.py` | 有 `batch.v1` 能力位时一次往返，否则退化 N 次 |
| 1:1 生成绑定 | `tools/gen_bindings.py` → `api/bindings.py` | 24 类 / 5243 成员 / docstring 100% / 确定性输出 |

成本对照（`python/examples/run_examples.py` 的 `contrast` 场景实测）：

```
门面 lp.unit(201) 后读 6 个属性       → 1 次 crossing
B 层 unit.hp + max_hp + mp + id      → 4 次 crossing
```

---

## 3. 示例（八个示例 + 两个对照，全部可运行）

```powershell
python python/examples/run_examples.py
python python/examples/run_examples.py --quiet --only drowning_tide
```

| 示例 | 展示什么 | 断言 |
|---|---|---|
| `python/examples/hello_passive` | 十行 Hello World：模块级 `@lp.on_hit` | 回调触发、0 次反射 |
| `python/examples/burst_on_hit` | 复合动作 `lp.burst` + `lp.unit` 快照 | 爆发一次、伤害 24、**0 次反射** |
| `python/examples/damage_ledger` | 纯推送记账 | 记到伤害/死亡/崩溃，**0 次 crossing** |
| `python/examples/field_medic` | 读一次 + 写一次 + config | 每单位 1 读 + 需要时 1 写 |
| `python/examples/drowning_tide` | 注册 Buff/被动 + `victim.has()` 零往返条件 | 位图判定 0 次 crossing，结算 1 次 |
| `python/examples/shield_warden` | 条件写在装饰器里 + 三种写操作 | 轻击不触发、重击补盾并反打 |
| `python/examples/battle_scribe` | 快照经济学 | 10 个事件 0 次 crossing，一次报告 2 次 |
| `python/examples/sinking_tide` | 门面 + Harmony 补丁 + 同步泳道 + 静态表 + 热重载 | 见 `test_example_mod.py`（14 项） |
| `A vs B`（驱动器内联） | 两层成本对照 | A=1、B=4 |
| `actions`（驱动器内联） | 全部门面动作可达性 | 21 项：每个动作都不返回 `ok=False` |

它们跑在 `MockKernel` + `MockOps`（契约的 Python 参考实现）上，走**真实 LPCP 通路**，
不需要游戏；驱动器还会扮演 C# 侧补上 `mask_victim` / `mask_self` / `round`，
所以 `victim.has(...)` 这类谓词在无游戏环境下跑的是**同一套判定**。
契约测试 `python/tests/test_curated_api.py` 盯着"spec ↔ Python 壳 ↔ 参考实现 ↔ payload"
四处不漂移，示例行为由 `python/tests/test_examples.py` 守。

---

## 4. 验收（当前状态）

| # | 标准 | 状态 | 证据 |
|---|---|---|---|
| V1 | A 层 Python 侧静态可查 + docstring | ✅ | 生成物是真类；`--audit`/契约测试 |
| V2 | 契约三向不漂移 | ✅ | `gen_contract.py --check` + `test_curated_api.py` |
| V3 | 内核与 harness 同一份反射路由 | ✅ | conformance **201/201**（含新增 reflect/BATCH 检查） |
| V4 | `BATCH` 1 次往返 N 个操作 | ✅ | conformance `batch:` 4 项 + `Session.read_many` |
| V5 | A 层 op 零反射 | ✅ | 示例断言 `counters.calls == 0` |
| V6 | C# 两个程序集真编译 | ✅ | `dotnet build` 0 warning / 0 error（对着真实 interop） |
| V7 | 三个 A 层示例可运行 | ✅ | `run_examples.py` 9 场景 / 62 断言 0 失败 |
| V8 | 封装层可独立失效 | ✅ | `OpsLoader` + `ops.v1` 协商 |
| — | 真机行为验证 | ⏳ | 需要开游戏跑一次（见 §5） |

---

## 5. 真机验证清单（下一步，10 分钟）

1. 把 `LimbusPy.Ops.dll` 和三个 `LimbusPy.Bridge*.dll` 放进 `BepInEx/plugins/LimbusPy/`；
2. 进战斗，看日志：`LimbusPy.Ops loaded: limbuspy.ops (available)`；
3. REPL：`python -m limbuspy repl --pipe limbuspy.<pid>`
   * `session.has_op("unit.info")` → True
   * `session.ops.battle.units()` → 全场扁平列表（验证 `_allUnitDictionary` 取法与全局注册）
   * `u = session.ops.battle.units()[0]` → `session.ops.unit.damage(u, 5)` → HP 下降
   * `session.ops.unit.burst_by_buff(u, "SINKING")` → 层数与伤害合理
4. `session.get(unit_ref, "Hp")` 应当**不再**报 `notfound`（§1.7 的修复）；
5. 打一场，看 `@on("DamageDealt")` 是否触发（`DamageDealt` 挂 `OnChangeHp`）。

未在真机验证的项：`unit.add_buff`（experimental）、`battle.round`（ObscuredInt 反射读法）、
`unit.heal` 走 `ChangeHp` 提升 HP 的副作用（游戏可能有专门的回复事件）。

---

## 6. 取舍（明确写下"不做什么"）

| 决策 | 理由 |
|---|---|
| A 层只做"必须原生"的东西 | 改 C# 要重编译+重启；参数校验/组合尽量留在 Python 薄壳 |
| op 用**命名参数**而不是位置数组 | 可增删参数不破坏旧调用；内核一次集中校验 |
| `burst_by_buff` 不消耗层数 | 消耗要走 BuffDetail 管线，语义未验证；先只做"按层数加伤" |
| A 层 hook 只做 5 个时机 | 每个都要对着 `dump.cs` 验签 + 真机验触发；先立样板再长大 |
| B 层保留 1:1 生成绑定 | 冷门类型/调试需要"什么都能调"；但有 docstring 与默认值，不再手写签名 |
| 不做运行时解析 26k 类型索引 | 34 MB JSON 不随包分发；冷门类型用 `limbuspy api gen` 生成到本地 |

