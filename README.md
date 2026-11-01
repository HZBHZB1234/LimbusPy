# LimbusPy

**用 Python 写边狱公司（Limbus Company）模组。** 一个极薄、长期稳定的 C# BepInEx 内核负责"贴近游戏"，全部逻辑跑在独立 Python 进程里，通过命名管道上的 LPCP 协议通信；改 `.py` 存盘即热重载。

> 设计文档：[`docs/PLAN-2026-11-01-limbuspy-python-script-layer.md`](../docs/PLAN-2026-11-01-limbuspy-python-script-layer.md)（分阶段计划、需求映射、风险表）
> 线协议：[`docs/01-protocol.md`](docs/01-protocol.md)（**已冻结 v1.0**，实现必须通过一致性测试）

---

## 为什么

| | Modular（Lua DSL） | LimbusPy（Python + IPC） |
|---|---|---|
| 语言 | 单一指令串 DSL + 内嵌 Lua | 完整 CPython：类型提示、标准库、任意 pip 包 |
| 热重载 | 否（需重启游戏） | 是（改文件即重载，战斗中途也能重载） |
| 能力上限 | 已内置的 consequence/acquirer | 反射保底：凡是互操作程序集能到的，都能从 Python 调 |
| 崩溃影响 | 脚本异常即整帧失败 | Python 崩 → 内核熔断，游戏继续跑 |
| 生态 | 只能抄/改 DLL | wheel / 目录式 mod / 依赖管理 |

**与 Modular 的关系是单向的**：LimbusPy 兼容 Modular（能驱动它、能被它调用、能生成合法的 Modular 内容），但 Modular 不需要知道 LimbusPy 存在。原生钩子优先——Modular 指令串只作为兼容层与逃生口（它有额外解析与派发开销）。

---

## 现状（0.1.0 开发中）

| 组件 | 状态 |
|---|---|
| LPCP 协议 v1.0（帧、msgpack 子集、消息集、错误模型） | ✅ 冻结 |
| `LimbusPy.Bridge.Core`（C#，**无 Unity 依赖**） | ✅ 帧编解码 / msgpack / 协议常量 / 句柄表 / PeerConnection / 传输 |
| Python `limbuspy.runtime` | ✅ codec / framing / protocol / transport / client / 错误模型 |
| 跨语言一致性测试 `tools/conformance.py` | ✅ **116 项全过**（含真实命名管道往返、并发乱序、句柄 arena、超时、协议不匹配） |
| Python 单测 | ✅ 137 项 |
| C# 侧 Unity 接线（BepInEx 插件 / 主线程 pump / 反射） | ⏳ 下一步 |
| `limbuspy.mod`（Mod 基类 / 钩子 / 热重载） | ⏳ |
| `limbuspy.game.*`（battle / statics / assets / locale / content） | ⏳ |
| `limbuspy.modular`（单向兼容层） | ⏳ |

---

## 目录

```
LimbusPy/
├─ docs/                        协议与架构文档
├─ kernel/
│  ├─ LimbusPy.Bridge.Core/     Unity 无关的核心（可单测）
│  └─ LimbusPy.Bridge/          BepInEx 6 IL2CPP 插件（Unity 接线，唯一需编译的产物）
├─ python/
│  ├─ limbuspy/                 运行时装包
│  │  ├─ runtime/               传输 / 编解码 / 协议 / 客户端
│  │  ├─ _mock/                 无游戏替身（MockKernel）
│  │  └─ …                      后续：il2cpp / game / modular / mod / devkit
│  └─ tests/                    pytest（不依赖游戏）
├─ tests/LimbusPy.Bridge.Tests/ C# 内核 harness（真实管道服务端，供 conformance 驱动）
└─ tools/conformance.py         跨语言一致性测试
```

---

## 快速验证（不需要游戏）

```powershell
# 1. Python 单测
.venv\Scripts\python.exe -m pytest LimbusPy\python\tests -q

# 2. 跨语言一致性（会 dotnet build 那个 harness）
.venv\Scripts\python.exe LimbusPy\tools\conformance.py

# 只要编码一致性（最快）
.venv\Scripts\python.exe LimbusPy\tools\conformance.py --golden-only
```

已经实测确认：C# 与 Python 的 msgpack 编码**字节级一致**（38 个边界用例），命名管道上握手 / 50 并发调用乱序配对 / 句柄 arena 语义 / 超时后的孤儿回包 / 协议不匹配致命拒绝全部通过。

---

## 设计要点（已被实测修正的两条）

1. **两条单向管道，而不是一条双工管道。** Windows 同步管道句柄在"挂起读 + 并发写"时写会失败（`EINVAL`）、读会伪 EOF。这是用裸 Python 复现出来的平台行为，不是桥的 bug → 见 [`docs/01-protocol.md §1.1`](docs/01-protocol.md)。
2. **句柄 slot 从 1 开始**（0 保留为"无句柄"），arena 作用域内分配的句柄在回调返回时整块释放，pinned 带 TTL —— Python 侧拿不到悬垂指针。

---

## 许可

与工作区其它部分一致：仅用于本地单人 modding / 研究用途，不修改游戏安装文件、不触碰服务端协议。
