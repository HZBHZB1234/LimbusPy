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
| `LimbusPy.Bridge.Core`（C#，**无 Unity 依赖**） | ✅ 帧编解码 / msgpack / 协议 / 句柄表 / PeerConnection / 传输 |
| 反射层（`Core/Reflect`） | ✅ 类型索引 / 值封送 / 重载解析 / `Describe` |
| Python `limbuspy.runtime` | ✅ codec / framing / protocol / transport / client / 错误模型 |
| 跨语言一致性测试 `tools/conformance.py` | ✅ **196 项全过**（反射层、真实管道、并发乱序、句柄 arena、阻塞通道、**Harmony 补丁表**、doctor、协议不匹配、真实 bootstrap 进程 ↔ 内核） |
| Python 单测 | ✅ 222 项 |
| BepInEx 内核插件（管道/握手/主线程 pump/IL2CPP 句柄/supervisor） | ✅ 编译通过（0 warning） |
| `limbuspy.bootstrap` / `limbuspy.mod`（自举 / Mod 基类 / 钩子 / 热重载） | ✅ 25 项 mod 层测试（含热重载代际、状态保持、失败隔离、补丁摘除） |
| 索引生成 + `limbuspy.il2cpp` + `devkit.doctor` + CLI | ✅ 真实 dump 26 126 类型 / 6 s；`python -m limbuspy doctor` |
| 表驱动 Harmony 补丁（`LimbusPy.Bridge.Harmony` + `Mod.patch`） | ✅ prefix/postfix、fail-open、按 binding 摘除、重载自动摘 |
| `limbuspy.game` 战斗 façade（`Battle`/`Unit`/`Skill`/`Coin`/`Buff`/`Action` + `ctx.session`） | ✅ 快照零往返读战场、成员名对着真实 dump 校验 |
| `limbuspy.modular`（单向兼容：`run`/`consequence`/`acquire` + DSL builder） | ✅ 反射驱动，无编译期依赖，`available` 降级 |

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

## 文档
- [`docs/01-protocol.md`](docs/01-protocol.md) 线协议 v1.0（冻结）
- [`docs/02-mod-authoring.md`](docs/02-mod-authoring.md) **mod 作者指南**（装饰器/ctx/热重载/调试）
- [`docs/03-architecture.md`](docs/03-architecture.md) 分层与内核插件
- [`docs/04-distribution.md`](docs/04-distribution.md) **两个分发构建与安装/升级**

## 工具链

`powershell
# 从 Il2CppDumper 的 dump.cs 生成索引 + 枚举 + 类型存根 + 能力报告（约 6 s / 26k 类型）
python -m limbuspy stubs --dump <dump.cs> --out python/limbuspy/_gen

# 自检：索引/解释器/mods/运行中的内核
python -m limbuspy doctor                       # 离线
python -m limbuspy doctor --pipe limbuspy.<pid> # 连运行中的内核（含 DOCTOR 实报）

# 查索引 / 列 mod / 版本
python -m limbuspy index "^BattleUnitModel$"
python -m limbuspy mods --mods mods
`

caps.json 是**版本漂移报警**：	ools/requirements/limbuspy.json 声明 LimbusPy 承诺的 API
（当前 10 个类型 / ~95 个成员，已对着实机 dump 全绿），新版本 dump 一生成就能看出哪条断了。

## 快速验证（不需要游戏）

```powershell
# 1. Python 单测
.venv\Scripts\python.exe -m pytest LimbusPy\python\tests -q

# 2. 跨语言一致性（会 dotnet build 那个 harness）
.venv\Scripts\python.exe LimbusPy\tools\conformance.py

# 只要编码一致性（最快）
.venv\Scripts\python.exe LimbusPy\tools\conformance.py --golden-only
```

已经实测确认：C# 与 Python 的 msgpack 编码**字节级一致**（38 个边界用例）；命名管道上握手 / 30 并发调用乱序配对 / 句柄 arena 语义 / 超时后的孤儿回包 / 协议不匹配致命拒绝全部通过；并且**真实的 python -m limbuspy.bootstrap 进程能被真实 C# 内核拉起**：握手 → 加载 mod → 钩子经 EVENTBATCH 触发 → 暴露函数经 SYNCALL 调用 → 内核关闭后干净退出。

---

## 设计要点（已被实测修正的两条）

1. **两条单向管道，而不是一条双工管道。** Windows 同步管道句柄在"挂起读 + 并发写"时写会失败（`EINVAL`）、读会伪 EOF。这是用裸 Python 复现出来的平台行为，不是桥的 bug → 见 [`docs/01-protocol.md §1.1`](docs/01-protocol.md)。
2. **句柄 slot 从 1 开始**（0 保留为"无句柄"），arena 作用域内分配的句柄在回调返回时整块释放，pinned 带 TTL —— Python 侧拿不到悬垂指针。

---

## 许可

与工作区其它部分一致：仅用于本地单人 modding / 研究用途，不修改游戏安装文件、不触碰服务端协议。


