# LimbusPy A 层重构：实施状态与真机验收清单

> 配套文档：`00-master-plan.md`（计划与裁决）、`01`/`02`/`03`（三份调研）。
> 本文记录**实际落地了什么、怎么验证的、还差什么**。

---

## 1. 状态总览

| 阶段 | 状态 | 证据 |
|---|---|---|
| S0 地基（G1/G2/G3/G4/G6） | ✅ | 加时点/加 op 已变成纯 toml 工作 |
| S1 时点与动作扩容 | ✅ | 时点 5→**72**，动作 13→**29** |
| S2 门面 `lp.*` | ✅ | 教程级名字固定 15 个，测试守门 |
| S3 注册层（Content/Buff/Passive + Pred + bitmask） | ✅ | 主场景 0 往返有断言 |
| S4 覆盖率长尾 | ✅ | 72 时点全部签名逐字验证 |
| S5 自定义 Buff 真注入 | ✅ 代码 | 枚举基址 24576 + 图标 prefix + 解析回退 |
| S6 人格/技能/EGO 模板 | ✅ 模板层 | `define()` 产出 schema 校验过的行/模板声明 |
| S7 示例（作者面的验收） | ✅ | 8 个示例 + 2 个对照，62 项断言 |

| 指标 | 重构前 | 现在 |
|---|---|---|
| A 层时点 | 5 | **72** |
| A 层 op | 13 | **29** |
| pytest | 255 | **323**（旧断言文件零改动） |
| conformance | 201 | **201**（未回归） |
| 门面入口 | 4 个 session 入口 + 2 套单位类型 | **1 个 `lp.*`** |
| 「受击者有 X 才触发」 | 做不到（40–60 行） | **8 行，0 次跨进程往返** |
| `import limbuspy` | 硬拉 2.1 MB 生成绑定 | **不拉** |
| 可运行示例 | 1 个（sinking_tide） | **8 个 + 2 个对照场景** |

**四道验收门全绿**：
`pytest 323 passed` · `gen_contract --check (29 op / 72 hook)` ·
`conformance 201/201` · `check_hook_bindings 72/72` ·
`run_examples 9 场景 / 62 断言 / 0 失败`

---

## 2. S0 地基：把两个手写瓶颈拆掉

| 项 | 落地 | 文件 |
|---|---|---|
| **G1** op 注册生成化 | 契约里的 `class`/`method` 直接推出接线 | `OpsRegistry.g.cs`（生成） |
| **G2** payload 按参数名绑定 | `arg = "realHpDmg"` → 生成映射表 + 70 行永不增长的装配器 | `HookPayloadMap.g.cs`、`PayloadShaper.cs` |
| **G3** 可改值泳道 | `postfix_result` / `prefix`，独立 Harmony id | `HookTable.cs` |
| **G4** 唯一一次 `Bridge.Core` 改动 | `extra` 元数据袋 + `IOpsHost.CallPython` | `OpsApi.cs`、`KernelHost.cs` |
| **G6** 签名漂移 CI | 按类型限定 + 逐字比对参数名 | `tools/check_hook_bindings.py` |

**关键决定：用参数名而不是位置下标。** `AccessTools.Method(type, name)` 不传类型时遇同名
重载会选错，而 `OnSucceedAttack` 在 `Dump.cs` 里有四种签名共存。

> **实证**：第一次提取 `OnTakeHpDamage` 时，正则被默认值 `= (BUFF_UNIQUE_KEYWORD)2123`
> 里的右括号截断，得到 8 个参数；真实签名是 **10 个**。G6 当场拦下。

---

## 3. S1/S4：时点 5 → 72，动作 13 → 29

`params` 全部取自 `il2csharp/out/limbus/full0`（2026-11-02，带方法体）。

**时点覆盖**（按族）：伤害/理智双通道、硬币（开始/前置/掷出/判定/结束/消耗）、
拼点与对拼（开始/胜/负/结算/结束）、攻击（确认/结束/对他人结束）、回合（前置/开始/结束）、
round（前置/开始/槽位/结束/阵亡结算）、战斗/波次/关卡（开始/结束）、
死亡（直接/旁观/假死/免死/不死状态）、崩溃（直接/强制/他者/部位）、退场与返回、
Buff（使用/失去/拦截/施加失败/层数消耗/罪孽激活）、护盾（增加/削减/打破/重置）、
精神崩溃、HP/MP 回复、召唤增援、振动爆炸、击杀（直接/队友击杀）。

**动作覆盖**：伤害/治疗/设血/设理智/护盾/速度/刷新/强制崩溃/加 Buff/加被动/叠层爆发、
全场单位/富状态/我方/敌方/批量伤害/批量治疗/回合数、内容计划/内容注册、随机三件套。

> **加时点的成本**：改 toml → `gen_contract.py` → `check_hook_bindings.py`。**不改 C#。**

---

## 4. S2 门面

```python
import limbuspy as lp

u = lp.unit(201)      # 1 次往返
u.hp, u.hp_ratio, u.is_break, u.level, u.buff_count   # 全部 0 次
lp.damage(u, 18)                                      # 1 次，返回 ActionResult
for enemy in lp.battle().enemies: ...                 # 1 次拿全场
```

`lp.battle()` 走 `battle.states`（`unit.state` 的批版），所以一次往返拿回的 `Unit`
天然带 `level` / `buff_count`，不是假值。

---

## 5. S3 注册层：问题 2 的正面解

```python
class Drowning(lp.ContentBuff):
    local_id, key, name = "drowning", "DROWNING", "溺水"

class TideCrush(lp.Passive):
    @lp.on_hit(when=lp.victim.has(Drowning))
    def crush(self, ev):
        lp.damage(ev.target, 12)
```

1. 注册期 `content.plan` 把 `{watch, hooks:[{timing, victim:[位], self:[位]}]}` 发给 C#；
2. 补丁回调里 `MaskPlan.Mask` 算整数位图塞进 payload；
3. Python 侧 `victim.has()` → 一次按位与。

**安全阀**：`rpc > 0` 的谓词注册期报 `PredicateCostError`。

---

## 6. S5 自定义 Buff 真注入

| 事实 | 值 |
|---|---|
| 原版 `BUFF_UNIQUE_KEYWORD` 成员数 / 最大值 | 2124 / **2123（=`None` 哨兵）** |
| Lethe 的注入基址 / 步长 | `1 + 4096 + 2k` |
| **我们的基址 / 步长** | **24576 (0x6000) / 1** |

实现（`BuffInject.cs`）：`EnumInjector.InjectEnumValues` 注入（幂等，上限 512）+
图标 patch（`GetBuffIconSprite` / `GetBuffTypoIconSprite`，用 **`GetKeyword()`** ——
`m_BuffKeyword` 那个字段不存在）+ PNG 走 `ImageConversion.LoadImage`
+ **拿不到图就 `return false` 放行**（与 Lethe 的同一补丁点共存）。

---

## 7. S6 内容生成层

`python/limbuspy/staticdata.py`（手写，全 docstring）。字段名**全部**取自
`full0/Assembly-CSharp/{Buff,Passive,Skill,Ego,Personality}StaticData.cs` 的 `// 0xNN` 行。

| 内容 | `define()` 产出 | 说明 |
|---|---|---|
| `Buff` | **完整行**（25 个字段） | `maxStack` / `destroyableOnZero` / `canBeDespelled` … 逐字对应游戏字段 |
| `Passive` | **完整行**（7 个字段） | 需要 `passive_id`；配合 `Passive.attach()` 直接生效 |
| `Skill` | **模板声明** `{table, base, overrides}` | 数据面太大，只覆盖 5 个字段 |
| `Ego` | 同上 | 26 个字段，只覆盖 4 个字段 |
| `Identity` | 同上 | 12 个字段，只覆盖 5 个字段 |

`staticdata.collect(contents)` 按表名分组产出内容包；缺模板的项被跳过（不拖垮其它内容）。

**刻意没做**：把行写进游戏加载器（`SkillStaticDataList.Init` 接管）。
理由：这段代码无法在没有游戏的条件下验证，而写错会直接崩选人界面。
诚实做法是把生成层做扎实、把边界写清楚（见 §8 的 R9/R10）。

---

## 8. 示例（问题 1/2/3 的可运行答案）

示例是这一轮改造的**验收面**：门面再好，作者也得从某个文件开始抄。
八个示例 + 两个对照场景，全部走真实 LPCP 通路、不需要开游戏：

```powershell
python python/examples/run_examples.py            # 9 个场景 / 62 项断言
python python/examples/run_examples.py --quiet    # 只打印失败项
```

| 目录 | 主题 | 实测成本 |
|---|---|---|
| `hello_passive` | 模块级 `@lp.on_hit`，十行，无 `Mod` 类 | 事件 0 + 1 次写 |
| `damage_ledger` | 只读事件（伤害/死亡/崩溃） | **0** |
| `field_medic` | 读一次 + 写一次 | 2 / 单位 |
| `burst_on_hit` | 复合动作 `lp.burst` | 1（整笔结算） |
| `drowning_tide` | 注册 Buff + 被动 + 零往返条件 | 谓词 **0**、结算 1 |
| `shield_warden` | 条件在装饰器里 + 三种写操作 | 每次触发 3 |
| `battle_scribe` | 快照经济学 | 10 事件 **0**、一次报告 2 |
| `sinking_tide` | 门面 + Harmony 补丁 + 同步泳道 + 静态表 + 热重载 | 见其驱动器 |

驱动器 `api_harness.py` 会**扮演 C# 侧**补上 `mask_victim` / `mask_self` / `round`，
所以 `victim.has(...)` 这类谓词在无游戏环境下跑的是同一套判定（不是"跳过谓词"）。

同时修掉的四处框架缺口（都是写示例时才暴露的）：

| 缺口 | 现象 | 修法 |
|---|---|---|
| 模块级 `@lp.on_*` 从未被扫描 | 文档写着支持，实际回调一次都不跑 | `loader._iter_module_decorated` + `on()` 的 wrapper 按实参个数判断有没有 `self` |
| 回合数恒为 0 | `round_() >= 3` / `once_per_round()` / `cooldown()` 全失效 | 运行时自己数 `RoundStarted`（0 往返），`Cond.round` 优先用 payload |
| 热重载后位图全 0 | 改完代码被动不触发，日志无异常 | `_load_one` 也调 `_publish_plan` |
| `victim.has("SINKING")` 恒假 | 原版 Buff 要先注册包装类才判得出来 | `plan_for` 把字符串关键字也 intern 进位图并回填位序号 |

顺带修的三处：`collect_contents` 只给 Buff 编号（否则 `watch` 与 `bit` 错位一位）、
`HookEntry.matches` 也看 payload 里的过滤字段（否则 `@lp.on_hit(head=True)` 恒不触发）、
`ActionResult` 新增子类 `BurstResult`（爆发要带回 `stacks`/`damage`/`hp_before`/`hp_after`）。

---

## 9. 真机验收清单

| # | 检查 | 期望 |
|---|---|---|
| R1 | 启动日志 | `LimbusPy.Ops loaded: limbuspy.ops (available)` |
| R2 | `lp.status()` | `ops=True`、`problems=()` |
| R3 | 72 个时点各触发一次 | `problems` 为空（全部挂点装上） |
| R4 | `lp.unit(id)` 后读 5 个属性 | 只产生 1 条 OPCALL |
| R5 | `@lp.on_hit(when=lp.victim.has(Buff))` | 有该 Buff 才触发；触发时无 OPCALL |
| R6 | 改值泳道（`postfix_result`） | 返回新值后游戏内数值真变化 |
| R7 | **S5 开关** | `Enum.TryParse` 能否解析注入成员（认不到也有回退，不影响功能） |
| R8 | `content.register` 后 | `unit.add_buff("<自定义key>")` 施加成功 |
| R9 | `Buff.define()` 的行 | 进内容包后游戏认识（图标/层数上限/分类符合预期） |
| R10 | `Identity/Skill/Ego` 模板声明 | 若要做加载器接管，先验模板行能被现有 loader 接受 |

R1–R6 已在 mock kernel 上验过；R7–R10 是真机才能得出的结论。
