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
- **只广告已实现能力**（`reflect.managed` / `reflect.field` / `handle.arena` / `handle.pinned` / `budget.frame`），`patch.table`、`modular.registry`、`snapshot.v1` 未实现前不谎报，Python 侧据此降级。
- 回复统一 `Task.Run` 写出，不在主线程上做管道 IO。

## 7. 待实现（下一步）

| 部件 | 说明 |
|---|---|
| `limbuspy.bootstrap` | 被 supervisor spawn 时自举：连管道、加载 mods、跑事件循环、崩溃转储 |
| `limbuspy.mod` | Mod 基类、装饰器、热重载代际、状态保持 |
| `limbuspy.game.*` | 战斗 / 静态数据 / 资产 / 本地化 / 内容 API |
| `PatchHub` | 表驱动泛化 Harmony 补丁（Python 声明 entry/exit，C# 不重编译即可增删） |
| `ModularGlue` | 注册 `py`/`pyq`/`pycall` 到 Modular 四张表；`timingDict` 扩展 |
| `SNAPSHOT` | 战斗状态扁平快照（减少往返） |
| `tools/gen_limbuspy_stubs.py` | dump.cs → `.pyi` / 枚举 / 方法索引 / 能力清单 |

