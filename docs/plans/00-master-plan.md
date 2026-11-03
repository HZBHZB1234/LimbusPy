# LimbusPy A 层重构总计划

> 本文是三份调研计划的**合并裁决版**，不是三份文档的拼接。
> 输入：`01-api-ergonomics.md`（API 人机工程，2207 行）、`02-registration-layer.md`（注册层，1557 行）、
> `03-coverage.md`（覆盖面，912 行）。
> 本文只决定「做什么、按什么顺序、边界在哪」；细节论证看那三份。

---

## 0. 起点校正：先说清什么**不是**问题

调研过程中发现三处流传的错误前提，先钉死，避免计划建在错的基座上。

### 0.1 现有 5 个 A 层时点是正确的

我按最新全量导出（`il2csharp/out/limbus/full0`，2026-11-02，带方法体）逐字核对了
`BattleUnitModel` 上的真实签名：

| 钩子 | 真实签名 | `HookTable.Shape()` 取值 | 结论 |
|---|---|---|---|
| `DamageDealt` | `OnChangeHp(int oldHp, int newHp, DAMAGE_SOURCE_TYPE dmgSrcType, BATTLE_EVENT_TIMING timing, BattleUnitModel attackerOrNull, BattleActionModel actionOrNull, BUFF_UNIQUE_KEYWORD keyword)` | 0,1,2,3,4,6 | ✅ |
| `AttackSucceeded` | `OnSucceedAttack(BattleActionModel action, CoinModel coin, BattleUnitModel target, int finalDmg, int realDmg, int realHpDmg, bool isCritical, Nullable<bool> isWinDuel, BATTLE_EVENT_TIMING timing)` | 2,3,4,5,6,8 | ✅ |
| `UnitDied` | `OnDie(BattleUnitModel killerOrNull, BattleActionModel actionOrNull, BATTLE_EVENT_TIMING timing, DAMAGE_SOURCE_TYPE dmgSrcType, BUFF_UNIQUE_KEYWORD keyword)` | 0,3,2 | ✅ |
| `UnitBroken` | `OnBreak(BATTLE_EVENT_TIMING timing, BattleUnitModel attackerOrNull, BattleActionModel actionOrNull, DAMAGE_SOURCE_TYPE dmgSrcType, bool isBreakForcely)` | 1,4,0 | ✅ |
| `TurnStarted` | `OnStartTurn_AfterLog(BattleActionModel action, BATTLE_EVENT_TIMING timing)` | 1 | ✅ |

**所以本次重构是「让它可扩展」，不是「修 bug」。** 这条很重要：它把 P0 的验收标准从
「新代码能跑」变成「新代码跑起来后这 5 条 payload 逐字段和今天完全一致」——一个可机械验证的强断言。

### 0.2 但「正确」是靠结构巧合，不是靠机制

`HookTable.Install()` 用 `AccessTools.Method(type, name)`（不传参数类型）解析目标方法。
而 `Dump.cs` 里 `OnSucceedAttack` **有四种签名共存**：

| 签名 | 宿主 |
|---|---|
| `(action, coin, target, finalDmg, realDmg, isCritical, isWinDuel, timing, BuffInfo info)` | `_buffDetail` |
| `(action, coin, target, finalDmg, realDmg, isCritical, isWinDuel, timing)` | 8 参版本 |
| `(action, target, finalDmg, realDmg, realHpDmg, isCritical, isWinDuel, timing)` | `_passiveDetail` / `_egoGiftDetail` |
| `(action, coin, target, finalDmg, realDmg, realHpDmg, isCritical, isWinDuel, timing)` | **`BattleUnitModel`（我们要的）** |

今天没出事，只因为 `BattleUnitModel` 上恰巧各只有**一个**声明。一旦游戏新增一个重载，
`AccessTools.Method` 会静默选错，然后 `args[5]` 从 `realHpDmg`(int) 变成 `isCritical`(bool)——
**不抛异常，取到的是 0/1**。这是典型的「沉默数据损坏」。

→ 这条直接决定了 P0 的第一个技术决策（§2 约束 C2）。

### 0.3 三份静态产物不是同一个游戏版本

| 产物 | 日期 | 可靠性 |
|---|---|---|
| `Il2CppDumper-release/Dump.cs` | 2026-10-23 | 签名可能已漂移 |
| `Il2CppDumper-release/script.json` | 2026-10-29 | 同上 |
| `il2csharp/out/limbus/full0` | 2026-11-02 | 最新，带方法体，**核对签名的唯一真源** |

`dump.cs` 里同名方法跨类型重复 30+ 次，**按方法名 grep 得到的第一条极可能是别的类型**。
→ 以后所有签名核对必须：(a) 用 `full0`；(b) 先限定类型；(c) 逐字比对参数名。这条写成工具（§4 G6）。

### 0.4 `Lethe.Passives/` 是空壳，Lethe 没有被动框架

`analysis/lethe-dll-decompiled/decomp/Lethe.Passives/` 整个命名空间只有一个 185 字节、
函数体为空的 `ChangeAppearance_Passive.Setup(Harmony) {}`。

Lethe 的真实模型是**声明式 JSON 内容包**（`BepInEx/plugins/Lethe/mods/<mod>/` + `ModManager.GenerateModData()`），
作者不写 C#。它连「被动」这个概念都没有，条件只能靠 `AbilityData.scriptName` 字符串前缀。

**最强的反面证据**来自 `mods/LetheOfficialSupport/`（16 文件 2862 行）：因为 Lethe 没有公开 API，
它对 Lethe 的唯一调用是 `GetType("Lethe.Utils.ModManager") + GetMethods().FirstOrDefault(m => m.Name == "GenerateModData")`，
其余全是把 Lethe 的功能逐个关掉。

> 这就是我们的验收线：**三年后不许出现 `LimbusPyOfficialSupport` 用字符串反射把我们逐个关掉。**

---

## 1. 三份计划的冲突与裁决

三份文档在四处正面冲突。以下是裁决，后文全部按裁决后的方案写。

| # | 冲突 | 01 的立场 | 02 的立场 | **裁决** | 理由 |
|---|---|---|---|---|---|
| **X1** | `OpsApi.cs` 能否改 | 要改（加字段、`EmitBatch`、`InvokeBatch`，113→230 行） | 一行都不改 | **按 02 办；只允许一次「Extra 袋」加法** | 见下 X1 详解 |
| **X2** | 注册形态 | 模块级 `lp.on_hit(fn)`，`Mod` 降为可选 | 类体 `@on(...)`，loader 收集 | **两者都要，共用一张注册表** | 01 自己已写「同一个注册函数在类里也照常可用」 |
| **X3** | 过滤条件表示 | `watch=["SINKING"]` | `when=victim.has(Drowning)` | **`watch` 是 `when` 的语法糖，内部只有谓词** | 02 的 `rpc>0 注册期报错` 安全阀必须建立在谓词上 |
| **X4** | 读模型 | `Unit` 是完整快照，属性 0 往返 | `Cond` + bitmask 广播 | **两者是不同场景的同一目标，不冲突** | 快照管「读」，payload 预填管「同步泳道」 |

### X1 详解：为什么 `OpsApi.cs` 应该基本不动

01 的两个接口新增其实**都不必要**：

- `IOpsProvider.InvokeBatch(object[])` → `RequestRouter` 里循环调 `provider.Invoke()` 即可，
  路由层本来就持有 provider 引用（`kernel/LimbusPy.Bridge.Core/Bridge/RequestRouter.cs`）；
- `IOpsHost.EmitBatch(events, battle)` → 用一条合成事件（`{"timing": "__battle__", ...}`）走现有
  `EmitEvent` 即可，Python 侧识别这个保留名。

真正躲不掉的只有：`OpSpec`/`HookSpec` 要携带新元数据（`kind`/`lane`/`watch`/`cost`）。

**折中方案**：只加**一个**参数：

```csharp
// kernel/LimbusPy.Bridge.Core/Ops/OpsApi.cs —— 唯一一次同步改动
public OpSpec(string name, string group, string stability, string[] argNames,
              string[] argTypes, string returns,
              IReadOnlyDictionary<string, object> extra = null)   // ← 新增，可选

public HookSpec(string timing, string target, string method, string kind,
                string[] payloadNames, string[] payloadTypes,
                IReadOnlyDictionary<string, object> extra = null) // ← 新增，可选
```

之后所有元数据扩展都是「往 `extra` 里塞键」，**永不再碰 `Bridge.Core`**。
这一次同步重编译换来长期的解耦，是合理的。

### X4 详解：两个「0 往返」怎么统一

它们回答的是两个不同问题：

| 场景 | 机制 | 载体 |
|---|---|---|
| **读**（非事件上下文）：我要看这个单位的血量 | 一次批量 op 拿回完整快照 | `lp.unit(id)` → `Unit` |
| **事件过滤**（同步泳道，20ms 预算内）：只在这人有 X buff 时触发 | C# 在补丁回调里当场预读，塞进 payload | `ev.target` → 同样是 `Unit`，但已预填 |

**统一叙事**：`ev.target` 就是一个普通 `Unit`，只不过它的字段在 C# 侧已经填好了。
所以 `ev.target.hp` 和 `ev.target.buff_stack("SINKING")` 在事件回调里**同样是 0 往返**——
前提是你在 `watch=` / `when=` 里声明过。

谓词 `victim.has(Drowning)` 只是同一件事的**过滤形态**：它编译成一份 watch 集，
C# 按这份集合决定补丁回调里该读什么。

---

## 2. 五条硬约束（不可协商）

| # | 约束 | 违反的后果 |
|---|---|---|
| **C1** | 单一契约源仍是 `ops/spec/api.toml`，手写 C# 只写「语义」，不写「接线」 | 加一个时点要改 3 处人手核对签名 → 覆盖 76 个时点不可能 |
| **C2** | payload 字段**按参数名**绑定（`arg = "realHpDmg"`），**禁止**位置下标 | `AccessTools.Method` 遇重载静默选错，取到 0/1 而不报错（§0.2） |
| **C3** | 读属性 **0 往返**（快照）；事件过滤 **0 往返**（payload 预填 + bitmask） | 作者会以为属性免费而实际每次往返，这是性能问题的根源 |
| **C4** | `LimbusPy.Ops` 加载失败时内核与 B 层必须照常工作 | 游戏一更新整个框架不可用，等于没有降级 |
| **C5** | 作者视野里只允许出现：函数、值对象、属性、`ev`。**没有** session / bridge / handle / ref / ctx | 四个入口三种缓存语义 = 心智负担的全部来源（01 的 D2） |

---

## 3. 目标终点：作者看到的唯一 API 面

这一节是三份文档的合并结果。**教程级名字必须在 15 个以内。**

```python
import limbuspy as lp

# ── 读：获取即批量，之后属性全部 0 往返（C3）──────────────────────────
b = lp.battle()                 # 1 次：回合 / 波次 / 全场单位
b.round      # int | None       ⚠ 是属性，不是 lp.round —— 见 §3.2
b.wave       # int | None
b.units      # list[Unit]
b.players / b.enemies / b.alive

u = lp.unit(201)                # 1 次：该单位完整快照
u.hp / u.max_hp / u.sp / u.speed / u.hp_ratio
u.is_dead / u.is_break / u.is_panic / u.is_player / u.name
u.buffs                         # list[Buff]（快照里带的）
u.buff_stack("SINKING")         # int，0 = 没有
u.has_buff("SINKING")           # bool

# ── 写：一次调用 = 一次往返，失败返回 ok=False 而不抛 ──────────────────
lp.damage(u, 18)                # -> ActionResult
lp.heal(u, 20)
lp.set_hp(u, 100)
lp.add_buff(u, "SINKING", stacks=3, turns=2)
lp.burst(u, "SINKING", per_stack=3)      # 读层数→算伤害→改血，一个时间片内

# ── 事件：注册函数（可就地 tab 补全），返回 None = 不改 ────────────────
lp.on_hit(fn)                      # 命中结算后
lp.on_damage_taken(fn, watch=["SINKING"])   # 同步泳道（可改倍率），watch 让 C# 当场预读
lp.on_death(fn)
lp.on_break(fn)
lp.on_turn_start(fn)
lp.on_round_start(fn)
lp.on("CoinTossed", fn)            # 全量 76 个时点的通用入口

# ── 带条件：谓词对象，编译成同一套 watch（X3）──────────────────────────
from limbuspy.pred import victim, self_unit, ev, once_per_turn, cooldown, chance, any_of
lp.on_hit(fn, when=victim.has("SINKING") & once_per_turn() & chance(0.3))
# 等价写法（简单场景）：lp.on_hit(fn, watch=["SINKING"])

# ── 内容注册：类体声明，loader 收集（X2）─────────────────────────────
class Drowning(lp.Buff):
    key, name, icon = "DROWNING", "溺水", "drowning.png"
    max_stacks, max_turns = 10, 5

class TideCrush(lp.Passive):
    """受击者身上有 Drowning 时，这次伤害翻倍。"""
    @lp.on_hit(when=victim.has(Drowning))
    def crush(self, ev) -> int:
        return 200 if ev.target is not None else 100

# ── 逃生口：教程里一个字都不提 ────────────────────────────────────────
u.raw.get("Hp")                 # B 层反射
lp.reflect.invoke(...)
lp.static.personality(10115)    # 纯本地静态表，不过桥
```

### 3.1 与今天的对照（同一需求，行数变化）

| 需求 | 今天 | 重构后 |
|---|---|---|
| 受击者有 SINKING 就翻倍 | 40–60 行 / 9 概念 / 3 份文档，**且实际做不到**（同步泳道读不到 buff） | 8 行 / 2 概念 / 0 往返 |
| 对全场每个单位做一件事 | N 次往返（逐单位读） | 1 次（`lp.battle()`）+ 0 次属性读 |
| 注册一个自定义 Buff 并在时点上判断 | 不可能 | `class X(lp.Buff)` + `when=victim.has(X)` |

### 3.2 三个必须写清的取舍

1. **模块级只放函数和类，值一律挂对象属性。** 用 `lp.round()` 而不是 `lp.round`；
   属性形态给 `lp.battle().round`。理由：模块属性只能靠 PEP 562 `__getattr__`，pyright 不追踪。
   代价是 4 个调用点多一对括号，收益是整份 API 面零动态属性。
2. **`Unit` 会过期。** 快照语义的必然代价。用 `u.refresh()`、`ev.target`（每次事件新建）、
   以及 `LIMBUSPY_COST_WARN` 三件套兜住，文档必须每页提醒一次。
3. **失败一律 `None` / `ok=False + reason`**，只有编程错误（参数类型错）抛 `BadArgument`。
   这是 FaustLauncher 的规则，也是 C4 的落地方式。

---

## 4. 执行链

三条并行轨道汇聚成一条链。**S0 不做完，后面全是人肉劳动。**

```
S0 地基（拆手写瓶颈，不含新功能）  ← 唯一的前置，3.5 人日
 ├─ G1 op 注册生成化        （0.5d，零 toml 变更）
 ├─ G2 payload 参数名绑定   （1.5d，含 PayloadShaper）
 ├─ G6 签名漂移 CI 守卫     （0.5d）
 └─ G4 Bridge.Core Extra 袋 （0.5d，唯一一次同步编译）
      │
      ├────────────────┬──────────────────┬─────────────────┐
      ▼                ▼                  ▼                 ▼
S1 时点/动作到 P0 集   S2 门面            S4 覆盖率长尾      S5 Buff 真注入
 (6d)                  (8d)              (8d)              (5d)
      │                 │                  │                 │
      └────────┬────────┘                  │                 │
               ▼                           │                 │
        S3 注册层（8d）  ← 用户主场景在此达成 │                 │
               │                           │                 │
               └───────────────────────────┴────────┬────────┘
                                                    ▼
                                          S6 人格/技能/EGO（6d）
```

S1 与 S2 可完全并行（不同文件、不同层）。S4 只依赖 S0。S3 依赖 S1(G3)+S2。

### S0 — 地基（3.5 人日，**先做，不含任何新功能**）

| 项 | 做什么 | 文件 | 验收 |
|---|---|---|---|
| **G1** | 从 `api.toml` 已有的 `class`/`method` 生成 `OpsRegistry.g.cs` | 新 `tools/gen_contract.py` 产物 + 删 `OpsEntry.cs:33-45` | 零 toml 变更；13 个 op 全部 `available=true` |
| **G2** | `[[hook]].payload[]` 增可选 `arg = "参数名"` / `arg="obj.member"` / `static=` / `const=`；生成 `HookPayloadMap.g.cs`；手写约 70 行、**永不增长**的 `PayloadShaper` 取代 `Shape()` switch | `api.toml` + `HookPayloadMap.g.cs` + `PayloadShaper.cs` | 5 个既有 hook 的 payload **逐字段与今天一致**（§0.1 的表就是断言表） |
| **G3** | `kind = "prefix"` / `"prefix_result"` + `mutate` 段；可干预型时点单独用 `"LimbusPy.Curated.Mutate"` Harmony id | `HookTable.cs` | 能改 `finalDmg` 并观察到游戏内伤害变化（真机） |
| **G4** | `OpSpec`/`HookSpec` 各加一个 `extra` 字典参数（**唯一一次**碰 `Bridge.Core`） | `OpsApi.cs` 113→125 行 | 内核与 Ops 重编译后，其余一切行为不变 |
| **G6** | `tools/check_hook_bindings.py`：按类型限定 + 逐字比对参数名，签名漂移挡在 CI | 新工具 | 对 5 个既有 hook 全 PASS；故意改错一个参数名 → 报错 |

**G3 为什么必须在 S0**：现有 5 个时点全是 `void` postfix（`HookTable.cs:257-271` 只有一个 `Postfix` 变体），
结构上做不了「改伤害 / 改威力 / 免死 / 能不能拼点」。而 Modulex 系生态约 1/3 的补丁点属于这类。
没有 G3，S3 的「受击者有此 Buff 就翻倍」根本写不出来（只能读，不能改）。

**S0 总验收**：改造后再加第 6 个时点，**只改 toml**——用一个 throwaway 时点验证，验完删掉。

### S1 — 时点 5→27、动作 13→28（6 人日，依赖 S0）

范围：`03-coverage.md` 表 1 的 P0 行 27 个时点 + 表 2/3 的 P0 行 28 个 op。
其中 `unit.state`（16 字段）与 `battle.state` 是 S2 的前置。

验收：`gen_contract.py --check` 通过；`OpsManifest` 自检 `problems == []`；
`check_hook_bindings.py` 27/27 PASS；conformance 从 201 → ~230（新增「27 个 hook 全部 installed」一项）；
真机 27 个时点各触发一次。

### S2 — 门面（8 人日，可与 S1 并行）

范围：`lp.*` 14 个教程级名字、`Unit`/`Buff`/`Battle`/`UnitState`/`ActionResult`、
模块级注册（`contextvars.CURRENT_MOD`）、`Mod` 降为可选（改 `loader.py:252-253`）、
弃用垫片 + `warn_once`、`bindings.py` 移出默认导入路径（`api/__init__.py:34-41` 那条硬 import）。

验收：pytest 全绿且**旧测试一个都不改**；`import limbuspy` 不再 import `api.bindings`（`sys.modules` 断言）；
`lp.dev.cost()` 与文档标注的往返数逐条一致；pyright 在门面模块上 0 error。

### S3 — 注册层（8 人日，依赖 S1-G3 + S2）**← 用户主场景**

范围：`Content`/`Buff`/`Passive` 基类 + `Pred`（18 个内置谓词）+ `Cond` + `compile`/`mask` +
`content.plan` op + payload bitmask 预填。

**核心验收 V3.1**：
```python
@lp.on_hit(when=victim.has(Drowning))
def crush(self, ev): ...
```
只在受击者拥有 `Drowning` 时触发，且回调内 `counters.calls == 0`（0 往返）。

其余：`rpc > 0` 的谓词注册期报 `PredicateCostError`；一个事件 N 个被动只构造 1 个 `Cond`；
mask 位 > 63 注册期报错；老内核无 `content.plan` 时降级为 1 次 `unit.buff_stacks`。

### S4 — 覆盖率长尾（8 人日，依赖 S0，可与 S1/S2 交错）

范围：`03-coverage.md` 表 1 的 P1 行 31 个时点 + 表 2 的 P1 行 27 个函数 + 5 个属性组补全。
此时加时点已是纯 toml 工作，成本主要是**核对签名**而不是写代码。

### S5 — Buff 真注入（5 人日，依赖 S3）

范围：枚举注入 + 图标 prefix + static data 行。**技术路线已由 Lethe 实证**：

- `EnumInjector.InjectEnumValues<BUFF_UNIQUE_KEYWORD>(dict)`；
- **id 基址用 24576（0x6000）、步长 1**，避开 Lethe 的 `4097 + 2k` 领地；
- 我实测的原版枚举范围：`BUFF_UNIQUE_KEYWORD` 共 2124 个成员，**最大值 2123（= `None` 哨兵）**，
  1 个 4 位以上成员块从 1000 起密排到 2123。24576 有约 2.2 万个空号，且天然跨过哨兵；
- 图标走 `HarmonyPatch(BuffStaticData, "GetBuffIconSprite")` 返自定义 Sprite（128×128），
  三个 prefix 都要打（`UISpriteDataManager.GetBufSprite` / `GetBuffIconSprite` / `GetBuffTypoIconSprite`），
  拿不到图就放行（Lethe 同款，因此**可与 Lethe 共存**）。

**第一件事**（10 分钟）：验证 `Enum.TryParse<BUFF_UNIQUE_KEYWORD>("MYBUFF")` 能否解析注入成员。
认不到就走 `keyword_id: int` 兜底并写进文档。**这条决定 S5 能不能开工。**

### S6 — 人格 / 技能 / EGO（6 人日，依赖 S5）

范围：static data 行生成 + `SkillStaticDataList.Init` 去重接管 + `base_game_id` 模板覆盖。
**`base_game_id` 必填**，不允许「从零造人格」（Lethe 用 32KB 两个文件也只做到模板+覆盖）。

### 工作量汇总

| 阶段 | 人日 | 累计 |
|---|---|---|
| S0 地基 | 3.5 | 3.5 |
| S1 时点/动作 P0 集 | 6 | 9.5 |
| S2 门面（并行） | 8 | 9.5（并行后） |
| S3 注册层 | 8 | 17.5 |
| S4 覆盖率长尾 | 8 | 25.5 |
| S5 Buff 注入 | 5 | 30.5 |
| S6 人格/技能 | 6 | 36.5 |

**单人约 37 人日（7–8 周）；双人并行约 4 周。**

---

## 5. 全流程验证门槛

每一阶段结束必须同时满足：

1. `python -m pytest python/tests -q` 全绿；**除 S2 允许新增断言外，旧断言一个都不许改**；
2. `python tools/gen_contract.py --check` 退出码 0；
3. `python tools/conformance.py` 全绿（S1 后从 201 涨到 ~230，只增不减）；
4. `python tools/check_hook_bindings.py` 对全部已声明时点 PASS；
5. 示例 `python python/examples/run_examples.py` 打印 `N 场景 / 0 项断言失败`
   （深水区示例另有 `run_sinking_tide.py`）；
6. **新增 op/hook 的 docstring 里 `Cost:` 与实测往返数一致**（S4 加 `tools/lint_cost.py`）；
7. 真机清单（每阶段一次，不允许「攒到最后一起验」）。

---

## 6. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| **R1 三份静态产物版本不一致**（dump 10-23 / script 10-29 / il2csharp 11-02） | **高** | 核对签名**只认 `full0`**；G6 做成 CI；53 个 `?` 条目实现前先用 B 层只读探针确认，禁止凭 dump 猜 |
| **R2 `AccessTools.Method` 重载歧义**（`OnSucceedAttack` 已有 4 种签名） | **高** | C2 参数名绑定；G6 校验「参数名集合唯一确定重载」 |
| **R3 同步泳道 20ms 预算** | 中 | payload 在补丁回调里当场拼完（只读 `watch` 声明的）；`rpc > 0` 注册期报错；mock 侧超时断言 |
| **R4 补丁点与 Modular / Lethe 冲突** | 中 | 可干预型时点单独 Harmony id；`Mutate` 与观测分离 |
| **R5 快照过期导致读到旧值** | 中 | `u.refresh()` + `ev.target` 每次新建 + 文档每页提醒 + `LIMBUSPY_COST_WARN` |
| **R6 自定义 Buff 与存档/其他 mod 冲突** | 中 | 先做「复用官方 id」路线（S3 内），真注入放 S5；id 基址 24576 避开 Lethe；卸载时存档降级不崩 |
| **R7 op 膨胀的维护成本** | 低 | S0 的 G1/G2 已把「加一个」降到「改 toml」；`--audit` 四件套强制配套 |

### 明确「不做」

- 不做 `BattleUnitView` 级视觉钩（交给 B 层 + `bindings.py`）；
- 不做迷宫 / 大厅 / 编队；
- 不做泛型 / 协程 / 异步游戏方法的封装（IL2CPP 下不可靠）；
- 不合并 `modular/dsl.py`（并存：它管「生成 Modular 指令串」，注册层管「原生时点」）；
- 不删 B 层，只把它从主路径上摘掉。

---

## 7. 参考真值（写在这里，避免以后再查一遍）

| 事实 | 值 | 来源 |
|---|---|---|
| `BUFF_UNIQUE_KEYWORD` 成员数 / 最大值 | 2124 / **2123（=`None` 哨兵）** | `Dump.cs:523853` |
| 4 位以上成员区间 | 1000 → 2123，密排 | 同上 |
| Lethe 自定义 Buff 注入基址 / 步长 | `1 + 4096 + 2k` = 4097, 4099, … | `EnumInjectionGenericHandler.cs` |
| 我们的注入基址 / 步长 | **24576 (0x6000) / 1** | 本文决定 |
| Lethe 保留位 / 截断阈值 | 索引 64..71 保留；> 90 条静默截断 | 同上 |
| Buff 图标补丁点 | `BuffStaticData.GetBuffIconSprite` / `GetBuffTypoIconSprite` / `UISpriteDataManager.GetBufSprite` | `Lethe.Patches/Texture.cs:259-283` |
| 自定义 Buff 图标尺寸 | 强制 128×128 | `Lethe.Patches/CustomBuffLocale.cs` |
| 现有 A 层时点数量 / 正确性 | 5 / 全部正确 | §0.1 |
| `@hook("OnSucceedAttack")` 是否触发 | **不触发**（内核只发 `AttackSucceeded`） | `registry.py:47` + `HookTable.Shape` |
| 签名核对唯一真源 | `il2csharp/out/limbus/full0` | §0.3 |
| `BattleCharacterModel` | 该版本**不存在**；实际是 `BattleUnitModel_Player/_Ally/_Enemy/_Abnormality/_Abnormality_Part/_Assistant` | §0.3 |
