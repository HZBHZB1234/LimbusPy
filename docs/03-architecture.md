# 架构（as-built）

> 状态：随实现推进更新。当前覆盖 **内核核心 + 反射层**（可无游戏验证的部分）。

---

## 1. 分层

```
游戏进程                                        独立 Python 进程
┌──────────────────────────────────────┐       ┌────────────────────────────────┐
│ LimbusPy.Bridge (BepInEx 插件)        │       │ limbuspy                       │
│   Plugin / Config / Supervisor        │       │  mod/     Mod 基类 + 钩子       │
│   Unity/{MainThreadPump, Il2Cpp反射}  │  LPCP │  game/    battle/statics/assets │
│   PatchHub（表驱动泛化 Harmony 补丁） │◄─────►│  modular/ 单向兼容层            │
│   ModularGlue（写 4 张注册表）        │  管道 │  runtime/ 协议实现（已完成）     │
│ ─────────────────────────────────    │       │  devkit/  doctor/repl/pack      │
│ LimbusPy.Bridge.Core（无 Unity）      │       └────────────────────────────────┘
│   Framing / Codec / Protocol          │
│   HandleTable / PeerConnection        │
│   Reflect/{CallMarshaller,Invoker}    │
│   Transport/{NamedPipe pair,TCP}      │
└──────────────────────────────────────┘
```

**分层纪律**：凡是能在 Core 里实现且能无游戏测试的，就**不**放进 BepInEx 插件。插件只做四件事：Unity 主线程调度、IL2CPP 对象 ↔ 句柄、Harmony 补丁、Modular 注册表接线。

于是 `tools/conformance.py` 用同一套 Core + 普通托管对象，就能在没有游戏的情况下验证协议、封送、重载解析与句柄语义（144 项检查）。

---

## 2. 传输层

见 [`01-protocol.md §1`](01-protocol.md)：**两条单向命名管道**（`.toPy` / `.fromPy`）。
原因（实测）：Windows 同步管道句柄上"挂起读 + 并发写"会失败（`EINVAL` + 伪 EOF）。
TCP loopback 作为回退（调试、抓包）。

---

## 3. 句柄模型

| 概念 | 规则 |
|---|---|
| 编码 | `u32 = kind(高 4 bit) \| slot(低 28 bit)`，**slot 从 1 开始，0 = 无句柄** |
| kind | `unit/skill/coin/buff/action/stage/model/ability/sound/asset/sa/obj` |
| arena | 作用域内分配，`EndScope` 时整块释放（未 pinned 的）→ 脚本拿不到悬垂指针 |
| pinned | 显式提升，进 TTL（默认 5 分钟），`Sweep()` 回收 |
| 存活校验 | `TryResolve` 时回调 `isAlive`（插件里是 IL2CPP 对象是否已回收），失效即释放并报 `stale_handle` |
| 出参 | 对象一律 `{"t":kind,"h":n}` 自描述 ref |
| 入参 | `{"t","h"}` 永远当引用；**裸整数**在"引用型参数位"当句柄、在数值参数位当数字（`CallMarshaller.IsHandlePosition`） |

裸整数规则是踩过坑的：`reflect.set(unit, "Hp", 30)` 曾把 30 当成句柄。类型导向解释修掉了它，且 Python 侧正常路径始终发显式 ref。

---

## 4. 反射层（`Core/Reflect`）

三个部件：

1. **`AssemblyTypeIndex`**：类型名 → `Type`。插件传入 IL2CPP interop 程序集；harness 传入自身程序集。支持 `Ns.Outer.Inner` 与 `Ns.Outer+Inner`。
2. **`CallMarshaller`**：LPCP 值与托管值互转。
   - 入：显式 ref → 对象（并校验 `IsInstanceOfType`）；裸整数按参数位解释；枚举按名解析；`Nullable<T>` 解包；数值范围检查；数组/`List<T>` 逐元素递归。
   - 出：基础类型直传；枚举 → 名；`IDictionary` → map；`IEnumerable` → array；**值类型（struct）拒绝过桥**（避免把副本伪装成句柄）；其余对象 → 句柄 ref。
3. **`ReflectionInvoker`**：`Invoke / GetMember / SetMember / Describe / Types`。
   - 目标写法：`{"t","h"}`（实例） · `"global:Name"`（命名单例） · `"type:Ns.Type"`（静态） · `""` + `"Ns.Type::Method"`（静态快捷）。
   - 重载解析：先按 `Score`（参数位评分）排序候选，再逐个尝试真实转换，全部失败报 `marshal` 并附候选签名与失败原因；候选为空报 `notfound` 并附相近名字。
   - 目标方法抛异常 → `internal` + `detail.source="target"`（区分"游戏代码炸了"和"内核炸了"）。
   - `Describe` 供 `limbuspy doctor` / REPL 的 `dir()` 使用。

---

## 5. 请求/响应与回调

- 请求类消息带 `id`，对端回 `CALLRESULT`；`SYNCALL` 回 `SYNCRESULT`（关联器两种都接受，避免版本错配死锁）。
- **`SetAwaiting(...)`**：声明"我会等这个类型的帧"，该类型帧改为缓冲而非交给 handler。修掉 HELLO/READY 与 EVENTBATCH/ACT 的注册竞态。
- 超时后到达的回包按"孤儿"丢弃，不会串到别的请求上（并发 30 线程乱序回包已验证）。
- **结果帧只"完成"不"摘除" pending**：摘除权归等待方（RequestAsync / TryWaitResult 的 finally）。
  早期版本由读线程摘除，与阻塞等待方竞争，导致 pumps=0 式的假超时——这是被一致性测试抓出来的真 bug。

---

## 6. 内核插件（`kernel/LimbusPy.Bridge`）

已编译通过（0 warning / 0 error，对着游戏实机 BepInEx + interop 程序集）。

| 文件 | 职责 |
|---|---|
| `Plugin.cs` | `BasePlugin` 入口：配置、dispatcher、pump、句柄表、反射器、host、supervisor 的装配；`EnsureMainThread()` 线程守卫 |
| `Runtime/PumpBehaviour.cs` | 唯一拥有的 Unity 对象（`ClassInjector` 注入的 MonoBehaviour），`Update()` 按帧预算 drain 队列 |
| `Runtime/KernelHost.cs` | 管道接受循环 + HELLO/READY 握手 + 消息路由（引擎相关一律 `dispatcher.Post` 到主线程） |
| `Runtime/Il2CppRefStore.cs` | IL2CPP 对象 ↔ 句柄；存活判定用 `Il2CppObjectBase.WasCollected` + `Pointer != 0`；kind 由类型名推断 |
| `Runtime/InteropTypeIndex.cs` | 索引 interop 程序集（黑名单排除 System/BepInEx/Interop 等），miss 时一次性扫描 `BepInEx/interop/*.dll` |
| `Runtime/PythonSupervisor.cs` | spawn/attach；PATH 或内置运行时；stdout/stderr 转发；退避重启 + 直通 |
| `Config/BridgeSettings.cs` | 全部可调项（传输、预算、超时、嵌套深度、重启策略、日志） |
| `Compat/NullableAttributes.cs` | Il2CppDumper 假 mscorlib 缺少 `NullableAttribute..ctor` 的兼容垫片 |

设计取舍：
- **不编译期引用 Lethe / ModularSkillScripts**，只声明 soft dependency，Glue 走反射 → 它们升级不会让内核dll 加载失败。
- **只广告已实现能力**（`reflect.managed` / `reflect.field` / `handle.arena` / `handle.pinned` /
  `budget.frame`，以及服务存在时才广告的 `snapshot.v1` / `patch.table`），`modular.registry` 未实现前不谎报，
  Python 侧据此降级。
- 回复统一 `Task.Run` 写出，不在主线程上做管道 IO。

## 7. 实现状态与下一步

### 7.1 已完成（真机可用，编译级验证）

| 部件 | 状态 |
|---|---|
| 传输 / 帧 / 码编解码 / 句柄表 / 主线程调度 | ✅ 双单向管道、LPCP v1.1、arena+pinned、`MainThreadDispatcher` |
| 反射（`reflect.get/set/invoke/types`，含重载打分与枚举按名过桥） | ✅ 一致性测试 196 项全绿 |
| `PatchHub`（表驱动 Harmony，prefix + 7 类 postfix，fail-open，按 binding 摘除） | ✅ 派发即 `SYNCALL`，见 §6.3 |
| `SNAPSHOT`（`_allUnitDictionary` → 扁平 msgpack） | ✅ 内核已实现并**已广告** `snapshot.v1` |
| `@expose`（Python 函数被内核按名调用） | ✅ 供 REPL / 其它 mod / 未来 Modular `py()` |
| `limbuspy.bootstrap` / `mod`（装饰器、代际、热重载、状态保持、配置） | ✅ mock kernel + C# harness 双端验证 |
| `limbuspy.game`（battle façade / statics / assets） | ✅ 成员表由 requirements 对着真 dump 校验 |
| `limbuspy.modular`（单向：Python → Modular） | ✅ 全反射，无编译期依赖 |
| `limbuspy.il2cpp` / `devkit` / `cli` / stubgen / pack | ✅ `python -m limbuspy …` |

### 7.2 未接入（已知缺口，按优先级）

| 缺口 | 影响 | 计划 |
|---|---|---|
| **时机事件源（`EVENTBATCH`）** | `@hook("OnSucceedAttack")` 这类时机回调在真机上**不会触发**（只在 mock/harness 里跑通）；`@hook(sync=…)` 的同步泳道同理要由补丁 binding 指向它才走得通 | 挂一小组合**中心通知方法**（`BattleUnitModel.OnSucceedAttack` / `OnChangeHp` / `OnDie` / `OnBreak` / `OnRollOneCoin_AfterAttack` / `OnStartTurn_*` …）——Modular 的 205 KB 补丁也正是挂这些点。内核侧本地攒批、每帧 `Update()` 末尾发一次 `EVENTBATCH`，**不依赖 Modular 是否存在** |
| `ModularGlue`（反向，Modular → Python） | Modular 指令串里的 `py(...)` 调不到 Python（`REGISTRY_ADD` 目前返回 `unsupported`） | 独立程序集引用 Modular，把 `consequenceDict`/`acquirerDict`/`luaFunctionDict` 转发到 `SYNCALL`；按设计裁决非必需 |
| 真实 `ModularSA.Enact` 协同时序 | Python 直接 `run()` 一条指令串时，`activationTiming` 门控已处理，但嵌套派发顺序未在真机验证 | 与事件源一起验证 |
| 富内容层（`locale` / `content` / RPG / 镜像） | 不在 v1 范围 | v2 |


