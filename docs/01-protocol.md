# LimbusPy 线协议规范 v1（LPCP）

> 状态：**已冻结（v1.0）**。任何实现必须通过 `tools/conformance.py` 的双向一致性测试。
> 变更规则：主版本不兼容即拒绝连接；次版本只增不改（新增消息类型 / 新增 map 键）；未知消息类型必须忽略并计数。

---

## 1. 传输层

| 项 | 规范 |
|---|---|
| 主传输 | **一对单向 Windows 命名管道**：`\\.\pipe\limbuspy.<pid>.toPy`（内核→Python，内核写、Python 读）与 `\\.\pipe\limbuspy.<pid>.fromPy`（Python→内核）。服务端 = C# 内核（游戏进程），单实例。 |
| 备选传输 | `127.0.0.1:<port>`（仅 loopback，端口写在内核 `config.toml`；用于调试/抓包） |
| 权限 | 管道用 `PipeOptions.CurrentUserOnly`（仅当前用户可连）。 |
| 连接数 | 1（第二个连接被拒绝，错误码 `busy`）。 |
| 生命周期 | 内核启动即监听；Python 由内核 spawn 或外部 attach；任一侧正常关闭时先发 `BYE`，再关闭写侧，读侧延迟关闭（见 §1.1）。 |

### 1.1 为什么是两条管道而不是一条双工管道

**实测结论（2026-11-01，裸 Python，未经过本桥）**：用 `open(r'\\.\pipe\x', 'r+b')` 打开的**同步**管道句柄，当线程 A 阻塞在 `read()` 时，线程 B 的 `write()` 会失败并返回 `ERROR_INVALID_PARAMETER (EINVAL)`，同时 A 的 `read()` 立即返回 0（伪 EOF）。

```
reader thread: read(4) -> 4 bytes (HELLO)
reader thread: read(151) -> 151 bytes
main thread:   write(68) -> OSError [Errno 22] Invalid argument
reader thread: read(4) -> b''          # 伪 EOF
```

这不是 LimbusPy 的 bug，而是 Windows 同步管道句柄不允许"挂起读 + 并发写"。

三种方案对比：

| 方案 | 结论 |
|---|---|
| 单双工管道 + 读线程 | **不可用**（上表实测） |
| Python 侧改用 overlapped I/O（ctypes `CreateFileW` + `ReadFileEx`） | 可行但把整个客户端拖进 Windows 异步 I/O 细节，且 `asyncio` proactor 才能舒服地做 |
| **两条单向管道（采用）** | 读写各用一个句柄，无共享、无锁竞争、保留管道全部优点（无端口、无防火墙弹窗、当前用户 ACL） |

另一处相关细节：Python 侧关闭时**先关写侧**（对端随即 EOF 并关闭其端），读侧由守护线程延迟关闭——直接在另一线程关闭正阻塞在 `ReadFile` 上的读句柄会死锁。


## 2. 帧格式

```
+--------+--------+--------------------------+
| u32 LE | u8     | payload                  |
| length | type   | msgpack（可 0 字节）      |
+--------+--------+--------------------------+
length = 1 + payload_len        （包含 type 字节本身）
1 <= length <= 8 MiB            （否则视为协议错误，断开）
```

- **字节序固定小端**。
- payload 为 **msgpack map**（字符串键）或空。解不出 map 的 payload 允许为任意 msgpack 值（保留给扩展）。
- 每个连接内消息**有序**；同一 `id` 的请求/响应可乱序（异步），但 `id` 必须单调递增唯一。

## 3. 消息类型

| 值 | 名称 | 方向 | payload 关键字段 |
|---|---|---|---|
| `0x01` | `HELLO` | C#→Py | `proto, kernel, game_build, game_version, caps[]` || `0x02` | `READY` | Py→C# | `proto, py, mods[], stubs_build, caps[]` |
| `0x03` | `CALL` | Py→C# | `id, target, method, args[]` |
| `0x04` | `CALLRESULT` | C#→Py | `id, ok, value` \| `err{code,msg,detail}` |
| `0x05` | `EVBATCH` | C#→Py | `seq, frame, events[]{timing, gen, ctx, snapshot?}` |
| `0x06` | `ACT` | Py→C# | `seq, actions[]{event, effects[], retval?}` |
| `0x07` | `SYNCALL` | C#→Py | `id, fn, ctx, deadline_ms` |
| `0x08` | `SYNCRESULT` | Py→C# | `id, ok, value` \| `err` |
| `0x09` | `PATCH_ADD` | Py→C# | `id, spec{class, method, param_types[], kind, hook}` |
| `0x0A` | `PATCH_DEL` | Py→C# | `id, patch` |
| `0x0B` | `REG` | Py→C# | `id, table∈{consequence,acquirer,lua,timing}, name, fn` |
| `0x0C` | `UNREG` | Py→C# | `id, table, name` |
| `0x0D` | `RELOAD` | C#→Py | `gen, phase∈{quiesce,resume}, mods[]` |
| `0x0E` | `STATE` | 双向 | `id?, op∈{save,load}, mod, data` |
| `0x0F` | `LOG` | 双向 | `level, mod, msg` |
| `0x10` | `STATS` | 双向 | `id?, counters{}` |
| `0x11` | `PING` | 双向 | 空 |
| `0x12` | `PONG` | 双向 | 空 |
| `0x13` | `ERROR` | 双向 | `code, msg, fatal` |
| `0x14` | `DOCTOR` | 双向 | `id, report{}` |
| `0x15` | `BYE` | 双向 | `reason` |

`PATCH_ADD/PATCH_DEL/REG/UNREG/STATE/DOCTOR` 为**请求类**消息：带 `id`，对端必须回 `CALLRESULT`（成功 `ok:true`，失败 `ok:false + err`）。这样 Python 侧可以同步等待注册完成，避免"注册了但还没生效"的竞态。

## 4. 值编码（msgpack 子集）

只允许以下类型过桥，**其它一律 `marshal` 错误**：

| 类型 | 编码 |
|---|---|
| null | `0xc0` |
| bool | `0xc2` / `0xc3` |
| int | 规范最小表示：非负用 `uint8/16/32/64`（`0xcc..0xcf`），负数用 `int8/16/32/64`（`0xd0..0xd3`）；`|v| < 128` 走 fixint（`0x00-0x7f` / `0xe0-0xff`） |
| float | 打包一律 `float64 0xcb`；解码接受 `float32 0xca` 与 `float64` |
| str | UTF-8，`fixstr 0xa0-0xbf` / `str8 0xd9` / `str16 0xda` |
| bin | `bin8 0xc4` / `bin16 0xc5`（bytes） |
| array | `fixarray 0x90-0x9f` / `array16 0xdc` |
| map | `fixmap 0x80-0x8f` / `map16 0xde`；**键必须为 str**（内部数据允许非 str 键，但跨桥 payload 顶层必须 str 键） |

**不支持**：ext、str32/bin32/array32/map32、timestamp。解码遇到 → `unsupported`。

### 4.0 大小限制（v1.0 现状）

| 项 | 上限 | 超限行为 |
|---|---|---|
| 单帧 | 8 MiB | 断开连接（`FrameProtocolException` / `FrameError`） |
| 单个 str / bin | 64 KiB | `marshal` 错误（**不截断**，避免静默损坏） |
| 单个 array / map | 65535 项 | `marshal` 错误 |

大块数据（资产、图片、长文本）在 v1.1 引入**分块传输**（`CHUNK` 消息 + `transfer_id`）或共享内存通道；在此之前应当在 Python 侧完成读写（资产类需求完全不需要过桥，UnityPy 是纯 Python 的）。


### 4.1 类型化引用（handle）

跨桥对象用自描述 map 表示：

```jsonc
{"t": "unit",  "h": 305419896}      // t=kind, h=u32 handle
{"t": "null"}                        // 显式空引用
```

- `kind ∈ {unit, skill, coin, buff, action, stage, model, ability, sound, asset, sa, obj}`。
- 接受裸 `int` 作为入参（内核按 handle 表推断 kind），但**出参必须用 map 形式**，避免 Python 侧猜类型。
- 句柄作用域由内核管理（arena / pinned / frame），详见 `02-architecture.md §5`。

## 5. 握手与版本协商

```
C#: HELLO {proto:"1.0", kernel:"0.1.0", game_build:"e6312d92", game_version:"1.84.0", caps:[...]}
Py: READY {proto:"1.0", py:"3.13.14", mods:[], stubs_build:"e6312d92", caps:[...]}
```

- 任一侧 `proto` 主版本 ≠ `1` → 发送 `ERROR{code:"proto", fatal:true}` 并关闭。
- `game_build` 与 `stubs_build` 不等 → **不阻断**，内核记 `stale_stubs` 警告，Python 侧 `imbutils.doctor` 打红字，反射保底仍可用。
- 握手 3 秒未完成 → 关闭连接。

### 5.1 能力（caps）

字符串集合，双方各自声明，交集即可用能力：

```
reflect.managed     托管 MethodInfo 反射可调用
reflect.native      原生 metadata MethodInfo* + runtime_invoke 可用
reflect.field       字段/属性读写
handle.arena        回调作用域句柄
handle.pinned       显式 pin/TTL
snapshot.v1         战斗状态快照
patch.table         表驱动泛化 Harmony 补丁
modular.registry    可写入 Modular 注册表
modular.run         可程序化执行 Modular 指令串
hot.reload          代际热重载
budget.frame        帧预算熔断
```

## 6. 回调语义（`EVBATCH` / `SYNCALL` / `ACT`）

### 6.1 批量副作用（默认，推荐）
C# 在每帧收集同一时机所有待触发的原生钩子，**一次性**发 `EVBATCH`；Python 执行完毕后回 `ACT`，C# 在**下一个安全点**统一应用。

```
C#: EVBATCH {seq:42, frame:1180, events:[
      {timing:"OnSucceedAttack", gen:7, ctx:{...快照...}},
      {timing:"OnSucceedAttack", gen:7, ctx:{...}}
    ]}
Py: ACT {seq:42, actions:[
      {event:0, effects:[{op:"buff.add", unit:{"t":"unit","h":11}, keyword:"sinking", stack:2, turn:1}]},
      {event:1, effects:[]}
    ]}
```

- `actions` 必须与 `events` 等长且按序对应；缺省视为无副作用。
- 无法在"下一个安全点"应用的 effect 必须被内核**丢弃并告警**（不是崩溃）。

### 6.2 同步返回（仅限必须立即影响的数值）
`SYNCALL` 由 C# 在主线程发出并**阻塞等待**（嵌套 pump，深度 ≤1，`deadline_ms` 默认 20，上限 250）：
- 超时 → C# 取 `fallback`（脚本注册时声明），计 `timeout` 指标；
- Python 侧在 `deadline_ms` 内未返回 → 同上，并记一次 `over_budget`；
- 嵌套（Python 在 hook 内又触发需要 C# 同步调用的游戏方法）→ **立即失败**，直接返回 fallback，避免死锁。

**设计取向**：`SYNCALL` 是逃生口，不是常规路径。绝大多数逻辑应走 `EVBATCH` + effect（原生钩子 + 效果队列）。这与"鼓励原生钩子、避免 Modular 指令串开销"一致。

## 7. 错误模型

```jsonc
"err": {
  "code": "stale_handle",         // 见下表
  "msg":  "handle 0x11a2 freed at frame 1181 (arena)",
  "detail": {"kind":"unit","slot":450,"frame":1181}
}
```

| code | 含义 | 脚本可见 |
|---|---|---|
| `proto` | 版本/帧格式错误 | 致命 |
| `busy` | 已有连接 | 致命 |
| `notfound` | 类型/方法/字段/注册键不存在 | 可捕获 |
| `ambiguous` | 重载解析出多个候选（需精确签名） | 可捕获 |
| `marshal` | 值类型不可过桥 | 可捕获 |
| `wrong_thread` | 非主线程调用游戏对象 | 可捕获（框架 bug） |
| `timeout` | 同步调用超时 | 可捕获 |
| `stale_handle` | 句柄已释放/对象已回收 | 可捕获 |
| `script` | Python 脚本自身异常 | 可捕获 |
| `unsupported` | 编码/能力不支持 | 可捕获 |
| `over_budget` | 超出帧/单次预算 | 指标 |
| `internal` | 内核内部错误 | 记录 + 熔断候选 |

**铁律**：C# 侧任何异常都必须被捕获并转成 `err` 回包，**绝不允许异常穿过 Harmony 补丁**。

## 8. 版本与兼容矩阵

| 组件 | 版本 | 说明 |
|---|---|---|
| 协议 | `1.0` | 本文档 |
| 内核 `LimbusPy.Bridge` | `0.1.0` | 与游戏 build 弱绑定（反射保底） |
| Python `limbuspy` | `0.1.0` | 与协议 `1.x` 兼容 |
| stubs | `<game_build sha8>` | 生成物，按 build 分区 |

Python 包对协议做 `proto.major == 1` 的前置校验；不匹配时给出可操作的错误信息（不要只说 "unsupported version"）。

## 9. 一致性测试（`tools/conformance.py`）

必须覆盖并通过的最小集合：

1. 帧边界：粘包、半包、超大帧拒绝、0 长度拒绝。
2. msgpack 双向字节级一致：nil/bool/int 全边界（-2^63、-33、-32、-1、0、127、128、2^63-1、2^64-1）/float64/str（含中文与 emoji 的 UTF-8）/bin/嵌套 array+map。
3. 握手：正常握手、主版本不匹配被拒、3 秒超时。
4. 请求/响应关联：并发 50 个 `CALL` 乱序返回（服务器故意打乱），全部正确配对。
5. 句柄：arena 释放后使用 → `stale_handle`；pinned 存活；`{t,h}` 与裸 int 均可作为入参。
6. 错误路径：不存在的 target/method → `notfound`；错误参数类型 → `marshal`。
7. `PING/PONG` 心跳 1000 次无泄漏、无错序。
8. 进程隔离：Python 被杀 → 服务端 500 ms 内感知并退出干净（harness 断言退出码）。
