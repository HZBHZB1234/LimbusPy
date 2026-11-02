# LimbusPy —— 基于 Python 的边狱公司脚本层（设计计划 v1）

> 提案日期：2026-11-01
> 参考实现：`ModularLimbis/ModularSkillScripts`（Modular 本体 v5.1.15，Lua DSL）、
> `ModularCustomA/ModularCustomConsequences`（MTCustomScripts v35.123.4，第三方扩展范式）
> 导出产物：`Il2CppDumper-release/dump.cs`（87 MB 结构化签名）、`il2csharp/out/limbus/{gameonly,f169,post167}`（74 871 个带方法体的 .cs）
> 桥接先例：`mods/BotQuixoteNative/bridge/host/Bindings/ExactMethodResolver.cs`（原生 il2cpp metadata 精确方法解析）、`mods/LimbusPacketDump`、`skills/limbus-battle`（AIControl HTTP 范式）
> 相关旧计划：`docs/LETHEJS_FEASIBILITY_AND_PLAN.md`（IL2CPP 进程内 JS 方案；本文档在其基础上升级为"进程外 Python + C# 内核"，理由见 §1.4）

---

## 0. TL;DR

**目标**：一套 Python 脚本层，运行在**独立进程**，通过 **命名管道 + msgpack 帧**与一个**极薄、长期稳定**的 C# BepInEx 内核通信；脚本支持**真热重载**；能覆盖**战斗类 + 常规模组**的绝大部分 modding 需求；并与 **Modular 双向兼容**。

**核心取舍（与 LetheJs 路线的区别）**：

| 维度 | 进程内 JS（LetheJs 方案） | 本方案（进程外 Python） |
|---|---|---|
| 语言生态 | 需要 IL2CPP 下无 JIT 的 JS 引擎，第三方库基本不可用 | 全量 CPython 生态：UnityPy / PIL / numpy / requests / 任意 pip 包 |
| 热重载 | 换引擎全局状态，DLL 不能热重载 | Python 进程内 reload 是真热重载；C# 内核用"表驱动 + 泛化补丁"几乎不必重启 |
| 性能 | 单次调用最快（同进程） | 单次 RTT ≈ 60–200 µs；靠**批量派发 + 快照**把每帧摊销到 <1 次往返 |
| 崩溃隔离 | 脚本崩 → 游戏崩 | Python 崩 → 内核熔断/自动重启，游戏不受影响 |
| 生态位 | 只能服务本 mod | `pip install` / wheel / 目录式 mod，人人可发 |

**工期**：单人 7–9 周；两人并行约 5 周。分 8 个阶段，每阶段有可执行验收（§11）。

**必须先做的 4 个探针**（§11 P0，2–4 天）决定架构细节是否成立：主线程同步等待、Il2CppInterop 反射封送、命名管道吞吐、Modular 注册表注入。

---

## 1. 参考物解剖（结论，含证据）

### 1.1 Modular 的真实机制（不是"Lua 解释器"，是"注册表 + 指令串"）

- **时机表**：`MainClass.Load()` 里用一个 `List<string>`（约 100 条）灌进 `public static Dictionary<string,int> timingDict`（`MainClass.cs:33-153`），并带大量 legacy 别名。时机值就是 `actevent` 整数。
- **扩展点（4 个公开静态字典）**（`MainClass.cs:649-653`）：
  - `consequenceDict` → `IModularConsequence.ExecuteConsequence(modular, section, circledSection, circles)`（动作，117 个实现）
  - `acquirerDict` → `IModularAcquirer.ExecuteAcquirer(...) → int`（取值，111 个实现）
  - `luaFunctionDict` → `IModularLuaFunction.ExecuteLuaFunction(...)`（19 个）
  - `mobjgetDict`、`timingDict`（可增条目）
- **指令串 DSL**：`SetupModular(instructions)` 去掉空白后按 `/` 切成 batch，每个 batch 按 `:` 切：
  - `TIMING:XXX(..)` / `LUA:name` / `LUAMAIN:fn(a,b)` / `LOOP:targeting` / `RESETWHENUSE` / `CLEARVALUES` / `EXPECTED`
  - `IF(...)` / `IFNOT(...)` / `STOPIF` / `CONTINUEIF` / `VALUE_n(acquirer)` / `MOBJ_n(acquirer)` / 其余即 `Consequence(...)`
  - `ProcessBatch`（`ModularScripts.cs:1190`）逐 token 执行；`DoMath` 支持 `+ - * % ! ¡ ?` 七种运算符。
- **执行入口**：`ModularSA.Enact(unit, skill, selfAction, oppoAction, actevent, timing)`（`ModularScripts.cs:318`）。**前后置硬约束**：`activationTiming != actevent` 直接返回 —— 这决定了"外部程序化执行指令串"必须把 `activationTiming` 设成匹配的 actevent。
- **Lua 支持**：`Enact` 里 `modsa_luaScript != null` 时新建 `LuaState`，`InitializeLuaState` 把**所有** consequence/acquirer/luaFunction 名字**全量注册成 Lua 全局函数**（`ModularScripts.cs:1364-1393`），因此 Lua 侧 `buff("MainTarget",...)` 与 DSL 里 `buff(...)` 等价。
- **挂载方式**：技能/硬币/被动/（部分）Buff 的数据里 `AbilityData.ScriptName` 以 `Modular/` 开头（8 字符前缀被 `Remove(0,8)` 后交给 `SetupModular`），`SkillScriptInitPatch.cs`（205 KB / 5127 行、约 100 个 Harmony 补丁）建立 `ptr → List<ModularSA>` 字典并在每个时机遍历 `Enact`。

### 1.2 MTCustomScripts 的扩展范式（我们要对齐的"生态继承"姿势）

`ModularCustomConsequencesMain.cs` 给出标准三件套：

```csharp
[BepInDependency("GlitchGames.ModularSkillScripts")]   // 强制自己的 Load() 在 Modular 之后
public class Main : BasePlugin {
  public override void Load() {
    harmony.PatchAll(typeof(自定义Harmony补丁));        // 补 Modular 没补的点
    MainClass.timingDict.Add("WaitCommand", 7335);      // 注册新时机（6000+ 段号避免撞车）
    MainClass.consequenceDict["xxx"] = new ...();       // 塞实现（可覆盖原有键！）
    MainClass.acquirerDict["yyy"]     = new ...();
    MainClass.luaFunctionDict["zzz"]  = new ...();
  }
}
```

配套 `MTCSLuaDef.lua`（52 KB）是给 Lua LS 的 `---@meta` 定义 —— **我们的 Python 版应当自动生成同等的 `.pyi`**。

### 1.3 由此得出的五条设计约束

1. **不要重写时机系统**。Modular 那 205 KB 补丁是已被实战验证的资产；重写等于重造 100 个 Harmony 挂点并承担全部回归风险。→ 复用 Modular 的派发链（§6 L1/L2）。
2. **每个 hook 有整数 actevent**，且执行受 `activationTiming` 门控 → 我们的 hook 表也必须"时机 ID + 过滤器（Head/Tail/Crit/Keyword）"同构。
3. **数据挂载靠字符串**（`ScriptName`）→ Python 脚本若要"看起来像一个正常 mod"，就得能生成/注入这些字符串与静态数据 JSON（§6 L3）。
4. **C# 侧只做"注册表 + 泛化补丁 + 反射"**，所有策略在 Python。C# 内核越薄，版本漂移与热重载问题越少。
5. **必须能与别人共存**：不 unpatch 别人的补丁、注册键加前缀、Harmony id 隔离、时机段号走 7300+/9000+ 空地。

### 1.4 为什么进程外（读 LETHEJS 计划后的修订）

`LETHEJS_FEASIBILITY_AND_PLAN.md` 的最大风险 R0 是"IL2CPP 无 JIT，JS 引擎能不能跑"。同一约束对 Python 更致命（CPython 本身就是 JIT 缺失也没关系，但**根本没有**能嵌进 IL2CPP 的成熟 Python 运行时）。进程外彻底绕开：Python 侧只是普通 CPython 进程，游戏侧只是标准 socket/管道客户端。代价是封送与主线程同步，这在本作的低频触发模型下完全可接受（§4.4 基准预算）。

---

## 2. 总体架构

```
┌──────────────────────────── Limbus Company (Unity IL2CPP, 主线程) ────────────────────────────┐
│  BepInEx 6 (LimiNex) + Lethe                                                                │
│                                                                                             │
│  ┌─ LimbusPy.Bridge.dll (C# 内核, 极薄、长期稳定) ─────────────────────────────────────────┐ │
│  │  PipeServer      : NamedPipeServerStream, 读线程只解帧，不碰游戏对象                     │ │
│  │  MainThreadPump  : 注入的 PyPump: MonoBehaviour.Update → 派发队列（唯一允许触碰游戏的地方）│ │
│  │  HandleTable     : u32 句柄 ↔ Il2Cpp 对象（arena 自动回收 / pinned TTL）                 │ │
│  │  Reflect         : 方法/字段/属性读写（托管 MethodInfo 缓存 + 原生 metadata 兜底）        │ │
│  │  PatchHub        : 表驱动的泛化 Harmony 补丁；Python 声明 → 运行时增删，无需重编译         │ │
│  │  ModularGlue     : 往 consequenceDict/acquirerDict/luaFunctionDict/timingDict 注册转发器  │ │
│  │  VirtualForward  : 预注入的派生子类（PySkillAbility/PyCoinAbility/PyPassiveAbility/...）  │ │
│  │  Snapshot        : 战斗状态打包成扁平 msgpack（避免 N 次往返）                            │ │
│  │  Supervisor      : 子进程看护、崩溃重启、熔断、性能预算                                    │ │
│  └────────────────────────────────────────────────────────────────────────────────────────┘ │
│                    ▲ Harmony 钩子（时机 / 任意方法补丁）                                     │
│                    ▼ IL2CPP 运行时（BattleUnitModel / SkillModel / CoinModel / BuffModel …） │
└────────────────────────────────────┬────────────────────────────────────────────────────────┘
                                     │  \\.\pipe\limbuspy.<pid>
                                     │  [u32 len][u8 type][msgpack payload]
┌────────────────────────────────────▼────────────────────────────────────────────────────────┐
│  python.exe  (独立进程, 由内核 spawn 或外部 attach)                                          │
│                                                                                             │
│  limbuspy.runtime      transport / codec / handles / arena / batching / errors / trace       │
│  limbuspy.il2cpp       自动生成的类型与枚举（.pyi 给 IDE + 查表 json 给运行时）              │
│  limbuspy.game.*       battle / statics / assets / locale / content / ui / audio / rpg / save │
│  limbuspy.modular      双向桥：consequence() / run() / dsl 构造器 / gdata / lua 互调          │
│  limbuspy.mod          Mod 基类、装饰器注册、生命周期、热重载、依赖、配置 schema               │
│  limbuspy.devkit       scaffold / pack / repl / doctor / trace / record-replay / inspector   │
│  mods/<name>/          mod.toml + *.py + assets/ + locale/                                  │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

**责任边界**：C# 内核 = "能力 + 安全 + 调度"，不含任何游戏策略；Python = 全部策略与 API 装配。

---

## 3. 五条需求逐条落地

| # | 需求 | 落地方案 | 关键设计点 |
|---|---|---|---|
| 1 | 大量 API 覆盖绝大部分 modding 需求（战斗 + 常规） | 三层 API：①**反射保底**（任意可及方法/字段，无需编译）②**类型化 facade**（`game.battle.*`/`game.assets.*`，从 dump 生成）③**Modular 直通**（直接调用 230 个已被验证的 C# consequence） | 覆盖度不是靠手写 500 个 wrapper，而是"反射保底 + 生成 facade + Modular 直通"三者叠加（§5） |
| 2 | 独立进程 + IPC/socket 桥接 | Windows 命名管道（首选）／TCP loopback（回退）；msgpack 帧；双泳道（同步返回值 / 批量副作用） | 主线程嵌套 pump + 深度上限 + 超时 + 熔断；读写与游戏对象严格线程隔离（§4） |
| 3 | 热重载 | 进程内模块 reload + 注册表重建 + 回调身份（mod, name, gen）+ 状态保持 + 安全点静默 | C# 内核"表驱动 + 泛化补丁"使 95% 改动不需要重启；剩余边界写清楚（§7） |
| 4 | 方便继承进模组生态 | 双解：**继承**（`class MyMod(Mod)` 子类化 + `@hook/@consequence/@acquirer` 装饰器）＋**集成**（BepInEx 插件 + wheel/目录式 mod + `[BepInDependency]` 反向暴露 API，任何 C# mod 可把函数注册成 Python 可调用） | 见 §8；其他 mod 的接线只需要一行 `PyBridge.Expose("MyMod", fn)` |
| 5 | 与 Modular 兼容 | 四层：L0 共享数据袋 / L1 Modular→Python / L2 Python→Modular / L3 Python 生成合法 Modular 内容；L4（可选）Python 侧实现 DSL 解释器接管 `Modular/` 前缀 | 见 §6；L1+L2 是 v1 必做，L3 是 v1.5，L4 是 v2 |

---

## 4. 桥接内核（C#）与协议

### 4.1 传输与帧

- **主传输**：`NamedPipeServerStream(@"limbuspy." + pid, PipeDirection.InOut, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous)`，仅本机、ACL 限定当前用户（比 TCP 免防火墙弹窗、免端口冲突、免被其它进程连）。
- **回退**：`127.0.0.1:<随机>` TCP（调试友好，可用外部工具抓包；仅在管道异常时启用）。
- **帧**：`[u32 LE length][u8 type][payload msgpack]`；payload 上限 8 MB（超限走分块或共享内存 v2）。
- **Codec**：msgpack 是唯一主格式；`LIMBUSPY_CODEC=json` 便于人读调试。Python 侧优先 `msgpack`（可选 C 扩展），缺失时回退纯 Python 实现（体积几十 KB，够用）。

### 4.2 消息集（proto v1）

| 方向 | 类型 | 说明 |
|---|---|---|
| C#→Py | `HELLO` | proto / kernel 版本 / game build hash / game version / caps[] |
| Py→C# | `READY` | 版本 / 已加载 mod 列表 / stubs build / 需要的 caps |
| Py→C# | `CALL`{id,target,method,args} | 反射调用（target 可为 `Type::Method` 静态、句柄、`global.X`） |
| C#→Py | `CALLRESULT`{id,ok,value\|err} | 结构化错误（类型：notfound / ambiguous / marshalling / wrongThread / timeout / script） |
| C#→Py | `EVBATCH`{seq,frame,events[{timing,gen,ctx,snapshot?}]} | **每帧一次**批量派发同一时机的全部 hook |
| Py→C# | `ACT`{seq,actions[{eventId,effects[],retval?}]} | 批量回写：副作用 + 需要同步返回的值 |
| C#→Py | `SYNCALL`{id,fn,ctx,deadline} | 同步回调（主线程阻塞等待，见 4.3） |
| Py→C# | `SYNCRESULT`{id,ok,value} | |
| Py→C# | `PATCH_ADD`/`PATCH_DEL` | 声明/撤销任意 Harmony 补丁 |
| Py→C# | `REG`/`UNREG` | Modular 三注册表 + timingDict 的增删 |
| C#→Py | `RELOAD`{gen,phase,mods} | `QUIESCE` / `RESUME` |
| Py→C# | `STATE_SAVE`/`STATE_LOAD` | 跨重载状态 |
| 双向 | `LOG` `PING` `STATS` `DOCTOR_REQ` | 日志、心跳、指标、能力自检 |

**兼容规约**：`proto` 主版本不匹配 → 拒绝连接并给出明确错误；次版本只增不改；未知消息类型忽略并计数（便于老 Python 配新内核）。

### 4.3 线程模型（这是整个方案最关键的地方）

C#：
- **PipeReader 线程**：只做解帧 + 入队 + 简单的值解包。**绝不触碰任何 IL2CPP 对象**。
- **MainThreadPump**：由注入的 `PyPump : MonoBehaviour`（`ClassInjector.RegisterTypeInIl2Cpp`，挂到常驻 GameObject）的 `Update()` 驱动；所有反射调用、补丁安装、状态读取都在这里。
- **同步等待（SYNCALL）**：战斗补丁在主线程需要 Python 的返回值时：
  1. 发送 `SYNCALL`；
  2. 进入 `NestedPump(deadlineMs)`：只处理"主线程安全"的入站消息（reply、纯反射读、小动作），**不再发起新的 Python 回调**（嵌套深度 ≤ 1，二次进入直接以 `default` 返回并向脚本报错）；
  3. 超时（默认 20 ms，可按 hook 配置）→ 返回脚本声明的 `fallback`，计数 + 告警，**绝不无限等待**；
  4. 若 Python 侧死亡 → 立即熔断，进入 `pass_through` 直通模式（游戏照常运行，脚本失效）。

Python：
- **单线程 selector 事件循环**：读帧 → 分发 → 执行 hook → 写帧。所有 hook 都在这条线程上跑（无锁、可复现、栈回溯清晰）。
- **线程池**只用于真阻塞 IO（资产解包、网络下载、大文件）；结果回投到事件循环。
- 默认禁止在 hook 内阻塞超过预算（默认 3 ms/帧，配置化）。超出 → 本次 hook 记 `over_budget` 并跳过后续同帧 hook（保护游戏）。

### 4.4 性能预算（P0 必须实测）

| 操作 | 目标 p50 | 目标 p99 |
|---|---|---|
| 空 RTT（ping） | ≤ 80 µs | ≤ 250 µs |
| `EVBATCH` 10 个 hook 派发+回写（1 次往返） | ≤ 250 µs | ≤ 800 µs |
| `CALL` 反射读 int 字段（含解包） | ≤ 120 µs | ≤ 400 µs |
| 单位快照（unit→dict，含 20 buff + 6 skill） | ≤ 400 µs | ≤ 1.5 ms |

若实测显著劣于上表：① 先试共享内存环形缓冲做"逐帧状态镜像"（读多写少）；② 再试把热点 facade 下推成 C# 侧的**编译期 typed 绑定**（由生成器产出，随内核一起发布）。两条都已列入后备方案。

### 4.5 句柄模型（防止悬垂指针）

- `Handle = u32`：`kind(4b) | slot(28b)`；kind = unit/skill/coin/buff/action/stage/model/ability/sound/asset/…
- C# 维护 `slot → GCHandle`（根化，保证 il2cpp 对象不被回收）。
- **作用域**：
  - **arena（默认）**：回调期间分配的句柄在回调返回后整块释放 → 脚本拿不到 dangling；
  - **pinned**：脚本显式 `pin()`，TTL 默认 5 分钟 + `release()`；内核每 30 s 巡检；
  - **frame-pinned**：`keep_until_frame_end()` 供批量场景。
- **有效性校验**：任何句柄使用前检查 `slot 有效` + `Il2CppObjectBase.WasCollected == false`，失败抛 `StaleHandle` 并带上原始 kind 与创建位置（trace 里能定位到脚本行）。

### 4.6 封送规约（只允许这些过桥）

`None / bool / int / float / str / bytes / list / dict / Handle / 枚举名(str) / struct(dict, 生成器给出字段名)`

- 不做通用对象图序列化（会拖慢、会循环）。
- il2cpp struct 一律走"生成器产出的扁平 dict"（字段名来自 `il2cpp.h`/`dump.cs`），或用句柄。
- 数组/列表：小数组直接传，大数组（>4 KB）走"句柄 + 迭代器"分页。
- 异常：C# 异常必须被内核捕获并转成结构化 `err`，**绝不让异常穿过 Harmony 补丁**（那会连累整个战斗帧）。

---

## 5. Python 侧 API 目录（"大量 API"的具体清单）

> 命名空间按"能查到、能补全、能测"组织；每个函数背后写明归属实现（facade / 反射 / Modular 直通），便于后续替换实现而不改脚本。

### 5.1 `limbuspy.runtime`（内核客户端，全部 mod 共用）

```
connect() / spawn() / attach(pid) / close()
invoke(target, method, *args) / invoke_static("Ns.Type::M", ...)
get(obj,"Field") / set(obj,"Field",v) / prop(obj,"Prop") / set_prop(...)
call_py_image / meta.classes(image) / meta.methods(type)     # 元数据查询
handle.pin/unpin/keep_frame/release/alive
batch()  # 上下文管理器：把块内多次 invoke 合并成一个帧
log/trace/profiler/stats
errors: StaleHandle, NotFound, Ambiguous, Marshalling, WrongThread, Timeout, ScriptError
```

### 5.2 `limbuspy.game.battle`（战斗类模组的主战场）

对齐 Modular 的 111 acquirer / 117 consequence 语义（不是照抄名字，而是按"能力"重排，Python 侧更像人类写法），并补 Modular 没覆盖的：

```
battle.state()            # 全局快照（round/wave/phase/units/sins/resource）
battle.units(player|enemy|all) / unit(handle|inst_id) / unit.focus()
battle.round / wave / phase / timing

unit.*    : hp/max_hp/sp/max_sp/speed/level/uptie/break_level/panic/position/faction/inst_id/
            origin_id/name/is_actionable/is_part/is_abnormality/part_owner/
            buffs()/buff(keyword)/has_buff/has_keyword/add_buff/remove_buff/set_buff_stack/
            set_buff_turn/stack/turn/active_round/source/
            skills()/skill(slot)/add_skill/remove_skill/replace_skill_on_dashboard/
            hide_skill/skill_slot_count/set_skill_amount/
            passive_list()/has_passive/add_passive/remove_passive/reveal_passive/
            shield()/heal()/damage()/instant_death()/change_hp()/set_immortal()/
            resist(atk_type|sin)/overwrite_resist/inflict()/aggro()/retreat()/summon.../
            appearance()/set_scale()/set_position()/play_motion()/change_motion()
skill.*   : id/name/atk_type/def_type/attr/ego_type/rank/base_power/coin_count/weight/
            target_list/targets()/add_ability/remove_ability/ability_list/
            on_use_scripts()/insert_skill/drop_skill/dash_amount/
coin.*    : index/state(head|tail)/is_broken/is_rerolled/operator/reuse/cancel/
            add_ability/clear_abilities/after_attack_hooks/
buff.*    : keyword/stack/turn/active_round/source/type/category/max_stack/max_turn/
            dynamic_locale()/edit_max/destroy/activate()
damage.*  : add/mult/break/bonus/weighted/change_atk_type/change_affinity/
            crit_chance/crit_ratio/heads_chance/set_damage_taken/ignore_defense/
            damage_source()/last_final_dmg/last_hp_dmg/
clash.*   : is_duel/start/win/lose/parrying_power/force_end/close_type/
action.*  : list()/index/priority/insert/remove_all/main_target/sub_targets/
            set_action_index/swap_deploy_order/better_send/
stage.*   : extra_slot/wave/buf/win_stage/end_stage/end_battle/end_limbus/change_map/
resource.*: sin_stock(sin,faction)/add_resource/resonance/sins_in_dashboard/
targeting.*: resolve("MainTarget"/"AllEnemy"/"LowestHp"...)/custom(...)   # 复用 Modular 的 GetTargetModel
```

### 5.3 `limbuspy.game.statics`（静态数据表，纯 Python，不需要桥）

- 数据源：`static_s1_0_assets_all_<hash>.bundle` 里 2185 个 TextAsset JSON（用 `tools/parse_catalog.py --find` 定位，UnityPy 解包，`FALLBACK_UNITY_VERSION="6000.3.12f1"`）。
- API：`statics.open("SkillData"|"PassiveData"|"BuffData"|"EgoData"|"PersonalityData"|"EncounterData"|"ItemData"|"StoryData"|…)` → 只读映射 + `query(field=…)`；`statics.patch(id, {"HP":…})` → 产出可打包的覆盖补丁。
- 与代码生成联动：`statics.enum_of("BUFF_UNIQUE_KEYWORD")` 直接从数据推导，避免硬编码枚举。

### 5.4 `limbuspy.game.assets`（常规模组主力，纯 Python）

```
assets.bundle(name) / assets.find(key_pattern) / assets.search_in_bundles(type=Texture2D|AudioClip|TextAsset)
assets.export(obj) / assets.replace(obj, bytes) / assets.patch_texture(...) / assets.patch_audio(...)
assets.build(patch_dir, out_bundle)      # 产出可被 Lethe 自定义 bundle 加载的补丁包
assets.catalog()                          # tools/parse_catalog.py 的 API 化（绝不裸 grep 二进制）
```
配套：精灵/字体（TMP）替换、UI prefab 改字段、CG/BGM 替换（工作区已有 `inject_cg_demo.py`、`LimbusBgmPlayer` 可复用）。

### 5.5 `limbuspy.game.locale` / `content` / `ui` / `audio` / `rpg` / `save`

- `locale`：读 `LimbusCompany_Data/lang/<set>`、动态 locale（等价 MTCustomScripts 的 `dlactivatepath`/`dlsetonepathvalue`）、缺键自动补（复用 `mods/AutoTranslateLocale`）。
- `content`：注册新技能/被动/Buff/技能脚本/名片/Banner（工作区已有 `banner_app`、`SinRegistry`、`static_mod_test` 先例）。
- `ui`：IMGUI overlay、toast/对话框、按键注入（复用 `mods/_shared/LimbusModKit/Ui/UiKit.cs` 的思路）、调试面板（参考 `docs/DEBUG_PANEL_MOD_DESIGN.md`）。
- `audio`：播放/替换/自定义音效（Modular 的 `sound`/`playcustomsound` 能力 + `LimbusBgmPlayer`）。
- `rpg`（镜像迷宫/RPG 模式）：`RpgMiniMap`/`RpgWeapons`/`RpgBlackSquad` 已证明该模式可深度改；API 面独立成子模块，v2 落地。
- `save`：只读快照优先；写入需显式 `--i-know-what-im-doing`（涉及 `docs/REPORT-2026-09-28-RPG-save-pipeline-and-mod-feasibility.md` 记的风险）。

### 5.6 `limbuspy.modular`（兼容层，§6 详述）

### 5.7 `limbuspy.mod`（脚本作者天天写的东西）

```python
from limbuspy import Mod, hook, consequence, acquirer, lua_function, config

class MyMod(Mod):
    name = "sinking_overload"
    version = "1.0.0"
    requires = ["limbuspy>=1.0"]           # mod 间依赖
    cfg = config(sinking_gain=2, verbose=False)

    @hook("OnSucceedAttack", heads=True)   # heads/tails/crit/keyword 过滤器，等价 Modular 的 TIMING:OSA(Head)
    def overload(self, ctx):
        u = ctx.target
        if u.has_buff("sinking"):
            u.add_buff("sinking", self.cfg.sinking_gain, 1)
            self.emit_sound("sinking_boom")

    @acquirer("my_power_bonus")            # 注册进 acquirerDict → Modular DSL 里可直接 my_power_bonus(...)
    def bonus(self, ctx, unit, n): ...

    @consequence("my_finisher")            # 注册进 consequenceDict → DSL: my_finisher(MainTarget,3)
    def finisher(self, ctx, target, power): ...

    @lua_function("mymod_stats")           # 注册进 luaFunctionDict → Lua 脚本可直接调
    def stats(self, ctx, unit): ...

    def on_load(self):  ...
    def on_unload(self): ...
    def on_save(self, state): return {"counter": self.counter}   # 热重载状态保持
    def on_restore(self, state): self.counter = state["counter"]
```

---

## 6. 与 Modular 的四层兼容

### L0 共享数据袋（v1）
- Python `modular.gdata.set/get/clear` ↔ Modular `GlobalLuaValues`（`SetGlobalValue/GetGlobalValue`），语义一致。
- Python `ctx.udata` ↔ Modular `LuaUnitDataKey.LuaUnitValues` / `setdata`/`getdata`。
- 红（同键冲突时以 Modular 侧为准，Python 只镜像，避免双写分歧）。

### L1 Modular → Python（v1，关键）
内核在 Modular 之后 `Load()`（`[BepInDependency("GlitchGames.ModularSkillScripts")]`），注册：

```
consequenceDict["py"]     = PyConsequence()   →  DSL:  py(mymod.finisher,MainTarget,3)
acquirerDict["pyq"]       = PyAcquirer()      →  DSL:  VALUE_0(pyq(mymod.bonus,Self,2))
luaFunctionDict["pycall"] = PyLuaFunction()   →  Lua:  pycall("mymod.stats", unit)
```

于是**任何现有 Modular 内容**（JSON 里的 `ScriptName: "Modular/TIMING:WhenUse/py(...)"`、被动 `requireIDList`、Buff 脚本）都能一句话调到 Python，而且享受 Modular 已验证的全部时机与目标选择器。**这是"融入生态"最省力也最稳的一条路**。
参数约定：`fn` 为 `mod.function` 字符串，其余参数按字符串传入（与 Modular 的 `circles[]` 同构），Python 侧按声明签名自动转换。

### L2 Python → Modular（v1）
- `modular.consequence("buff", ctx, ["MainTarget","sinking","1","1"])` → 直接调 `MainClass.consequenceDict["buff"].ExecuteConsequence(modsa, …)`。**等于免费获得 230 个已实战验证的战斗动作**（装 MTCustomScripts 时再+150）。
- `modular.acquire("getbuffcount", ctx, [...])` 同理。
- `modular.run("TIMING:WhenUse/buff(...)/VALUE_0(...)", ctx)` → Python 造一个 `ModularSA`，设 `activationTiming = actevent`，`SetupModular(...)` + `Enact(...)`。**Python 可以在任意自选时机执行任意 Modular 指令串**。
- `modular.dsl` 提供 builder：`Modular().timing("OnSucceedAttack", heads=True).value(0, "getdmg").consequence("dmgmult", ["MainTarget","150"]).render()`。

### L3 Python 生成合法 Modular 内容（v1.5）
`content.attach_script(skill_id=601101, "Modular/TIMING:OSA/py(mymod.fn)")` + `statics.patch(...)` + `assets.build(...)` → **一个 Python mod 的产物就是一个标准 Modular mod**（别人不装 LimbusPy 时装不了，但结构和纯 Lua mod 完全同构，可混装、可迁移）。

### L4 Python 侧实现 Modular DSL 解释器（v2，可选）
用 Python 重实现 `SetupModular/ProcessBatch/DoMath/GetTargetModel` 的语义，接管以 `Modular/` 开头的脚本 → 现存 Modular 内容立刻获得**热重载 + 断点 + 单步 + 单元测试**。风险：语义要 1:1（尤其 `GetTargetModelList` 的目标字符串方言），所以放到最后，且做成"可切换后端"（`modular.backend = "cs" | "py"`）。

---

## 7. 热重载设计

### 7.1 流程

```
文件 mtime 变化 (watchdog 或 500ms 轮询)
  → 计算受影响 mod 集合（含依赖它的 mod，按 DAG 拓扑序）
  → C# RELOAD{phase=QUIESCE, gen}      # 主线程 pump 暂停派发该 mod 的回调（新调用进队列）
  → 等待在途钩子结束（≤ 200 ms，超时则丢弃其结果）
  → Python: on_save() → 卸载注册（装饰器表、pinned 句柄、定时器、线程）→ on_unload()
  → importlib.invalidate_caches() + 重新 import（以同进程重新执行为准）
  → on_load() → on_restore(state)
  → C# RELOAD{phase=RESUME, gen+1}
```

### 7.2 四个必须解决的细节

1. **回调身份与代际**：注册项键为 `(mod, name)`，携带 `gen`；内核只承认最新 `gen` 的结果，旧 `gen` 的迟到结果静默丢弃（幂等），避免"重载瞬间的幽灵伤害"。
2. **战斗中途重载**：`ctx.state`（每 hook 的局部计数/累加器）由框架托管并跨重载保留；作者也可以 `self.state["x"]` 显式持久化。战斗对象句柄**一律不跨重载**（重载后全部失效，强制重新取）。
3. **Harmony 补丁**：Python 声明的补丁登记在 `PatchHub` 表里，`PATCH_DEL` 只是从表移除（补丁本身留着但直通），因此**不需要 unpatch/repatch**，也不影响别人的补丁。
4. **C# 侧无需重启的边界**（诚实清单）：
   - ✅ 任意方法/字段/属性读写；任意方法的 Harmony prefix/postfix/replace；新时机注册；Modular 注册表增删；Python 派生的 ability 子类（预注入虚拟转发基类）；资产/静态数据/UI overlay。
   - ⚠️ 需要"新的托管类型"或"新的原生钩子"的场景（自定义 struct 布局、部分自定义 enum 注入若 Lethe 未提供运行时 API）→ 需写 C# shim 或重启。P0 探针会量化这条边界。

### 7.3 崩溃与熔断

- Python 死亡 → 内核立即 `pass_through`，可选自动重启（默认开，退避 1s/3s/10s，3 次后停）；
- 单 hook 抛异常 → 记录 + 该 hook 本帧跳过，其它 hook 继续；
- 单帧 Python 超预算 → 熔断该帧剩余 hook，累计 10 帧超预算 → 自动禁用该 mod 并 UI 提示；
- 死锁保护：正规锁 + 所有等待都有 deadline；内核任何等待上限 250 ms。

---

## 8. 生态集成（需求 4 的两种读法都做）

### 8.1 "继承"（脚本作者视角）
- `class MyMod(Mod)` + 装饰器；`limbuspy.mod.Mod` 提供生命周期、配置（`config()` 自动生成 IMGUI 配置页）、事件总线、状态持久化、依赖解析、`self.logger`。
- 多层继承：`BaseBattleTweak` → `MyBattleTweak`；框架按 MRO 收集 hook（父类注册的 hook 自动生效），满足"方便的继承"。

### 8.2 "集成"（mod 生态视角）
- **发布形式三种**：① 目录式（`BepInEx/plugins/LimbusPy/mods/<name>/`，热重载首选）② wheel（`pip install limbuspy-mod-x`，走 `entry_points: limbuspy.mods`）③ 打包成 `.lpmod`（zip + manifest，一键安装）。
- **反向暴露**：任何 C# mod 只要加 `[BepInDependency("LimbusPy.Bridge")]` 并写一行 `PyBridge.Expose("MyMod", "DoThing", fn)`，其功能即可从 Python 调用（`limbuspy.external.mymod.do_thing(...)`）。反过来 Python 也能把自己的函数暴露给 C#（`ctx.expose(...)`）。
- **共存**：Harmony id 前缀 `LimbusPy.*`；Modular 注册键用 `py`/`pyq`/`pycall` 等短名并给出冲突时的优先规则（后加载者覆盖，且 `doctor` 会报冲突）；时机段号用 7300+/9000+ 未占用段。
- **示例 mod**：随包发 6 个示例，直接对着参考工程写：① 纯 Modular 风格（`py()` 挂技能）② 战斗数值改写（伤害公式）③ 自定义 buff 行为（Lethe 数据 + Python 逻辑）④ 资产替换（立绘/CG）⑤ 本地化自动补全 ⑥ Python 与 Lua 混合脚本。

### 8.3 与现有工作区基础设施对接
- `tools/parse_catalog.py` → `assets.catalog()` 的底层（**禁止裸 grep catalog** 的纪律写进 API 文档）。
- `auto_offset_pipeline/auto_offset_watcher.py` → 检测到新 `GameAssembly.dll` 时，除 cheat 偏移外追加跑 `tools/gen_limbuspy_stubs.py` + `limbpy doctor` smoke。
- `LetheLauncher-Distribution-7` + `headless/` → 无人值守集成测试（离线启动 → 进战斗 → 断言）。

---

## 9. 类型生成与版本漂移

```
Il2CppDumper-release/dump.cs  (87 MB, 结构化签名)  ─┐
il2csharp/out/limbus/<label>/*/*.cs (含方法体)      ─┼→ tools/gen_limbuspy_stubs.py
script.json (RVA) / il2cpp.h (struct 布局)          ─┘
        │
        ├─ limbuspy/_gen/<build>/types.pyi     # IDE 补全 + 类型检查（对应 MTCSLuaDef.lua 的角色）
        ├─ limbuspy/_gen/<build>/enums.py      # 枚举常量（BUFF_UNIQUE_KEYWORD / ATTRIBUTE_TYPE / …）
        ├─ limbuspy/_gen/<build>/structs.py    # il2cpp struct ↔ dict 字段映射（来自 il2cpp.h）
        ├─ limbuspy/_gen/<build>/methods.json  # 方法索引（内核与 Python 共用同一份）
        └─ limbuspy/_gen/<build>/caps.json     # 能力清单（P0 探针实测哪些可用）
```

- **不对 74 871 个文件全量生成**：以"可达类型"（战斗/UI/资产/本地化 + 数据模型，约 3–5k 类型）为界，按需扩展 `--include-ns`。
- **build 分区**：`_gen/<build_hash>/` + `_gen/current` 软链；握手时比对 build hash，不匹配则**仍可运行**（反射保底），但 doctor 打红字并给 `limbpy stubs --refresh`。
- **能力自检**（`limbpy doctor`）：逐项探测关键方法是否存在（`BattleUnitModel.GetHP`、`SkillModel.SkillAbilityList`…），输出 diff 报告 → 版本更新后 3 分钟知道"哪条 API 断了"，而不是等玩家报崩。

---

## 10. 工程布局

```
LimbusPy/                                  # 新顶层目录（独立可发布 git 仓库，与 ModularLimbis 平级）
├─ kernel/LimbusPy.Bridge/                 # C# BepInEx 6 IL2CPP 插件（唯一需要编译的产物）
│  ├─ Bridge/{PipeServer,FrameCodec,Protocol,Supervisor}.cs
│  ├─ Runtime/{MainThreadPump,HandleTable,Arena,ValueCodec,Diagnostics}.cs
│  ├─ Reflect/{ManagedInvoke,NativeMetadata,ExactMethodResolver}.cs   # 后者复用 BotQuixoteNative 的思路/代码
│  ├─ Patch/{PatchHub,GenericPatch,DynamicTiming}.cs
│  ├─ Glue/{ModularGlue,ExposeRegistry,VirtualForward}.cs
│  └─ Host/{AssetHost,UiOverlay}.cs        # 极少量必须在 C# 侧的能力
├─ python/
│  ├─ limbuspy/{runtime,il2cpp,game,modular,mod,devkit}/…
│  ├─ limbuspy/_gen/<build>/…
│  └─ pyproject.toml                        # 打包 + entry_points
├─ tools/{gen_limbuspy_stubs.py,doctor.py,pack.py,deploy.py,bridge_bench.py,replay.py}
├─ mods/{examples…}
├─ tests/{unit(无游戏),integration(有游戏),bench}/
├─ docs/{getting-started,api/,modular-compat,hot-reload,faq}
└─ dist/                                    # Bridge.dll + python payload + wheels + mods
```

**部署形态**（`BepInEx/plugins/LimbusPy/`）：

```
LimbusPy/
├─ LimbusPy.Bridge.dll
├─ config.toml                # 传输选择、预算、python 路径、熔断策略
├─ python/                    # 内置 CPython embeddable（3.12/3.13, ~40 MB）+ wheels（msgpack/UnityPy/pillow）
├─ site/limbuspy/…            # 我们自己的包
├─ mods/<name>/               # 用户 mod
└─ .state/                    # 热重载持久化 + 日志 + trace
```

交付策略：**内置 embeddable 为默认**（版本可控、离线可用、不受用户 Python 环境污染），`config.toml` 可切 `python_mode = "system"` 用本机 3.11/3.13（本机已确认存在 3.11.9 / 3.14rc）。

---

## 11. 分阶段计划（每阶段有可执行验收）

### P0 — 探针（2–4 天，决定架构细节）
| 探针 | 内容 | 通过判据 |
|---|---|---|
| A 管道 | C# NamedPipeServer ↔ Python 客户端，握手/心跳/日志/重连 | 30 分钟不掉线；kill Python 后游戏无异常 |
| B 主线程反射 | 通过 pump 调 `Singleton<BattleObjectManager>.Instance` 读单位 HP；试 `MethodInfo.Invoke`、字段读写、枚举 | 战斗内能读到真实 HP 并与 UI 一致 |
| C 基准 | 10 000 次 ping / 1000 次字段读 / 100 次快照 | 满足 §4.4 或给出后备方案数据 |
| D Modular 注入 | `consequenceDict["py"]` 注册转发器，真实战斗中经由 `Modular/` 脚本触发一次 | Python 侧收到回调并能改一个可见数值（如回血） |

**任一项失败 → 启用对应后备**（TCP / C# typed 绑定 / 内嵌最小解释器），并在本文件记录决策。

### P1 — 内核 v0（1–1.5 周）
管道+帧+协议骨架、pump、句柄/arena、反射调用、日志、配置、Supervisor、`bridge_bench`。
**验收**：Python 端 `from limbuspy import runtime; unit = runtime.game().battle.units()[0]; print(unit.hp)` 能跑；基准达标。

### P2 — Python 运行时 + 类型层（1–1.5 周）
msgpack codec、错误模型、arena API、batching 上下文、`_gen` 生成器、`limbpy stubs`、`limbpy doctor`。
**验收**：`.pyi` 生成完成（≥3k 类型），IDE 内能补全 `ctx.target.hp`；`doctor` 输出能力报告。

### P3 — 战斗 API 覆盖（2 周）
按 §5.2 六组（unit/skill/coin/buff/damage/clash/action/stage/resource/targeting）实现 facade，逐项对齐 Modular 的语义并写 pytest（无游戏）+ 集成断言（有游戏）。
**验收**：Modular 的 111 acquirer 语义 100% 有 Python 等价物；117 consequence 中战斗相关 ≥90% 有 facade 或直通；示例 mod ②③ 可运行。

### P4 — Modular 双向兼容（1 周）
L0/L1/L2 全量 + `modular.dsl` builder + 冲突检测。
**验收**：一个纯 Modular JSON 内容通过 `py(...)` 调 Python 生效；Python 侧 `modular.consequence("buff",…)` 生效；`modular.run()` 执行任意指令串生效。

### P5 — 热重载 + 开发工具（1 周）
watch/reload/代际/状态保持、`limbpy repl`（连运行中的游戏）、trace/profiler、录制回放（把 `EVBATCH` 录成文件，离线重放到 mock 上做回归）。
**验收**：战斗中改 `.py` → 3 秒内新逻辑生效且不打断战斗；10 次连续重载无句柄泄漏（`doctor --handles`）；录制的战斗可离线重放且断言一致。

### P6 — 常态 modding API（2 周，可与 P3 并行）
`statics` / `assets` / `locale` / `content` / `ui` / `audio`（`rpg`/`save` 放 P7 之后）。
**验收**：示例 mod ④⑤ 可运行；能产出一条能被 Lethe 加载的自定义 bundle。

### P7 — 生态、打包、文档、CI（1.5 周）
三种发布形态、`Expose` 反向暴露、6 个示例、文档站、CI（stub 生成 + 无游戏测试 + `doctor` 冒烟）、与 `auto_offset_watcher.py` 串联。
**验收**：干净机器上按文档 15 分钟跑通第一个 mod；新版本 GameAssembly.dll 到达后流水线自动产出新 stubs + diff 报告。

**里程碑**：P0+P1+P2 结束 = "能在 Python 里读写战斗"（约 2.5–3.5 周，第一个有价值版本）。

---

## 12. 风险与缓解

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R0 | Il2CppInterop 反射不可用/值类型封送坑 | 中 | 高 | P0 双路探针：托管 `MethodInfo.Invoke` ↔ 原生 metadata `runtime_invoke`；struct 一律走生成映射 |
| R1 | 主线程同步等待导致卡帧/死锁 | 中 | 高 | 嵌套 pump 深度≤1 + 全部等待有 deadline + 超预算熔断 + 批量派发 |
| R2 | 版本漂移（IL2CPP 符号/签名变化） | 高 | 中 | 生成器 + `doctor` 能力探测 + build 分区 + 反射兜底 + 接 `auto_offset_pipeline` |
| R3 | 句柄泄漏/悬垂 | 中 | 中 | arena 默认 + pinned TTL + `WasCollected` 校验 + `doctor --handles` + 泄漏回归测试 |
| R4 | Python 进程崩溃 | 中 | 低 | 看护 + 退避重启 + `pass_through` + UI 提示；游戏永不因脚本崩 |
| R5 | 与 Modular/MTCustomScripts/其它 mod 冲突 | 中 | 中 | 注册键前缀 + 不 unpatch 他人补丁 + Harmony id 隔离 + `doctor --conflicts` |
| R6 | 性能不达标（复杂战斗高频 hook） | 中 | 中 | 批量 + 快照 + 帧预算 + 热点下推 typed 绑定 + 共享内存（v2） |
| R7 | 反作弊/私服一致性 | 低 | 中 | 纯客户端、不动服务端协议；写进文档与 `docs/ANTICHEAT.md` 口径一致 |
| R8 | 双份数据源分歧（Python 与 Lua 各存一份） | 中 | 低 | L0 单向镜像，Modular 侧为权威 |

---

## 13. 需要拍板的决策（建议值已给）

1. **命名**：`LimbusPy` / 包名 `limbuspy` / 内核 GUID `LimbusPy.Bridge`（依赖 `Lethe`，软依赖 `GlitchGames.ModularSkillScripts`）。**建议采纳**。
2. **Python 交付**：内置 embeddable（默认）+ 可切系统 Python。**建议采纳**。
3. **传输**：命名管道为主 + TCP 回退（可配置）。**建议采纳**。
4. **同步语义默认**：混合 —— 需要返回值的 hook 同步（20 ms 上限），纯副作用 hook 走批量延后。**建议采纳**。
5. **v1 范围是否包含 L4**（Python 解释 Modular DSL）：**建议不含**，放 v2。
6. **`statics/assets/locale/content` 是否同一包发布**：**建议同仓分模块**（`limbuspy.game.assets` 依赖 UnityPy，若玩家不装则 import 时惰性失败，不影响战斗类 mod）。
7. **基线版本**：以当前 `Modular 5.1.15` + `il2csharp/out/limbus/gameonly`（v39-20261022 构建，metadata sha `e17855…`）为首个绑定基线。
8. **是否需要 RPG/镜像模式深度 API 在 v1**：**建议 v2**（先做 `battle` + `statics` + `assets` + `locale`，覆盖绝大多数需求）。

---

## 14. 附：与既有文档/工程的关系

- **取代/超越** `LETHEJS_FEASIBILITY_AND_PLAN.md` 的技术路线（进程内 JS → 进程外 Python），但其第 1 章的 Modular 能力全景仍是有效调研，本文 §1 已复用并校正（例如 `ModularSA.Enact` 的 `activationTiming` 门控、Lua 全量注册机制）。
- **复用** `mods/BotQuixoteNative/bridge/host/Bindings/ExactMethodResolver.cs` 的"精确方法定位 + 线程属主 + 代际失效"设计（原生 metadata 路径）。
- **复用** `mods/_shared/LimbusModKit`（UI/日志）、`tools/parse_catalog.py`（catalog 解析纪律）、`auto_offset_pipeline`（版本更新自动流水线）、`skills/limbus-battle`（外部 agent 驱动战斗的 API 形态参考）。
- **纪律**：本作更新频繁，任何"API 已失效"的结论必须先写进 `docs/` 并标注证据（哈希/地址/复现脚本），不得凭旧偏移下结论。
