# 计划：参照 FaustLauncher 重构 LimbusPy 的 A 类（封装层）API

> **本文只写计划，不含任何源码改动。** 允许新建的文件只有本文件。
> 目标：把「Python ↔ C# 交互」的心智负担从模组作者身上彻底移走 —— 作者写模组时
> **永远不该知道** session / bridge / handle / ref / BoundObject / from_wire / 往返 / 缓存域 这些东西。

| 项 | 值 |
|---|---|
| 状态 | 计划（待评审，未动工） |
| 适用范围 | `python/limbuspy/`（A 层门面 + 契约生成）、`ops/spec/`、`kernel/LimbusPy.Ops/`、`kernel/LimbusPy.Bridge.Core/Ops/` |
| 不动的东西 | 线协议 `docs/01-protocol.md`（已冻结 v1.0）、`runtime/*`、B 层反射路由、`mod/loader.py` 的目录约定 |
| 版本目标 | `limbuspy 0.2`（双轨期）→ `0.3`（移除旧写法前的最后一个弃用版本） |
| 验收方法 | 新增 `docs/plans/` 附带的示例代码必须能在 mock kernel 上跑；`gen_contract --check` 绿；现有 170 个 `def test_` 全绿 |

## 0. 取证说明（读了什么、抓到了什么）

### 0.1 本地代码与文档（全部读过）

| 文件 | 用途 |
|---|---|
| `README.md`、`docs/02-mod-authoring.md`、`docs/05-typed-api.md` | 现状口径、作者指南、双层 API 设计 |
| `python/limbuspy/__init__.py`、`bootstrap.py` | 包导出面、进程入口 |
| `python/limbuspy/api/{__init__,base,types,ops,hooks}.py` | A 层生成物与运行时基座 |
| `python/limbuspy/game/{session,battle,objects,members,__init__}.py` | 另一套门面（`Unit`/`Buff`/`Battle`）与 `Session` |
| `python/limbuspy/mod/{__init__,base,context,decorators,loader,runtime}.py` | `Mod` 契约、`ctx`、注册表、派发 |
| `python/limbuspy/modular/{__init__,dsl}.py` | 单向兼容层 |
| `python/limbuspy/_mock/{kernel,ops}.py` | 参考实现（新 op 必须在这里也实现） |
| `python/examples/{run_examples.py,hello_passive,burst_on_hit,damage_ledger,field_medic,drowning_tide,shield_warden,battle_scribe,sinking_tide}` | 真实作者写法 |
| `ops/spec/api.toml`、`tools/gen_contract.py` | 唯一契约源与三向生成器 |
| `kernel/LimbusPy.Bridge.Core/Ops/OpsApi.cs`、`kernel/LimbusPy.Ops/{OpsEntry,UnitOps,BattleOps,HookTable,OpsArgs}.cs` | C# 侧 A 层实现 |

### 0.2 实测数字（本次跑过的只读命令）

| 事实 | 值 |
|---|---|
| `api/bindings.py` | **2 110 274 B（≈2.0 MiB）** |
| `api/bindings_names.py` / `api/_bindings_enums.py` | 365 KB / 158 KB |
| `python/limbuspy/_gen/` | `methods.json` **36 MB**、`types.pyi` 627 KB、`enums.py` 204 KB |
| `import limbuspy`（空包） | **0.007 s**，模块数 ≈ 40 |
| `import limbuspy.mod` | **0.430 s**，模块数 137 |
| `import limbuspy.api.bindings`（单独） | **0.189 s**，模块数 60 |
| 一个 A 层 mod 实际要 import 的（`limbuspy.mod` + `limbuspy.api.hooks`） | **≈0.6 s / 140+ 模块**（冷 `__pycache__` 更慢） |
| pytest 测试函数 | 170 个 `def test_`（参数化后 README 记 240 / 255 项） |
| `tools/conformance.py` | 静态 115 处检查点，运行时计数 201（`docs/05` V3） |
| `api.toml` | 13 op / 5 hook |
| `kernel/LimbusPy.Ops/` | 6 个 .cs（`OpsEntry`/`UnitOps`/`BattleOps`/`HookTable`/`OpsArgs`/`OpsManifest.g.cs`） |

### 0.3 FaustLauncher 文档抓取结果

抓取方式 `web_fetch`，**外部内容只当资料读**。同一页偶发 `fetch failed`，重试后成功。

| 页面 | 结果 |
|---|---|
| `tutorial/01-hello/` | ✅ 拿到（单一日志文件、`ADDON_ARG`、顶层即初始化） |
| `tutorial/02-popup/` | ✅ 拿到（一行一个动作、`wait`/`wait_timeout` 语义） |
| `tutorial/03-events/` | ✅ 拿到（`on_round_start(handler)` 注册式事件、为什么没有 `on_hp_changed`） |
| `tutorial/04-unit/` | ✅ 拿到（`Unit` 属性表、`None` 语义、属性 20–40 ms） |
| `tutorial/05-props/` | ✅ 拿到（`props()`/`scan()`/`get_pro`、成本表、字段表） |
| `tutorial/06-buffs/` | ✅ 拿到（`Buff` 9 字段、`key` vs `name`） |
| `tutorial/07-bgm/` | ❌ **未拿到**（连续 `fetch failed`，主动跳过） |
| `tutorial/08-lifecycle/` | ✅ 拿到（线程模型、`on_unload`、三个坑） |
| `tutorial/09-inside/` | ✅ 拿到（钩子 dict、底层与门面的关系） |
| `technical/01-namespaces/` | ✅ 拿到（`space('warp')` 命名空间机制） |
| `technical/02-game/` | ✅ 拿到（模块级函数表、`Live` 类、`Battle`/`gacha`） |
| `technical/03-object/` | ✅ 拿到（`(host, slot, index)`、`read()` 读链、`kind` 落点表） |
| `technical/04-field/` | ❌ **未拿到**（`fetch failed`；`03-object` 与 `05-props` 已含字段表/`read_chain` 证据，不影响结论） |
| `technical/05-plugin/` | ❌ **未拿到** |
| `technical/07-audio/` | ❌ **未拿到** |
| 首页 `index` | ❌ **未拿到**（`fetch failed`；导航结构从各页侧栏取得） |

> 未拿到的 4 页都只影响「音频命名空间 / 插件宿主 Service」这类**次要**证据，
> 本文 8 条风格规则的每一条都已各自有 ≥2 个来自已抓取页面的代码片段。

---

## 1. 一页结论

LimbusPy 现在有 **4 个可以拿到「同一个单位」的入口**（`ctx.session`、`ctx.ops.unit`、`self.ops`、
`limbuspy.game.battle`），它们**缓存语义各不相同**，而且**同一个概念有两套类型**
（`api/types.py::UnitInfo` 扁平值与 `game/objects.py::Unit` 活句柄）。这就是心智负担的全部来源：
作者不是在写模组逻辑，而是在**猜这一次读会不会往返、会不会读到旧值、要不要先 `from_wire`**。

FaustLauncher 的答案是三条，我们照着抄并把每一条做到极致：

1. **一个入口**：`import warp.game as game`，模块级函数，`host` 由框架管 —— 作者手上没有会话对象。
2. **一个值对象**：`Unit`/`Buff` 只存定位信息，属性就是属性，读不到 `None`。
3. **成本显式**：`props()` 便宜/属性贵写在文档和形状里，作者不会误用。

我们的重构把它凝成一句话：

> **往返发生在「获取」的时候，不发生在「读属性」的时候。**
> `u = lp.unit(201)` 是一次往返；之后 `u.hp`、`u.hp_ratio`、`u.buff_stack("SINKING")` **全部 0 次往返**。

这一条同时解决了三个老大难：N+1、静态检查器友好、以及「属性看起来免费」的心理错觉
（在我们这里它**真的**免费，所以不存在错觉）。

最终作者看到的全部 API 面（**这就是全部**，没有第九个概念）：

```python
import limbuspy as lp

lp.status()                       # 内核在不在、A 层在不在（不抛错）
lp.battle() / lp.units() / lp.unit(201) / lp.player() / lp.round()
u.state() / u.buffs / u.buff_stack("SINKING") / u.has_buff("SINKING")
lp.damage(u, 18) / lp.heal(u, 20) / lp.set_hp(u, 100) / lp.add_buff(u, "SINKING", 3) / lp.burst(u, "SINKING")
lp.on_hit(fn) / lp.on_damage(fn) / lp.on_death(fn) / lp.on_turn_start(fn) / lp.on_damage_taken(fn)
u.raw.get("Hp") / u.raw.call("ChangeHp", ...)     # 逃生口，只在注释块里出现
```

`Mod` 子类从**必须**降级为**可选**（模块顶层 `lp.on_hit(...)` 即注册），`ctx` 不再出现在作者视野里。

工作量：**P0 5–6 人日 / P1 7–8 人日 / P2 5 人日**，全程双轨、现有测试不需要改（新增 op 只增不改）。

---

## 2. 参照对象：FaustLauncher 的风格规则（8 条，各 2 个证据片段）

以下片段均取自抓到的公开文档页（原文为中文，代码为原文摘录，仅做缩进整理）。

### 规则 1｜命名空间即模块：作者只 import 模块，不 import 类

技术文档 `01-namespaces` 的机制：

```python
_NS = ADDON_ARG.get('Namespaces')

warp   = _NS.space('warp')          # 顶级：拿到整个 WarpHook
game   = _NS.space('warp.game')     # 扁平取：直接拿子分区
game2  = warp.game                  # 嵌套取：等价写法
```

`technical/02-game` 里的日常写法（一整页没有一个类名）：

```python
game = _NS.space('warp').game
game.popup('来自插件的问候')
game.on_round_start(_on_round)
```

**要点**：`warp.game` / `warp.object` / `warp.field` / `warp.plugin` / `warp.audio` 是**模块级名字**
（点下去就是函数和常量），`Unit`/`Buff` 这类类型只出现在 `warp.object` 的**返回值**里，作者不需要
`from warp.object import Unit`。注册能力用 `provide(...)` 而不是导出符号。

### 规则 2｜值对象门面：句柄只存定位信息，属性是懒加载 attribute，读不到返回 `None`

`technical/03-object`：

> `Unit` 只存了 `(host, slot, index)` 三样东西 —— 每次读属性都是「现拼读链 + 让 DLL 代读」。
> 所以 `Unit` 对象很便宜，但**属性很贵**。

```python
u = obj.unit(host, 0)          # 底层：自己要给 host
u = game.units()[0]            # 门面：同一批对象，host 帮你管好了
```

属性表（节选）与 `None` 语义（`tutorial/04-unit`）：

```python
u.id, u.identity, u.hp, u.max_hp, u.sp, u.speed, u.hp_ratio, u.alive, u.is_dead,
u.is_break, u.is_panic, u.is_low_morale, u.index      # 每一个都是一次真实的内存读取

# 读不到时是 None，不是 False —— 这个区别很重要
if u.hp_ratio is not None and u.hp_ratio < 0.3:
    ...
```

**要点**：值对象是**便宜的门面 + 贵的属性**；类型注解写成 `bool|None`，强迫调用方处理"读不到"。

### 规则 3｜批量取值：一次往返换一组字段，且文档明说「属性很贵」

`tutorial/05-props` 的三种读法与成本表：

| 想要 | 写法 | 成本 |
|---|---|---|
| 一两个字段 | `u.hp` / `u.get_pro("_totalTakeDamageInThisBattle")` | 1 次往返 |
| 一批字段 | `u.props()` | 60 多次往返，约 2 秒 |
| 整场简表 | `game.snapshot()` | 单位数 × 11 次往返 |

```python
props = u.props()                       # 一次读 60 多个字段，会慢（约 2 秒）
props['_maxHp'], props['_mp'], props['_isBreak']

u.scan(classes=("CharacterState",))     # 可以只挑一段
```

`tutorial/04-unit` 直接把反例写进去：

```python
for u in game.units():        # 11 个单位
    print(u.hp, u.sp, u.speed)  # ×3 个属性 ×11 = 33 次往返 ≈ 1 秒
```

**要点**：批读不是"优化技巧"，是**文档主推的第二条路**；而且它给出**具体数字**，让作者自己权衡。

### 规则 4｜路径链 DSL + 落点 `kind`：底层可控，但被包装得能读

`technical/03-object` 的读链写法与落点表：

```python
u.read({'off': 0x10, 'deref': True}, {'off': 0x60, 'deref': False}, kind='int')
```

| `kind` | 得到 | 用途 |
|---|---|---|
| `int` / `float` / `bool` | 原生标量 | 默认 |
| `obscured` | 解混淆后的 int | 血量、理智（游戏里是 `ObscuredInt`） |
| `ptr` | 64 位指针 | 要用低 32 位时 |
| `count` / `dict_count` | 元素数 / 活跃项数 | 容器长度 |
| `text` / `buff_mask` | 字符串指纹 / bitmask | 判「有没有某几个 buff」 |

链步的词汇表（`off` / `list` / `array` / `dict` / `list_val` / `dict_val` / `'iter'`）也在同一页，
`warp.field.read_chain('BattleUnit','_model','_state','_hp')` 则把**语义路径**编译成链：

```python
fields.read_chain('BattleUnit', '_model', '_state', '_hp')
# {'off': [{'off': 16, 'deref': True}, {'off': 328, 'deref': True}, {'off': 328, 'deref': False}],
#  'kind': 'obscured'}
```

**要点**：底层能力**存在**且**可读**（有词汇表、有 `kind` 枚举表、有语义路径编译），
但门面属性把 90% 的场景提前解好（`u.speed` 已从 ×1000 定点数除回，`u.alive` 已解混淆）。

### 规则 5｜模块级函数（自己传 host）与门面（host 已管好）是同一批对象，差别只在谁创建 `Host`

`technical/03-object` 明说：

> 两种拿到的都是同一个 `Unit` 类，差别只在谁负责创建 `Host`。

```python
# 模块级：host 是第一个参数
obj.bind_manager(host)
for u in obj.units(host, limit=3):
    print(u.id, u.hp)

# 门面：host 内部管好
for u in game.units():
    print(u.id, u.hp)
```

`technical/02-game` 给出门面背后的单例与它的两套入口：

```python
live = game.Live(owner='我的插件')
live.on_round_start(handler)
live.start(menu=(('看状态', lambda: print(live.describe())),))
```

**要点**：**同构的两层，不是两套 API**。门面 = 模块级函数 + 一个按 owner 记账的单例 `Live`。
所以「一个插件一个通道」是默认值，需要多通道时才自己 `Live(...)`。

### 规则 6｜事件用「注册函数 + 命名约定」，不用类装饰器

`tutorial/03-events` 的全部事件面就是 4 个同构函数：

```python
game.on_round_start(_on_round)                  # 回调收到 Battle 快照
game.on_scene_changed(lambda scene: ...)        # scene 是 dict
game.on_battle_start(lambda battle: ...)
game.on_battle_end(lambda scene: ...)
```

命名约定：**`on_<事件名>(handler)`**，handler 一个参数（快照/字典）。
它甚至解释了为什么**故意不做**某类事件：

```python
# 为什么没有 on_hp_changed：血量变化每回合几十次，每次通知都要跨进程往返
_last = {}
def _on_round(battle):
    for u in battle.units:
        old = _last.get(u.id)
        if old is not None and u.hp < old:
            print(f'#{u.id} 掉了 {old - u.hp} 点血')
        _last[u.id] = u.hp
```

底层（`tutorial/09-inside`）的钩子则退化成一条 dict，用 `service.on_hit(handler, method=...)` 接：

```python
service.on_hit(_on_create_unit, method='OnCreateUnitModel')
service.start()
```

**要点**：**注册是"给函数"，不是"写类成员"**；门面层按事件命名逐个出函数（可 tab 补全），
底层层用一条 dict 描述（`klass/method/argc/cache/guard/flags`）。

### 规则 7｜运维约定：单一日志文件、失败降级（读不到 = None）、线程与生命周期禁忌

`tutorial/01-hello` 把日志收敛成一个文件，并规定标签：

```
2026-11-01 22:24:33.640 pid=9320 thread=pygame [host] [WarpHook] 钩子安装 32/32 err=0
2026-11-01 22:24:34.110 pid=9320 thread=pygame [event] EVT ev=battle.StartBattle slot=12 rv=0
```

> 以前 WarpHook 会摊出三个日志……现在整个 WarpHook 只写 `addons/WarpHook/warp.log`。

失败降级写死在属性语义里（`tutorial/04-unit`）：

```python
| 全部是 None | 不在战斗里，或者游戏没开 / WarpHook 没注入 |
| 只有某几个是 None | 那个字段的偏移对这个角色不适用 |
| game.units() 是空的 | 还没打第一个回合（管理器是在回合开始时才被抓住的） |
```

线程禁忌（`tutorial/08-lifecycle`）：

| 你的代码在哪 | 跑在哪个线程 | 能做什么 |
|---|---|---|
| `scr.py` 顶层（载入时） | 启动器主线程 | 快，别阻塞 |
| `on_round_start` 回调 | 心跳线程 | 可以慢，可以 `sleep` |
| `menu` 菜单项 | UI 线程 | 别阻塞太久 |

**要点**：**日志只有一个文件 + 标签区分来源**；降级不是 try/except 散落各处，而是**属性语义**；
线程模型用一张表说清「你永远不在游戏线程上」。

### 规则 8｜文档三层对 API 形状的反向要求

站点结构：**入门教程（01–09 按"能做什么"排）/ 技术文档（按命名空间排）/ 参考手册（偏移、笔记、验收台账）**。

反向要求（这是本文最重要的方法论结论）：

| 层 | 回答的问题 | 对 API 形状的要求 |
|---|---|---|
| 入门教程 | "我要做 X，怎么写" | 每一篇只引入**一个新概念**；同一篇里不出现两套写法；示例必须**能整段抄** |
| 技术文档 | "这个命名空间里有什么" | 每个命名空间必须是**封闭可枚举**的（`props` 表 / `read` 链步表 / `kind` 表都能一页列完） |
| 参考手册 | "偏移/字段/踩坑" | 数据与 API 分离：字段表、偏移、`verified` 标记是**数据**，不混进 API 文档 |

`tutorial/05-props` 的例子最能说明「教程篇逼出 API 形状」：因为教程要教"三种读法按需要选"，
API 就必须真的提供**三个粒度**（单属性 / `props()` / `snapshot()`），否则这一篇没法写。

**对我们的直接推论**：如果 LimbusPy 的入门教程必须写「先判断你在 hook 里还是 `on_load` 里，
然后选 `ctx.ops` / `self.ops` / `ctx.session` / `self.session`」——那说明 **API 形状错了**，
不是文档没写好。这正是第 3 节要逐条钉死的诊断。

---

## 3. 现状诊断：模组作者今天**必须**知道、而 FaustLauncher 作者不必知道的东西

每条给 `文件:行` + 片段 + 一句话说明为什么是负担。序号 `D1…D9` 在后面的设计里会被逐条对应解决。

### D1｜`Mod` 子类是硬约束：没有子类直接抛错

`python/limbuspy/mod/loader.py:252-253`

```python
if not candidates:
    raise ManifestError("%s: no Mod subclass defined in %s" % (manifest.name, module.__name__))
```

同文件 `:244-250` 还规定「必须是在本模块里定义的类」「多个候选取最派生的那个」：

```python
for attribute in dir(module):
    value = getattr(module, attribute)
    if not isinstance(value, type):
        continue
    if not issubclass(value, Mod) or value is Mod:
        continue
    if value.__module__ != module.__name__:      # 从别的模块 import 来的基类不算
        continue
```

**为什么是负担**：作者想写 5 行"受击时加伤害"，得先知道"必须有一个 `Mod` 子类、必须是本模块定义的、
挑选规则是最派生"。FaustLauncher 没有类：`scr.py` 顶层执行即启动（`tutorial/01-hello`：
「没有 `main()`，也没有注册函数」）。**类不是逻辑需要，是加载器实现细节泄漏。**

### D2｜4 个入口 + 3 种缓存语义：`ctx.session` / `ctx.ops` / `self.session` / `self.ops`

`python/limbuspy/mod/context.py:37-39`（每次回调新建、**带缓存**）

```python
#: Game façade bound to this callback. A fresh session per callback means the value cache
#: never leaks stale numbers into the next event.
self.session = _make_session(bridge)
```

`python/limbuspy/mod/base.py:64-73`（长生命周期、**无缓存**）

```python
def _new_session(self):
    """hook 之外的会话：**关掉缓存**（读到的是当次真值）。"""
    ...
    return Session(self.bridge, use_cache=False)
```

`python/limbuspy/mod/base.py:86-102`（`self.ops` 又是第三个会话）

```python
@property
def ops(self):
    """A 层（封装 op）入口。

    hook 里请用 ``ctx.ops``（绑在本次回调的 fresh session 上）；``self.ops`` 用于
    ``on_load`` / 后台线程等**没有 ctx** 的场景。
    """
```

`python/limbuspy/game/session.py:47-54`（缓存开关本身就是文档主题）

```python
def __init__(self, bridge: Bridge, index=None, strict: Optional[bool] = None,
             use_cache: bool = True) -> None:
    """``use_cache=False`` 用于**长生命周期**的会话（``Mod.session`` / ``Mod.ops``）。
    缓存只能活在"一次回调"里……
    """
```

`docs/02-mod-authoring.md:108-117` 甚至专门做了一张表：

| 场景 | 用什么 | 缓存 |
|---|---|---|
| hook / `@expose` 内读写 | `ctx.ops` / `ctx.session` | 缓存只活一次回调 |
| `on_load`、后台线程 | `self.ops` / `self.session` | **无缓存** |
| 只读事件里已有信息 | `ctx.payload` / `ev.*` | 不需要桥 |

**为什么是负担**：这是**框架内部实现（谁 new 了 Session、有没有开缓存）泄漏成用户级决策**。
作者读第二回合的 HP 得到第一回合的值时，代码不会报错——这是最贵的一类 bug。
`docs/05-typed-api.md:77` 的官方示例本身就在同一个回调里混用两个入口：

```python
stacks = ctx.ops.unit.buff_stacks(ev.target, "SINKING")     # 1 crossing
if stacks >= 6:
    self.ops.unit.burst_by_buff(ev.target, "SINKING", ...)  # ← 换成了无缓存会话
```

### D3｜会话/引用/句柄这五个词，出现在作者的每一处

| 概念 | 出现在作者视野的位置 | 为什么是负担 |
|---|---|---|
| `Session` | `docs/02:216` `gb.current(ctx.session)`；`battle.py:106` `def current(session)` | 作者必须传一个"会话"才能读战场 |
| `Bridge` | `mod/base.py:113-118` `if self.bridge is None: raise`；`context.py:23` `bridge: Bridge` | 作者要理解"桥在不在" |
| `BoundObject` | `api/base.py:57-107`：`.session` / `.wire` / `.target` / `.handle` / `.data` / `.raw` | 5 个访问器，全都不是模组逻辑 |
| `RawView` | `api/base.py:233-259`；`docs/02:228` `unit.get("某成员")` | 逃生口长得和正常 API 一样，作者分不清该用哪个 |
| `select()` | `api/base.py:199-230`（批量读，返回 `List[Dict[str, Any]]`） | 批量读返回**没类型**的 dict 列表 |
| `from_wire` | `api/types.py:48-60`、`api/hooks.py:29-31`、`ops.py:35` 等 11 处 | 序列化细节公开在签名里 |
| `ref` / `handle` / `instance_id` | `api/types.py:34-72`；`sinking_tide/__init__.py:409` 手写 `int(target.get("h"))` | 作者要自己从 dict 里抠句柄 |

其中 `sinking_tide/__init__.py:407-415` 是最刺眼的证据 —— 一个**官方示例**在手工解 wire 结构：

```python
@staticmethod
def _find_unit(book, target) -> Any:
    """把事件里的主对象换成快照里的 Unit（同一句柄即同一单位）。"""
    handle = int(target.get("h") or 0) if isinstance(target, dict) else int(getattr(target, "handle", 0) or 0)
    if handle:
        for unit in book.units:
            if unit.handle == handle:
                return unit
    return Unit(book.session, target) if isinstance(target, dict) else None
```

**为什么是负担**：`{"t":"unit","h":123}` 是线协议（`docs/01-protocol.md` 冻结的内容），
**协议是内核与框架之间的合同，不该是作者的 API**。

### D4｜`ctx` 的生命周期规则：能用但不能存

`python/limbuspy/mod/context.py:20-41` 用 `__slots__` 建一个每回调对象；`docs/02:206` 明写
「**引用（句柄）不跨重载**，重载后要重新获取」；`docs/02:151-152`「Python 侧永远只持有 `{"t","h"}` 引用；
**不要自己构造引用**」。

**为什么是负担**：作者脑子里要维护一张"什么东西能活多久"的表（`ctx` 一次回调 / `ctx.state` 跨热重载 /
句柄不跨重载 / `Mod.session` 跨生命周期但无缓存）。FaustLauncher 里对应只有一条：
`game.close()` 在 `on_unload`（`tutorial/02-popup`）。

### D5｜`@on` 的两种签名 `(self, ev)` 与 `(self, ev, ctx)`

`python/limbuspy/api/hooks.py:139-150`

```python
def wants_context(fn: Any) -> bool:
    """回调是否接受第三个参数（``ctx``）。约定：``def f(self, ev, ctx)``。"""
    ...
    return has_varargs or len(positional) >= 3
```

`hooks.py:154-161` 把两种签名并列写进 docstring：

```python
@on("DamageDealt")
def on_damage(self, ev: DamageDealtPayload) -> None: ...        # 只要 payload

@on("DamageDealt")
def on_damage(self, ev: DamageDealtPayload, ctx) -> None: ...   # 还要 ctx（ops/session）
```

**为什么是负担**：靠**参数个数**判断行为，是最脆的一种约定：`*args` 会被当成"要 ctx"；
mypy 无法对"你多加了一个参数"报警；两个分支的可用能力不同（有 `ctx` 才能 `ctx.ops`），
于是**签名决定能力**，而作者看不出这件事。

### D6｜返回值形态不一致：dataclass / dict / 裸值 / bool

| op | 返回 | 位置 |
|---|---|---|
| `unit.info` | `UnitInfo`（frozen dataclass） | `ops.py:159-170` |
| `unit.buffs` | `List[BuffInfo]` | `ops.py:95-102` |
| `unit.burst_by_buff` | `BurstInfo` | `ops.py:104-120` |
| `battle.round` | `Optional[int]` | `ops.py:20-25` |
| `unit.add_passive` / `unit.add_buff` | `bool` | `ops.py:63-71` / `46-61` |
| `bindings.*.select()` | `List[Dict[str, Any]]` | `api/base.py:199-230` |
| `ctx.get` / `ctx.call` | `Any` | `context.py:61-71` |
| Harmony 补丁 prefix | `None` / `{"skip": True}` / `{"args": [...]}` | `docs/02:282` |

再加一处**真正的类型断点**（本次实测）：

```python
>>> from limbuspy.api.types import UnitInfo, to_wire_unit
>>> to_wire_unit(UnitInfo(instance_id=201, hp=300))
TypeError: 无法解析为单位引用: UnitInfo(instance_id=201, origin_id=0, hp=300, ...)
```

即 **`UnitInfo` 不能回传给任何 op**，因为 `api/types.py:75-92` 的 `to_wire_unit` 只认
`UnitRef` / `dict` / `int` / `str(:)` / 有 `.wire` 的对象。而 `docs/05-typed-api.md:211-212` 的
真机验证清单正好教人这么写：

```python
u = session.ops.battle.units()[0]  →  session.ops.unit.damage(u, 5)  → HP 下降
```

**这条 REPL 配方按现在的代码是抛 `TypeError` 的**（`battle.units()` 返回 `UnitInfo`）。

**为什么是负担**："读到的单位"和"能操作的单位"是两种东西，作者必须记住哪个能传回 op。
FaustLauncher 里 `game.units()` 返回的就是 `Unit`，直接能传（`u.has_buff('Breath')`、`u.props()` 都在同一对象上）。

### D7｜2 MB 的 `api/bindings.py` + `_gen/` 是否会被作者看见：**会，而且是硬失败**

`python/limbuspy/api/__init__.py:34-41`

```python
try:  # 生成物：跑 `python -m limbuspy api gen` 就有了
    from . import bindings
except ImportError as exc:  # pragma: no cover - 只在未生成时命中
    raise ImportError(
        "limbuspy.api.bindings 尚未生成。请运行：\n"
        "  python -m limbuspy stubs --dump <dump.cs> --bindings\n"
    ) from exc
```

而**每一个 A 层 mod 都会踩到这条路径**：作者的写法是 `from limbuspy.api.hooks import on`
（`burst_on_hit/__init__.py:21`、`field_medic/__init__.py:22`、`damage_ledger/__init__.py:19`），
这会先执行 `limbuspy/api/__init__.py`，于是：

1. 无条件 import 2.0 MiB 的 `bindings.py`（另有 365 KB `bindings_names.py`、158 KB `_bindings_enums.py`）；
2. 没生成过就直接 `ImportError`，**连 `@on` 都用不了**；
3. 实测 import 成本：`limbuspy.api.bindings` 单独 0.189 s / 60 模块；作者实际要付的是
   `limbuspy.mod`(0.430 s / 137 模块) + `limbuspy.api.hooks` ≈ **0.6 s**。

`python/limbuspy/_gen/`（`methods.json` **36 MB**、`types.pyi` 627 KB、`enums.py` 204 KB）不在包内但
`docs/05:230` 明确把它当"可选本地生成物"。`tools/requirements/limbuspy.json`（10 类型 / ~95 成员）
是它的一致性闸门。

**为什么是负担**：作者为了写一个 3 行的钩子，**间接依赖 2 MB 生成物能否 import 成功**。
分层应该是反过来的：A 层零依赖生成物，B 层（绑定的使用者）才付这个成本。

### D8｜同一个"单位"有两套门面，且属性成本看起来都是 0

| | `api/types.py::UnitInfo` | `game/objects.py::Unit` |
|---|---|---|
| 类型 | `@dataclass(frozen=True)` 扁平值（`types.py:100-141`） | 活句柄（`objects.py:186-233`） |
| 从哪来 | `ops.unit.info()` / `ops.battle.units()` | `game.battle.current(session).units` |
| `hp` | 字段，0 往返 | `property` → `_prop("hp")`（`objects.py:190`），**1 往返** |
| 能否回传 op | ❌（D6 实测 `TypeError`） | ✅（有 `.wire`） |
| `buff(k)` | 无（只有 `buff_stacks`） | ✅ 但有 N+1（见下） |

`python/limbuspy/game/objects.py:217-230` 的 `buff()` 是 N+1 的实证：

```python
def buff(self, keyword: str) -> Optional[Buff]:
    """First buff whose main keyword matches (case-insensitive, accepts the enum name)."""
    wanted = str(keyword).upper()
    for buff in self.buffs:                      # ← 1 次往返（GetActivatedBuffModels）
        try:
            if str(buff.keyword).upper() == wanted or buff.is_keyword(wanted):
                #                                    ↑ 每个 buff 再来 1 次往返（IsKeyword）
                return buff
        except Exception:  # noqa: BLE001 - a stale buff must not break the search
            continue
    return None
```

而 A 层有等价且一次到位的 `unit.buff_stacks`（`ops/UnitOps.cs:19-24` → `GetActivatedBuffStack(keyword, false)`）。

**为什么是负担**：同一个概念（"单位身上某 buff 有几层"）有两个写法、成本一个 1 一个 1+N，
**没有任何地方告诉作者该选哪个**；而两者都长得像免费属性（`unit.buff_stack("SINKING")` vs
`unit.buff_stack("SINKING")` —— 名字几乎一样）。这是"属性看起来免费"的心理错觉最严重的一处。

### D9｜具体任务实测：「当受击者身上有 SINKING buff 时，把这次伤害翻倍」

这是最典型的"信息类 + 改写类"混合需求，FaustLauncher 的作者会怎么写？
`tutorial/06-buffs` 的 `u.has_buff('Breath')` + `tutorial/09-inside` 的拦截能力，**两篇文档、一个概念**
（`Unit` 有 `has_buff`），同步改写则用 `HF_BLOCK`/钩子。LimbusPy 现在的情况：

**结论：今天这个需求"无法正确实现"，只能投机。**

原因链（全部有据）：

1. 要改伤害必须在**同步泳道**里返回倍率：`docs/02:184` `@hook("ChangeTakeDamage", sync=True, fallback=100)`；
2. 同步泳道有 **20 ms 硬预算**（`docs/02:189`），且 `docs/02:194`「同步钩子里**不要**再触发需要等待的深层调用」；
3. 读 buff 层数**必须**一次往返（`unit.buff_stacks` / `game.Unit.buff_stack`），而一次往返 ≈ 20–40 ms 量级
   （FaustLauncher 实测数；LimbusPy 本机 A 层 op 更快，但仍是一个时间片级别的等待）；
4. 于是**官方示例自己绕开了这个需求** —— `sinking_tide/__init__.py:246-256`：

```python
@hook("ChangeTakeDamage", sync=True, fallback=100)
def take_damage_multiplier(self, ctx) -> int:
    """返回 100 = 不变，115 = +15%。

    同步钩子会**阻塞游戏主线程**等 Python 回包（默认 20 ms）。所以这里只读内存里的两个
    字段，绝不 ctx.call/get —— 任何往返都可能撞上预算而落到 fallback。
    """
    if not self.cfg.amplify_take_damage or not self.tide_armed:
        return 100
    return 100 + max(0, int(self.cfg.damage_per_stack) * 5)
```

注意它读的是 **mod 自己的开关**（`self.tide_armed`），**不是受击者的 buff** —— 因为后者读不到。
要真的按 buff 判定，作者只剩两条脏路：
(a) 用 `Mod.patch("BattleUnitModel", "OnChangeHp", ..., kind="prefix")` 往 C# 侧塞逻辑（`docs/02:258-280`）；
(b) 在别的地方（非同步时机）预先把 buff 状态缓存下来，再在同步钩子里查自己那份可能过期的副本。

**现在的成本**：约 **40–60 行**、需要 **9 个概念**
（`Mod` 子类、`@hook` + `sync=True` + `fallback`、`ctx`、`ctx.event["args"]` 索引、
`Mod.patch` + `sig` 签名列表、Harmony prefix/postfix、`BUFF_UNIQUE_KEYWORD` 哨兵 2123、
`frame budget`、以及"为什么不能在里面 read"的规则），需要读 **3 份文档**
（`docs/02` §5/§6.5、`docs/05` §1.3、`docs/01` 的错误模型）。

**这就是本文要消灭的那个数字。** 目标（见 §4.7）：**8 行、2 个概念、1 份文档、0 次往返**。

---

## 4. 目标设计

### 4.1 五条设计公理

| # | 公理 | 直接解决的问题 |
|---|---|---|
| A1 | **往返发生在「获取」的边界，不发生在「读属性」的时候。** 拿到手的对象是**完整快照**，属性是纯内存 | D8 的"属性看起来免费"（这里它真的免费）、N+1、静态检查器友好 |
| A2 | **作者手上只有一个名字：`lp`。** 没有会话、没有桥、没有句柄。需要"通道"时 `lp.game()` 返回一个显式对象 | D2（4 个入口）、D3（5 个概念） |
| A3 | **失败不抛错，返回 `None` / `ok=False` + 理由；只有作者的编程错误才抛** | D6（返回形态不一致）、D9 的不可用场景 |
| A4 | **模块顶层即注册。** `Mod` 子类变成"需要状态时的可选道具"，不是入场券 | D1 |
| A5 | **每一个往返都在签名/docstring 里写明次数（`Cost:`）。** 形状 + 文档双保险，不靠作者记性 | D8、§6.3 的"错觉"风险 |

再加一条**元公理**（决定一切取舍）：

> **M1：如果一个需求必须让作者知道"框架内部发生了什么"才能写对，那就是 API 的 bug，不是文档的 bug。**
> 这条同时对 D1（加载器实现）、D2（Session 缓存）、D5（参数个数探测）成立 —— 三者都是"内部实现泄漏成用户决策"。

### 4.2 顶层形状：`import limbuspy as lp`

#### 4.2.1 结论

```python
import limbuspy as lp

# —— 批量取（每次 1 crossing，拿回完整快照）
lp.battle()                      -> Battle            # 全场 + 回合 + 波次
lp.units()                       -> list[Unit]         # = lp.battle().units
lp.unit(201)                     -> Unit | None        # 按 instance_id
lp.unit(identity=10115)          -> Unit | None        # 按人格 ID
lp.unit(index=0)                 -> Unit | None        # 按列表下标
lp.player()                      -> Unit | None        # 我方第一个
lp.round()                       -> int | None
lp.buff_stacks("SINKING")        -> dict[int, int]     # 全场某 buff 层数，1 crossing
lp.status()                      -> Status            # 内核/A 层可用性（不抛错）

# —— 动作（每次 1 crossing，返回 ActionResult）
lp.damage(u, 18) / lp.heal(u, 20) / lp.set_hp(u, 100)
lp.add_buff(u, "SINKING", 3, turns=2) / lp.add_passive(u, 601101)
lp.burst(u, "SINKING", per_stack=3, max_stacks=8)

# —— 事件（模块顶层注册，立即生效）
lp.on_hit(fn) / lp.on_damage(fn) / lp.on_death(fn) / lp.on_break(fn)
lp.on_turn_start(fn) / lp.on_damage_taken(fn)

# —— 类型（只为类型注解而 import；拿到对象不需要它们）
lp.Unit / lp.Buff / lp.Battle / lp.UnitState / lp.ActionResult

# —— 逃生口（文档里只有一段，且要求写注释）
lp.reflect                        -> 反射命名空间
u.raw                             -> RawHandle

# —— 静态表（纯本地，不过桥）
lp.static                         -> limbuspy.game.statics 的薄封装
```

#### 4.2.2 `lp.units()` / `lp.unit(1)` / `lp.round()` 还是 `lp.round`？

题面提到 `lp.round` 这样的属性形状。**结论：模块级只放函数和类，值一律由对象属性提供。**

理由（这是唯一一处必须和 FaustLauncher 分道的地方，要写清）：

1. 模块级"属性"只能用 PEP 562 的 `__getattr__` 实现，而 **pyright/mypy 不追踪 `__getattr__` 的返回类型**
   （除非额外发 `.pyi`）。这与"静态检查器友好"的硬要求直接冲突。
2. 备选方案 `lp.round = property(...)` 在模块上不成立（模块不是类）。
3. 备选方案"发 `.pyi`"能解决，但给了两条真相来源，`gen_contract.py` 的"单一契约源"原则就破了。

于是：**需要"属性"语义的四个值（round / wave / 单位列表 / 场景）都挂在 `Battle` 上**：

```python
b = lp.battle()       # 1 crossing
b.round               # -> int | None      （真属性，静态可查，0 往返）
b.wave                # -> int | None
b.units / b.players / b.enemies / b.alive
```

`lp.round()` 保留为**便利函数**（= `lp.battle().round`，同样 1 crossing，不多不少）。
代价是 4 个调用点多一对括号；收益是**整份 API 面没有一处动态属性**，`pyright --strict` 可全绿。
这条取舍写进 `docs/05-typed-api.md §1.1` 的"取舍"表。

#### 4.2.3 要不要 `lp.game` / `lp.unit` / `lp.battle` / `lp.effect` 子命名空间？

| 方案 | 优点 | 缺点 | 裁决 |
|---|---|---|---|
| A. 全平铺（14 个名字在 `lp` 上） | 最少击键，tab 补全一次看全 | 将来长到 40 个名字就失控 | ✅ **主面** |
| B. 按概念分子命名空间（`lp.unit.damage(u)`） | 分组清晰 | **又多一个"该用哪个"的决策点**；`lp.unit` 既是函数又要当命名空间，命名冲突 | ❌ 不采用 |
| C. 全子命名空间（`lp.game.units()`） | 严格 | 每行多一层，且 `lp.game.round` 又回到 §4.2.2 的模块属性问题 | ❌ 不作为主面 |

**裁决：平铺 14 个「教程级」名字 + 3 个「参考级」子命名空间。** 判据是文档分层（规则 8）：

- **教程级（平铺）**：入门文档 §0–§9 里出现的所有名字，**不许超过 15 个**。多了就是 API 设计失败。
- **参考级（子命名空间）**：`lp.reflect`（B 层）、`lp.static`（静态表）、`lp.dev`（成本计数器/诊断）。
  这三个的共同点是：**教程里一个字都不提**，只在参考手册里出现。

FaustLauncher 也是这个形状：`game.*` 平铺（`technical/02-game` 28 个函数），
`object`/`field`/`plugin`/`audio` 分命名空间且只在技术文档出现。

#### 4.2.4 `Mod` 变成可选：模块顶层即注册

```python
# mods/sinking_double/__init__.py   —— 全部内容，没有 import Mod、没有类
"""受击者身上有 SINKING 时，这次伤害翻倍。"""

import limbuspy as lp

SINKING = "SINKING"


def double_if_sinking(ev: lp.DamageTaken) -> int:
    """返回伤害百分比：100 = 原样，200 = 翻倍。"""
    if ev.on is not None and ev.on.buff_stack(SINKING) >= 1:
        return 200
    return 100


lp.on_damage_taken(double_if_sinking, watch=[SINKING])
```

需要状态 / 配置 / 持久化时再上 `Mod`（**同一个注册函数在类里也照常可用**）：

```python
import limbuspy as lp

class SinkingDouble(lp.Mod):
    cfg = lp.config(min_stacks=1, multiplier=200)

    def on_load(self) -> None:
        lp.on_damage_taken(self.on_taken, watch=["SINKING"])

    def on_taken(self, ev: lp.DamageTaken) -> int:
        if ev.on is None or ev.on.buff_stack("SINKING") < self.cfg.min_stacks:
            return 100
        return int(self.cfg.multiplier)
```

实现要点（`mod/loader.py` 与 `mod/runtime.py`）：
`import_mod` 之前由 loader 设置 `limbuspy._registry.CURRENT_MOD`（`contextvars`），
`lp.on_*` 把 `(timing, fn, filters)` 追加进当前 mod 的**待注册表**；`register_instance`
先把这张表 flush 进 `Registry`，再走原来的 MRO 扫描。类里的 `@on`/`@hook` 装饰器路径保持不变
（`limbuspy.api.hooks.on` 的 wrapper 仍然带 `__limbuspy_hook__`）。热重载时注册表按代际整体重建，
待注册表随模块重建而清空 —— 不需要额外生命周期。

`Mod` 从"入场券"变"可选道具"，同时消灭 D1。

### 4.3 值对象：`Unit` / `Buff` / `Battle` / `UnitState`

#### 4.3.1 `Unit`：一个**已填好的快照**，属性 0 往返

```python
class Unit:
    """一个战斗单位。

    这个对象是**某一刻的快照**：构造时（`lp.unit()` / `lp.battle()` / 事件 payload）已经一次
    拿全了全部基础字段，所以**所有属性都是 0 次往返的内存读**。要新值请重新获取，或调
    `refresh()`（1 次往返）。

    读不到的字段返回 `None`（不是 0、不是 False）。三种原因：不在战斗里 / 该字段对这个
    单位不适用 / 内核版本过旧。想看具体原因用 `lp.status().problems`。
    """

    # —— 身份（0 往返，来自快照）
    instance_id: int                 # 战斗内编号（跨回合稳定，记账用它）
    origin_id: int                   # 人格 ID（10115 永远是同一个人格）
    key: str                         # 记账键：str(instance_id)
    index: int                       # 在列表里的下标（0..）
    display_name: str | None         # 本地静态表查出来的名字（0 往返，可能 None）

    # —— 数值（0 往返）
    hp: int | None
    max_hp: int | None
    hp_ratio: float | None           # 已处理 max_hp == 0
    missing_hp: int | None
    mp: int | None
    speed: float | None              # 已从 ×1000 定点数除回
    level: int | None

    # —— 状态（0 往返）
    alive: bool | None
    is_dead: bool | None
    is_break: bool | None
    is_panic: bool | None

    # —— 阵营（0 往返）
    is_player: bool | None
    faction: str | None
    position: str | None

    # —— buff（默认 1 往返；若来自 `battle(buffs=True)` 或事件快照则为 0）
    @property
    def buffs(self) -> list[Buff]: ...
    def buff(self, key: str) -> Buff | None: ...      # 1 往返
    def buff_stack(self, key: str) -> int: ...        # 1 往返；没有该 buff → 0
    def buff_turns(self, key: str) -> int | None: ... # 1 往返
    def has_buff(self, key: str) -> bool: ...         # 1 往返

    # —— 批读 / 刷新
    def state(self) -> UnitState: ...                 # 1 往返，一次拿 16 个字段（含 buff 摘要）
    def refresh(self) -> Unit: ...                    # 1 往返，原地更新，返回 self

    # —— 逃生口
    @property
    def raw(self) -> RawHandle: ...                   # 唯一出现 IL2CPP 成员名的地方

    # —— 协议
    def __repr__(self) -> str:
        """<Unit #201 エンリコ 245/300 hp  SINKING×8>"""
    def __eq__(self, other: object) -> bool:          # 按 (instance_id or handle)
    def __hash__(self) -> int
```

**与 FaustLauncher 的对齐 / 有意偏离**（必须写进文档，否则读者会问"为什么你不一样"）：

| FaustLauncher | LimbusPy 目标 | 理由 |
|---|---|---|
| `u.hp` 1 次 20–40 ms 往返 | `u.hp` **0 往返** | 我们有 A 层（C# 原生批量），可以在边界一次拿全；FaustLauncher 只能靠共享内存单值读 |
| 字段 14 个属性 + `props()` 60 字段 | 16 个属性 + `state()` 16 字段 | 只暴露 A 层能**高效**拿到的；`props()` 那种 60 次往返的能力属于 B 层（`u.raw.scan()`） |
| 读不到 `None` | 读不到 `None` | 完全一致 |
| `id`/`identity` 两套编号 | `instance_id`/`origin_id` + 别名 `id`/`identity` | 保留游戏 dump 名（`members.py` 已校验），同时给 FaustLauncher 作者熟悉的短名 |

**`__repr__` 的设计**：一行能定位 + 一眼能看懂状态，这是日志可读性的关键（规则 7：单一日志文件，
看日志的人只有作者）。

```
<Unit #201 エンリコ 245/300 hp  SINKING×8 CHARGE×2>
<Unit #101 ダンテ 40/40 hp>
<Buff SINKING×8/99 3turn 「沈潜」>
```

#### 4.3.2 `Buff`：**全字段 0 往返的纯值对象**

```python
@dataclass(frozen=True, slots=True)
class Buff:
    keyword: str          # 主关键字（BUFF_UNIQUE_KEYWORD 名字，如 "SINKING"）
    name: str             # 显示名（本地化文本，来自 C# 侧 BuffStaticData）
    stacks: int
    turns: int
    max_stacks: int
    max_turns: int

    @property
    def stack_ratio(self) -> float | None: ...   # 0..1，max_stacks == 0 时 None
    def is_(self, key: str) -> bool: ...         # 大小写不敏感的关键字判定
    def raw(self) -> dict[str, Any]: ...         # 6 个字段的字典（给日志/序列化用）
    def __repr__(self) -> str: ...
```

对齐 FaustLauncher 的 9 个 buff 字段（`name/key/stack/turn/max_stack/max_turn/stack_ratio/icon/type_id`）：

- 拿到的 6 个直接映射（`stack`→`stacks`、`turn`→`turns`，**保留游戏 dump 复数名**并给 `stack`/`turn` 别名属性）；
- `stack_ratio` 派生；
- `icon` / `type_id` **只在 `raw()` 里出现**（A 层的 `OpsArgs.BuffInfo` 还没读它们，
  加进去需要 C# 改 `BuffStaticData` 读取路径 → 列入 P2 的 `buff.icon` 扩展，不阻塞 P0）。

`Buff` 之所以能全 0 往返：它**只从 `unit.buffs`（一次 `GetActivatedBuffModels()` 遍历）生成**，
不会单独持有一个要再解引用的句柄 —— 这一点比 FaustLauncher 的 `Buff` 更干净（后者 `b.name` 还要读字符串）。

#### 4.3.3 `Battle`：一次往返的全场视图

```python
class Battle:
    round: int | None
    wave: int | None
    units: list[Unit]
    players: list[Unit]
    enemies: list[Unit]
    alive: list[Unit]

    def by_id(self, instance_id: int) -> Unit | None: ...
    def by_identity(self, origin_id: int) -> Unit | None: ...   # 可能多个 → by_identities
    def lowest_hp(self, *, side: Literal["any","player","enemy"] = "enemy") -> Unit | None: ...
    def buff_stacks(self, key: str) -> dict[int, int]: ...      # 1 往返（全场）
    def __len__(self) -> int
    def __iter__(self) -> Iterator[Unit]
    def __repr__(self) -> str:   # <Battle round 3  11 units (5P/6E)>
```

`b.enemies[0].hp` **0 往返**（`_data` 已填）。这就是 FaustLauncher `game.on_round_start(handler)`
拿到的 `Battle`（`technical/02-game`），只是我们的单位属性更便宜。

#### 4.3.4 `UnitState`：显式批读的返回类型

```python
@dataclass(frozen=True, slots=True)
class UnitState:
    """`u.state()` 的返回值：一次往返的 16 个字段。"""
    instance_id: int; origin_id: int; hp: int; max_hp: int; mp: int; speed: float
    level: int; is_dead: bool; is_break: bool; is_panic: bool
    is_player: bool; faction: str; position: str
    buff_count: int
    buff_stacks: dict[str, int]     # 主关键字 → 层数（全部 buff，不再往返）
    applied: bool = True            # 动作返回时为 True/False

    @property
    def hp_ratio(self) -> float
```

`state()` 的价值不是"省往返"（属性本来就 0 往返），而是**"我要的字段名是动态的/要整体序列化"**
（记账、导出、写 JSON）。这一点和 FaustLauncher 的 `props()` 动机不同，必须写清：
**我们不是用它省往返，是用它拿 dict 形状。**

### 4.4 上下文：一个入口（A2）+ 一个显式通道

#### 4.4.1 `lp.*` 怎么知道"现在在哪"

```python
# limbuspy/_ctx.py（手写，内部）
_CURRENT: contextvars.ContextVar[Game | None] = contextvars.ContextVar("limbuspy.current", default=None)

def bind(game: Game) -> Token:      # runtime 在调 handler 前调用
    return _CURRENT.set(game)

def unbind(token: Token) -> None:   # handler 返回后调用
    _CURRENT.reset(token)

def current() -> Game | None:
    return _CURRENT.get()
```

`mod/runtime.py:390-402` 现在这样建 `Context` 并 `entry.fn(context)`；改成：

```python
game = self._game_for_callback()          # 一个回调一个 Game（缓存只在 Game 内的 _batch 上）
token = _ctx.bind(game)
try:
    entry.fn(context)                     # 作者写的 handler
finally:
    _ctx.unbind(token)
```

于是 `lp.units()` 等模块级函数的实现只有一句：

```python
def units() -> list[Unit]:
    """..."""
    return _require().units()
```

#### 4.4.2 后台线程 / `on_load` / REPL 怎么办

```python
g = lp.game()        # 显式拿一个通道（等价于 FaustLauncher 的 game.Live(owner=...)）
g.units()
g.round
g.damage(u, 5)
```

- `lp.*` 在**没有绑定上下文**时：`lp.game()` 返回"后台通道"（无缓存、读到当次真值）；
  而 `lp.units()` 在后台线程里**也能用**（内部 `current() or background()`），
  但它每次调用都新建一次往返，**不复用缓存**——这就把 D2 的"缓存语义"从"用户要记住的东西"
  变成"性能细节"，而且**语义永远是"读到当次真值"**，不可能读到旧值。
- 因此 `use_cache` 这个参数从作者世界消失：缓存存在，但它是 `Game` 内部对**同一次事件派发**
  的批读优化，**只影响往返次数，不影响读到的值**（同一次派发内所有读都发生在同一个时间片里，
  本来就是同一个值）。
- **`ctx` 不再交给作者。** `Context` 仍然存在（框架内部），但作者签名里不出现它。
  如果将来确实需要（例如 `ctx.state` 的跨热重载字典），用 `lp.state` 这个**专门对象**：
  `lp.state.save/load`（P2），语义单一，不再混在 `ctx` 里。

#### 4.4.3 签名统一：handler 只有一个参数

```python
def handler(ev: lp.Hit) -> None: ...        # 唯一形态
```

`wants_context()`（`api/hooks.py:139-150`）保留但**只用于兼容旧代码**；新 API 的
`lp.on_*` 全部包一层固定签名的内部 wrapper。消灭 D5。

### 4.5 批量与缓存：用**形状**防 N+1（而不是靠提醒）

四个形状层面的机制，每一个都让"错的做法写不出来"或"写出来很难看"：

#### 机制 1｜获取即批量（A1）

```python
# 好：1 次往返，之后 11 个单位 × 任意属性全免费
for u in lp.units():
    print(u.display_name, u.hp, u.hp_ratio, u.is_break)

# 坏：想 N+1 也做不到 —— 没有"逐个获取"之外的写法，
# 而 lp.unit(i) 在循环里会很显眼（而且是唯一会亮红灯的写法）
```

要点：**没有 `u.hp` 这种"会往返"的属性**，所以"循环里刷属性"这个最常见的 N+1 从根上不存在。

#### 机制 2｜全场 buff 一次拿：`battle(buffs=True)` / `battle().buff_stacks(key)`

```python
# 需求：找出所有身上 SINKING ≥ 6 的敌人
b = lp.battle(buffs=True)                       # 1 次往返（含每个单位的 buff 列表）
targets = [u for u in b.enemies if u.buff_stack("SINKING") >= 6]   # 0 往返

# 等价但更省字节（只要层数、不要完整 buff 列表）
stacks = lp.battle().buff_stacks("SINKING")     # 1 次往返 -> {instance_id: stacks}
targets = [u for u in lp.battle().enemies if stacks.get(u.instance_id, 0) >= 6]
```

对照现状：`for u in book.enemy_units: u.buff_stack(SINKING)`（`sinking_tide/__init__.py:325`）
= **1 + N 次往返**，而且作者看代码看不出来。

#### 机制 3｜事件 payload 自带 `battle`（每批 1 次，不是每事件 1 次）

`HookTable.Tick()`（`kernel/LimbusPy.Ops/HookTable.cs:90-105`）本来就在帧末集中推事件。
扩展 `IOpsHost` 增加 `EmitBatch(events, battle)`：**每帧走一次单位字典**（O(11)，主线程微秒级），
批级 payload 里带一份 `battle`；Python 侧 `_on_event_batch` 只建 **一个** `Battle`，
注入到每个事件的 `ev.battle`（`event["_battle"] = battle` 的轻量引用，不是复制）。

```python
def on_hit(ev: lp.Hit) -> None:
    # ev.battle: 0 往返 —— 这一帧的全场快照，和 ev 一起送过来的
    low = ev.battle.lowest_hp()          # 0 往返
    lp.damage(low, 10)                   # 1 往返
```

成本：msgpack 每批多约 1 KB（11 单位 × 16 字段），与现有的每事件 payload 同量级。
若某时机不需要（例如 `UnitDied` 只需要死者），spec 里 `include_battle = false` 关掉。

#### 机制 4｜"读一次就够"的 docstring `Cost:` 行 + 运行时计数器

```python
>>> lp.dev.cost()          # 本回调/本秒的往返计数
Cost(crossings=3, ops={"battle.snapshot": 1, "unit.damage": 1, "battle.buff_stacks": 1})
>>> lp.dev.explain()
获取 lp.units()           1 crossing   (battle.snapshot)
属性 u.hp / u.hp_ratio    0 crossing   (快照字段)
u.buff_stack("SINKING")   1 crossing   (unit.buff_stacks)  ← 循环里用会变 N+1，改用 battle(buffs=True)
```

`lp.dev.cost()` 让"错觉"变成可测量的东西（§6.3）。开发期 `LIMBUSPY_COST_WARN=50`
时，一个回调超过 50 次 crossing 就打一行警告 + 建议（**不改变语义，只提醒**）。

### 4.6 动作函数：签名、返回值、失败语义

```python
def damage(unit: Unit | int, amount: int, *, source: str = "PASSIVE") -> ActionResult: ...
def heal(unit: Unit | int, amount: int, *, timing: str = "ON_SUCCESS_ATTACK") -> ActionResult: ...
def set_hp(unit: Unit | int, hp: int, *, source: str = "PASSIVE") -> ActionResult: ...
def add_buff(unit: Unit | int, key: str, stacks: int = 1, turns: int = 1) -> ActionResult: ...
def add_passive(unit: Unit | int, passive_id: int) -> ActionResult: ...
def burst(unit: Unit | int, key: str, *, per_stack: int = 3, max_stacks: int = 6) -> ActionResult: ...
```

**签名决策**：

- `unit` 接受 `Unit` **或** `instance_id: int`（keyword-only 的 source/timing 全部有默认值）。
  接受 `int` 是为了记账场景（`self.ledger` 只存 id），但**不接受 `UnitInfo`/dict/dict-like**——
  D6 那个 `TypeError` 不再可能，因为只有一种"单位"类型。
- 枚举参数用 `str` 而非 `IntEnum`：A 层的 wire 就是**成员名**（`ops/UnitOps.cs:75-76`
  `OpsArgs.EnumValue(args, "source", DAMAGE_SOURCE_TYPE.PASSIVE)`），传 IntEnum 还要转一次。
  P2 再给 `Literal[...]` 别名（`Source = Literal["PASSIVE","BUFF","SKILL",...]`）让拼写错误静态可见。

**返回值 `ActionResult`**：

```python
@dataclass(frozen=True, slots=True)
class ActionResult:
    ok: bool
    unit: Unit | None                # 动作后的新快照（1 次往返已包含，不用再读）
    hp_before: int | None
    hp_after: int | None
    reason: str = ""                 # ok=False 时的人类可读原因
    op: str = ""                     # 哪个 op（诊断用）

    def __bool__(self) -> bool:      # `if lp.damage(u, 18):` 可直接用
        return self.ok
```

**失败语义（A3）**：

| 情况 | 行为 |
|---|---|
| A 层不可用（`LimbusPy.Ops.dll` 没加载 / 游戏更新导致失效） | `ActionResult(ok=False, reason="ops-unavailable")`，**记一次日志，不抛错** |
| 目标已死 / 无效单位 | `ok=False, reason="target-dead"`（C# 侧 `OpsArgs.Info(unit, false)` 已经是这个语义） |
| 数值被夹（治疗超上限、HP 夹到 0） | `ok=True`，`hp_before/hp_after` 反映实际值 |
| **编程错误**：`amount <= 0`、未知 `source`、`unit` 不是 Unit/int | 抛 `lp.BadArgument`（fail fast，这是作者的 bug） |
| 桥断了 / 超时 | `ok=False, reason="bridge-down"`；`LIMBUSPY_STRICT=1` 时改抛 `LimbusPyUnavailable` |

为什么动作不返回 `bool`：作者永远需要"动作之后 HP 是多少"（`field_medic/__init__.py:71-77`
就为了这个又读了一次）。`ActionResult` 把"是否成功"和"结果状态"合成一次往返。

### 4.7 事件与时点：注册函数 + 声明的过滤条件

#### 4.7.1 六个事件，全部同构

```python
lp.on_hit(fn)            # AttackSucceeded：一次攻击命中结算完成
lp.on_damage(fn)         # DamageDealt：任意来源的 HP 变化
lp.on_death(fn)          # UnitDied
lp.on_break(fn)          # UnitBroken
lp.on_turn_start(fn)     # TurnStarted
lp.on_damage_taken(fn)   # 【新增】同步倍率泳道（ChangeTakeDamage）
```

命名照 FaustLauncher 的 `on_round_start` 约定：**`on_<事件>`，handler 一个参数**。可 tab 补全。

#### 4.7.2 payload：字段 + `battle` 快照

```python
@dataclass(frozen=True, slots=True)
class Hit:
    """一次攻击命中。**全部字段 0 次往返**（C# 侧已拆好参数，`battle` 随批次一起来）。"""
    attacker: Unit | None      # 攻击方（自带快照字段）
    on: Unit | None            # 受击方（别名 target；`on` 更短，且不与 Unit.target 冲突）
    damage: int                # 最终伤害（final_damage 的短名，别名仍在）
    real_damage: int
    hp_damage: int
    critical: bool             # 别名 is_critical
    timing: str
    battle: Battle             # 这一帧的全场快照（0 往返）
```

字段命名统一为**短名 + 保留长名别名**（`on`/`target`、`damage`/`final_damage`），
避免"旧代码抄过来改名"的迁移成本。

#### 4.7.3 过滤条件：两种，成本天差地别，**形状上区分**

```python
# (1) 声明的过滤：在 C# 侧判定，0 次往返（事件根本不会推过来）
lp.on_hit(fn, buff="SINKING")          # 受击方身上有 SINKING
lp.on_hit(fn, keyword="SINKING")       # 单位关键字（UNIT_KEYWORD）
lp.on_hit(fn, side="enemy")            # 受击方是敌人
lp.on_hit(fn, critical=True)           # 只暴击（payload 已有该字段，0 往返）

# (2) 谓词过滤：在 Python 侧对 payload 求值，0 次往返（payload 已经够用）
lp.on_hit(fn, where=lambda ev: ev.damage > 50 and ev.on is not None)

# (3) 需要额外状态才成立的过滤：写成 handler 头两行（要往返，所以显式可见）
def on_hit(ev: lp.Hit) -> None:
    if ev.on is None or ev.on.buff_stack("SINKING") < 6:   # 1 往返，写在明面上
        return
```

- `(1)` 的实现在 C# 侧：`[[filter]]` 进 spec，`HookTable.Capture` 里在**入队前**判掉
  （`unit.GetActivatedBuffStack(keyword)` 是主线程内存读，微秒级）。等价于现有
  `@hook("OnGainBuff", keyword="SINKING")`（`docs/02:75`）的能力，但扩展到全部时机。
- `(2)` 的实现是 `HookSpec` 的 `where` 字段，在 `Registry.hooks_for()` 之后、调用 handler 之前求值。
- `(3)` 是"剩下的一切"，**故意让它看起来和 (1)(2) 不一样**（写在函数体里 = 有成本）。

#### 4.7.4 同步倍率泳道：`lp.on_damage_taken`（D9 的解法）

```python
def double_if_sinking(ev: lp.DamageTaken) -> int:
    if ev.on is not None and ev.on.buff_stack("SINKING") >= 1:   # 0 往返！
        return 200
    return 100

lp.on_damage_taken(double_if_sinking, watch=["SINKING"])
```

为什么它是 0 往返：这条泳道**不能等帧末的 `battle`**（它是同步的，必须在 20 ms 内回答）。
所以 spec 里声明 `watch=["SINKING"]`，C# 侧在回调里**当场**读这几个 keyword 的层数
（`GetActivatedBuffStack`，主线程内存读），塞进 payload：

```python
@dataclass(frozen=True, slots=True)
class DamageTaken:
    on: Unit | None                      # 受击者（快照字段已填）
    attacker: Unit | None
    buffs: Mapping[str, int]             # 只含 watch 里声明的关键字 → 层数（0 往返）
    incoming: int                        # 原始伤害
    source: str
    timing: str
```

这就是 D9 的答案：**"同步泳道读不到 buff"不是物理限制，是契约里没声明"我要读哪几个关键字"**。
声明之后，成本从"一次 IPC 往返（撞 20 ms 预算）"降到"几次内存读（微秒级）"。

两个入口指的是**同一批已经读好的数据**，所以都 0 往返：`ev.buffs` 是原始映射
（`{"SINKING": 8}`），`ev.on.buff_stack("SINKING")` 是它的便利读法（对 `watch` 之外的关键字
返回 `0`，**不会**再偷偷发一次往返）。要注意的是：`watch` 声明的是"我最关心哪几个关键字"，
所以**声明了才有值** —— 这是刻意的，因为两者都在同步预算里。

失败/超时语义不变：Python 侧没在预算内回答 → 内核用 `fallback`（默认 100）继续，游戏不卡。
`on_damage_taken` 的 handler 若抛异常 → 记一次失败 + 用 100，**不影响游戏**。

### 4.8 错误与降级：A 层消失时作者代码不炸

#### 4.8.1 导入永不失败

```python
# limbuspy/__init__.py（改写）
__all__ = ["units", "unit", "player", "battle", "round", "status", ...]

try:
    from .game.facade import (battle, buff_stacks, damage, heal, on_break, on_damage, ...)
    from .game.value import Battle, Buff, Unit, UnitState, ActionResult
    _IMPORT_ERROR = ""
except Exception as exc:                  # noqa: BLE001 - 只可能是打包/依赖问题
    # 用"全部不可用"的桩顶上，签名与真实现一致（静态检查器看到的是桩的类型注解）
    from .game._stub import (battle, buff_stacks, damage, heal, on_break, ...)
    _IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)
```

要点：桩**必须与真实现同签名**，由 `gen_contract.py` 从同一份 spec 生成
（`api/_stub.py`，生成物），所以桩不会漂移。

#### 4.8.2 三层可用性，作者只需要一层

```python
st = lp.status()
st.ready              # 内核连着 + A 层可用（最常用）
st.kernel             # 内核连上了
st.ops_available      # LimbusPy.Ops.dll 加载成功
st.game_build         # 游戏 build hash（与 stubs 对不对得上）
st.problems           # ["ops: missing implementation: unit.state", ...]  ← 直接抄进 issue
```

| 层 | 不可用时 | 作者代码 |
|---|---|---|
| 导入层 | `import limbuspy` 失败（打包坏了） | 桩顶上，`ready=False` |
| 内核层 | 游戏没开 / 桥断了 | `lp.units() == []`，`lp.unit() is None`，`lp.round() is None`，动作 `ok=False, reason="bridge-down"` |
| **A 层（`LimbusPy.Ops.dll`）** | 游戏更新导致程序集加载失败（`docs/05:131-138`） | 读：`[]`/`None`；动作：`ok=False, reason="ops-unavailable"`；事件：注册成功但**永不触发**，注册时打**一行**日志说明原因 |

于是"优雅降级"的写法是**普通的 None 检查**，不是 try/except：

```python
def on_turn_start(ev: lp.TurnStarted) -> None:
    u = ev.on                          # Unit | None
    if u is None or u.hp is None:      # 三种 None 都走这里
        return
    if u.hp_ratio is not None and u.hp_ratio < 0.6:
        lp.heal(u, 20)                 # 即使 A 层没了也只是 ok=False，不抛
```

#### 4.8.3 异常类型（只有 4 个，且只有 1 个常用）

```python
class LimbusPyError(Exception): ...            # 基类
class LimbusPyUnavailable(LimbusPyError): ...  # 只由 lp.require() / u.raw 抛（选择"硬失败"的人）
class BadArgument(LimbusPyError, ValueError): ...  # 作者的编程错误（负伤害、未知枚举名）
class StaleTarget(LimbusPyError): ...          # 只由 u.raw 抛（句柄过期）
```

`docs/02:328-336` 的错误对照表里，`stale_handle` / `marshal` / `frame budget exceeded` /
`notfound` 四条**全部从作者世界消失**：前两条只可能来自 `u.raw`，后两条是框架内部事件
（框架自己记日志 + 降级）。

### 4.9 docstring 规范（`gen_contract.py` 生成 + 手写补充）

#### 4.9.1 七段式

```
<一段式摘要：一句话说清"做什么"，不写"这个函数用于……">

<扩展段：语义陷阱 / 与谁等价 / 什么时候别用它>

Args:
    name (type): 说明。**单位**（crossing / 枚举 / 默认值理由）
    ...

Returns:
    type: 说明。特别写清 None 的三种原因。

Raises:
    BadArgument: 什么情况下抛（只列编程错误）

Cost:
    <n> crossing（op: `unit.damage`）—— 往返次数 + 对应的 op 名

Example:
    >>> ...
"""

```

**为什么 `Cost:` 是独立一段**：规则 3 的精神（FaustLauncher 把"属性很贵"写进文档），
但我们用**结构化的段**而不是散文，这样 `lp.dev.explain()` 和 CI 都能解析它，
`tools/lint_cost.py`（P1）可以校验"docstring 里的 crossing 数与实测一致"。

#### 4.9.2 完整真实样例（`lp.damage`）

```python
def damage(unit: Unit | int, amount: int, *, source: str = "PASSIVE") -> ActionResult:
    """对单位造成一笔伤害，走游戏自己的 HP 管线。

    **打伤害的唯一推荐入口。** 别自己拼 `ChangeHp`：它的第一个参数是「目标 HP 值」而不是
    增量（`ChangeStat.ChangeHp(int newHp, ...)`），写错方向就是把敌人治满血。本函数在 C# 侧
    已经做完了「读当前 HP → 夹到 0 → 调 ChangeHp → 读回新 HP」，所以也是一次原子操作：
    中间不会被帧边界或别的 mod 插进来。

    与 `set_hp(u, u.hp - 18)` 的区别：本函数会给 `OnChangeHp` 传正确的
    `DAMAGE_SOURCE_TYPE` 与 `BATTLE_EVENT_TIMING`；需要精确控制这两个枚举时用 `set_hp`。

    Args:
        unit: 目标单位。可以传 `Unit`，也可以传 `instance_id`（记账场景只存 id 时用）。
            传别的类型（`UnitInfo`、dict、`None`）会抛 `BadArgument` —— 不要猜。
        amount: 伤害点数，必须 > 0。传 0 或负数抛 `BadArgument`（"不打伤害"请用 `if`，
            不要让 API 猜你的意图）。
        source: 伤害来源枚举名（`DAMAGE_SOURCE_TYPE` 成员，如 `"PASSIVE"` / `"SKILL"`）。
            默认 `"PASSIVE"`：与 A 层现有 op 一致，且副作用最小。

    Returns:
        `ActionResult`。`ok=True` 时 `hp_before` / `hp_after` 是**这次调用前后**的真实 HP
        （可以直接拿去记账，不要再读一次）；`ok=False` 时 `reason` 说明原因，`unit` 为
        `None`。目标已死时 `ok=False, reason="target-dead"`，**不抛错**。

    Raises:
        BadArgument: `amount <= 0`、`source` 不是已知枚举名、`unit` 类型不可接受。
        LimbusPyUnavailable: 只在 `LIMBUSPY_STRICT=1` 且 A 层不可用时（默认降级为 ok=False）。

    Cost:
        1 crossing（op: `unit.damage`）。返回值里已带动作后的 HP，**不需要**再 `refresh()`。

    Example:
        >>> import limbuspy as lp
        >>> b = lp.battle()                       # 1 crossing
        >>> foe = b.lowest_hp()
        >>> r = lp.damage(foe, 18)                # 1 crossing
        >>> if r.ok:
        ...     print("%s: %d → %d" % (foe.display_name, r.hp_before, r.hp_after))
    """
```

（注意 `Example` 里 `foe.display_name` / `r.hp_before` 都是 0 往返 —— **文档示例本身在示范成本模型**。）

### 4.10 B 层（反射）保留，但要"不脏"

#### 4.10.1 作者什么时候才需要 B 层

| 场景 | 用哪层 | 判据 |
|---|---|---|
| 读战场 / buff / 读写 HP / 施加 buff·被动 | A 层（`lp.*`） | 覆盖 90% |
| 改伤害倍率、拦动作 | A 层事件（`on_damage_taken`）+ P2 的 `lp.patch` | 覆盖 8% |
| 冷门类型（技能/硬币/行动/卡牌/存档字段）、游戏刚更新 A 层还没跟上 | **B 层** | 剩下 2% |
| 调试：想知道这个 build 里某类型到底有什么成员 | B 层 `lp.reflect` | 工具 |

**唯一判据（沿用 `docs/05:19` 那句话）**：
> 写模组时不该出现游戏成员名字符串；出现了就是 B 层，应当有理由。

#### 4.10.2 B 层怎么做到"不脏"：三条规矩

```python
# 规矩 1：B 层只从 `.raw` 进，名字就一个（RawHandle）
u.raw.get("Hp")                       # 读（原始值，不包装）
u.raw.set("Hp", 245)
u.raw.call("ChangeHp", 245, "PASSIVE", "ON_SUCCESS_ATTACK")
u.raw.describe()                      # 这个 build 里这个类型有什么成员

# 规矩 2：B 层代码必须带 `# raw:` 标注块（CI 检查）
# raw: 原因——AddPassive 的 int 参数在 dump 里是 long，A 层还没覆盖；见 issue #123
u.raw.call("AddPassive", 601101)

# 规矩 3：B 层返回值可以随时"抬回" A 层
other = lp.unit(int(u.raw.get("TargetInstanceID")))    # 回到 Unit，属性继续 0 往返
```

实现：`RawHandle` 就是现在 `api/base.py:233-259` 的 `RawView`，改名为 `RawHandle`、
**只挂在 `Unit` 上**（不再有 `BoundObject.raw` 的第二条路），并且**返回值永远是原始 wire 值**
（不给它加任何便捷属性，避免"又一个半成品门面"）。

#### 4.10.3 2 MB 的 `bindings.py` 怎么办（D7）

**裁决：把 `bindings` 从默认导入路径上摘掉，但功能一个不少。**

| 变化 | 前 | 后 |
|---|---|---|
| `import limbuspy` | 0.007 s | 不变（不碰 bindings） |
| `import limbuspy.api.hooks`（作者的写法） | 触发 2 MB bindings + 可能 `ImportError` | **不触发**：`api/__init__.py` 改成 `__getattr__` 懒加载 `bindings`（PEP 562 只用于**这一个** `TYPE_CHECKING` 场景，并配 `api/__init__.pyi`） |
| 想用 1:1 绑定 | `from limbuspy.api import bindings` | `from limbuspy.api import bindings`（仍是唯一入口，但**只有你写了这行才付 0.19 s / 2 MB**） |
| 没生成过绑定 | `import limbuspy.api` 直接 `ImportError` | 只有 `import ... bindings` 才报，且提示可执行命令 |
| `_gen/`（36 MB `methods.json`） | 文档提到 | 明确写进文档："**只在开发机**，不随包分发，不进作者的视野" |

顺带把 `bindings` 的定位从"第二套门面"降为"**B 层的调试/补全工具**"：
`docs/05` §2 的表述改为"生成式绑定是 `u.raw` 的类型化辅助，不是第二条主路径"。

### 4.11 同一需求：旧写法 vs 新写法（6 组，每组两段完整代码）

> 所有"旧写法"都逐字来自仓库现有示例/文档（标注了出处），不是编的；
> 所有"新写法"只用 §4.2–§4.10 定义的 API。

#### 对照 1｜读全场并给血最低的敌人一刀

**旧**（`burst_on_hit/__init__.py:48-86` 精简，保留全部概念）

```python
from typing import Any
from limbuspy.api.hooks import AttackSucceededPayload, on
from limbuspy.mod import Mod, config

class BurstOnHit(Mod):
    name = "burst_on_hit"
    cfg = config(threshold=6, max_stacks=8, hp_ratio_ceiling=0.75)

    @on("AttackSucceeded")
    def on_attack(self, ev: AttackSucceededPayload, ctx: Any) -> None:
        if ev.target is None:                    # 概念：UnitRef 可能是 None
            return
        ops = ctx.ops                             # 概念：ctx / ops / 缓存域
        enemies = [u for u in ops.battle.units() if not u.is_player]   # 1 crossing
        if not enemies:
            return
        best = min(enemies, key=lambda u: u.hp)  # 概念：UnitInfo 是扁平值
        info = ops.unit.info(best.instance_id)   # 1 crossing（要 UnitInfo→int 转换）
        if info.hp_ratio > 0.75:
            return
        ops.unit.damage(best.instance_id, 18)    # 1 crossing（不能传 UnitInfo！）
```

*概念数 9*：`Mod` 子类、`config`、`@on` + payload 类型、`ctx`、`ctx.ops`、`UnitRef`、
`UnitInfo`、`instance_id` 转换、缓存域。*往返 3*。*要读的文档 3 份*。

**新**

```python
import limbuspy as lp

def on_attack(ev: lp.Hit) -> None:
    b = ev.battle                                 # 0 crossing（随事件来的快照）
    foe = b.lowest_hp()                           # 0 crossing
    if foe is None or (foe.hp_ratio or 0.0) > 0.75:
        return
    lp.damage(foe, 18)                            # 1 crossing（Unit 直接可传）

lp.on_hit(on_attack)
```

*概念数 3*：`lp`、`ev.battle`、`lp.damage`。*往返 1*。*要读的文档 1 份*。

#### 对照 2｜受击者身上有 SINKING 时伤害翻倍（D9）

**旧**（现状只能投机：`sinking_tide/__init__.py:246-256` + `docs/02:184-195` 的规则）

```python
from limbuspy.mod import Mod, hook, config

class SinkingTide(Mod):
    cfg = config(amplify_take_damage=False, damage_per_stack=3)

    @hook("ChangeTakeDamage", sync=True, fallback=100)
    def take_damage_multiplier(self, ctx) -> int:
        """同步钩子会阻塞游戏主线程等 Python 回包（默认 20 ms）。
        所以这里只读内存里的两个字段，绝不 ctx.call/get。"""
        if not self.cfg.amplify_take_damage or not self.tide_armed:
            return 100
        return 100 + max(0, int(self.cfg.damage_per_stack) * 5)
    # ↑ 这里读的是 mod 自己的开关，不是受击者的 buff —— 因为同步泳道读不到。
    #   真要按 buff 判定，只能再加 Mod.patch("BattleUnitModel","OnChangeHp",..., sig=[...])
```

*概念数 9*：`@hook` + `sync` + `fallback`、`ctx`、`ctx.event["args"]` 索引（若走 patch 路）、
`Mod.patch`、`sig` 签名列表、Harmony kind、`BUFF_UNIQUE_KEYWORD` 哨兵 2123、20 ms 预算、帧预算规则。
*要读 3 份文档*。**结论：做不到；示例自己绕开了。**

**新**

```python
import limbuspy as lp

def double_if_sinking(ev: lp.DamageTaken) -> int:
    if ev.on is not None and ev.on.buff_stack("SINKING") >= 1:   # 0 crossing（watch 声明过）
        return 200
    return 100

lp.on_damage_taken(double_if_sinking, watch=["SINKING"])
```

*概念数 2*：`lp`、`ev.buffs`（`ev.on.buff_stack` 从 `watch` 里查，0 往返）。*往返 0*。*文档 1 份*。

#### 对照 3｜记录每一笔伤害（0 往返记账）

**旧**（`damage_ledger/__init__.py:42-70`）

```python
import collections
from typing import Any, Dict
from limbuspy.api.hooks import DamageDealtPayload, UnitDiedPayload, on
from limbuspy.mod import Mod, config, expose

class DamageLedger(Mod):
    cfg = config(keep_recent=8, count_overkill=True)

    def __init__(self) -> None:
        super().__init__()
        self.totals: Dict[str, Dict[str, int]] = {}
        self.recent = collections.deque(maxlen=32)
        self.deaths: list[Dict[str, Any]] = []

    @on("DamageDealt")
    def on_damage(self, ev: DamageDealtPayload) -> None:
        if ev.unit is None:
            return
        lost = int(ev.old_hp) - int(ev.new_hp)
        row = self.totals.setdefault(ev.unit.key, {"taken": 0, "healed": 0, "hits": 0})
        if lost > 0:
            row["taken"] += lost
            row["hits"] += 1
        self.recent.append({"unit": ev.unit.key, "from": int(ev.old_hp), "to": int(ev.new_hp)})

    @expose("ledger.report")
    def report(self, ctx) -> Dict[str, Any]:
        return {"mod": self.name, "totals": self.totals, "recent": list(self.recent)}
```

*概念数 7*：`Mod`、`config`、`@on` + payload、`__init__` + `super().__init__()`、`UnitRef.key`、
`@expose` + `ctx`、`int(...)` 强制转换（payload 字段名义上是 `int` 但作者不信）。

**新**

```python
import collections
import limbuspy as lp

totals: dict[str, dict[str, int]] = {}
recent: collections.deque = collections.deque(maxlen=32)

def on_damage(ev: lp.Damage) -> None:
    if ev.on is None:
        return
    lost = ev.old_hp - ev.new_hp
    row = totals.setdefault(ev.on.key, {"taken": 0, "healed": 0, "hits": 0})
    if lost > 0:
        row["taken"] += lost
        row["hits"] += 1
    recent.append({"unit": ev.on.key, "from": ev.old_hp, "to": ev.new_hp})

lp.on_damage(on_damage)
lp.expose("ledger.report", lambda: {"totals": totals, "recent": list(recent)})
```

*概念数 3*：`lp`、`ev`、dict。往返 0（两边都是 0），但**概念从 7 降到 3**，且 `ev.old_hp`
是真 `int`（不需要 `int(...)`）。状态用模块级变量 —— `lp.expose(name, fn)` 是 P1 新增的
"把函数暴露给内核/REPL"的模块级形态（等价 `@expose`，`docs/02:64`）。

#### 对照 4｜回合开始按血量治疗（读一次 + 写一次）

**旧**（`field_medic/__init__.py:53-83`）

```python
from typing import Any, Dict
from limbuspy.api.hooks import TurnStartedPayload, on
from limbuspy.mod import Mod, config, expose

class FieldMedic(Mod):
    cfg = config(hp_ratio=0.6, base_amount=8, per_round=2, max_rounds=5)

    def __init__(self) -> None:
        super().__init__()
        self.turn_seq = 0
        self.heals = 0

    @on("TurnStarted")
    def on_turn(self, ev: TurnStartedPayload, ctx: Any) -> None:
        if ev.unit is None:
            return
        self.turn_seq += 1
        ops = ctx.ops
        info = ops.unit.info(ev.unit)                     # crossing 1（读）
        if info.is_dead or info.hp >= info.max_hp:
            return
        if info.hp_ratio > float(self.cfg.hp_ratio):
            return
        amount = int(self.cfg.base_amount) + min(
            int(self.cfg.max_rounds), self.turn_seq - 1) * int(self.cfg.per_round)
        after = ops.unit.heal(ev.unit, amount)            # crossing 2（写）
        self.heals += 1
        self.log("治疗 %s：%d → %d" % (ev.unit.key, info.hp, after.hp))

    @expose("medic.report")
    def report(self, ctx) -> Dict[str, Any]:
        return {"heals": self.heals, "config": self.cfg.as_dict()}
```

*概念数 8*。*往返 2*（读 1 + 写 1），但**必须知道 `info` 是值、`ev.unit` 是引用**（否则
`ev.unit.hp` 会 AttributeError）。

**新**

```python
import limbuspy as lp

heals = 0
turn_seq = 0

def on_turn(ev: lp.TurnStarted) -> None:
    global heals, turn_seq
    turn_seq += 1
    u = ev.on                                        # Unit | None，自带快照
    if u is None or u.hp is None or u.max_hp is None or u.is_dead:
        return
    if u.hp_ratio is None or u.hp_ratio > 0.6:
        return
    amount = 8 + min(5, turn_seq - 1) * 2
    r = lp.heal(u, amount)                           # 1 crossing，结果自带新 HP
    if r.ok:
        heals += 1
        print("[%s] 治疗 %s：%d → %d" % (ev.battle.round, u.display_name, r.hp_before, r.hp_after))

lp.on_turn_start(on_turn)
```

*概念数 3*。*往返 1*（`ev.on` 是 0，`heal` 是 1）。配置改成常量函数参数（P2 给
`lp.config(...)` 的模块级形态）。

#### 对照 5｜读某 buff 层数（N+1 陷阱，D8 实证）

**旧**（`game/objects.py:217-230` 的 `buff()` + `sinking_tide/__init__.py:322-326` 的循环）

```python
from limbuspy.game import battle as gb

book = gb.current(ctx.session)          # 1 次 SNAPSHOT
for unit in book.enemy_units:
    # 下面这一行：1 次 GetActivatedBuffModels + 每个 buff 各 1 次 IsKeyword
    stacks = unit.buff_stack(SINKING)   # = 1 + N 次往返（N = 该单位的 buff 数）
    scored.append((stacks, -unit.hp_ratio, unit))
```

11 个敌人 × 9 个 buff ≈ **100 次往返**，而作者写出来是**两行**，看起来像内存读。

**新**

```python
import limbuspy as lp

stacks = lp.battle().buff_stacks("SINKING")          # 1 crossing -> {instance_id: stacks}
scored = [(stacks.get(u.instance_id, 0), -(u.hp_ratio or 0.0), u) 
          for u in lp.battle().enemies]              # 0 crossing
```

*往返 1*（全场），而且**要写成 N+1 反而更麻烦**（得显式循环调 `u.buff_stack`，一眼能看出成本）。

#### 对照 6｜"我想读一个 A 层没覆盖的东西"（B 层）

**旧**（`docs/02:228` + `api/base.py:244-252` + `sinking_tide/__init__.py:369-394`）

```python
from limbuspy.mod import Mod
from limbuspy.runtime.errors import BridgeError

class X(Mod):
    def _deal_damage(self, unit, new_hp: int, result: dict) -> bool:
        """dump 里 BattleUnitModel.ChangeHp 的签名是 ……（8 个参数）"""
        try:
            unit.call("ChangeHp", int(new_hp), "PASSIVE", "ON_SUCCESS_ATTACK",
                      None, None, None, NO_KEYWORD, False, False)   # 概念：哨兵 2123 / 枚举名 / 参数顺序
            result["applied"].append("ChangeHp")
            return True
        except BridgeError as exc:                                  # 概念：BridgeError
            result["errors"].append("ChangeHp: %s" % exc)
            return False
```

*概念数 6*：`unit.call`、哨兵值 2123、枚举名字符串、参数顺序、`BridgeError`、`result` dict 约定。

**新**

```python
import limbuspy as lp

def burst_plus(u: lp.Unit, hp_target: int) -> bool:
    # raw: A 层 set_hp 走的是同一根管线，这里只是演示 B 层形状；真实 mod 请用 lp.set_hp。
    u.raw.call("ChangeHp", hp_target, "PASSIVE", "ON_SUCCESS_ATTACK",
               None, None, None, 2123, False, False)
    return bool(u.raw.get("Hp") == hp_target)
```

*概念数 2*：`lp`、`u.raw`。**注意**：新写法没有消灭"哨兵 2123"（那是游戏知识，不可能消灭），
但它消灭了"`BridgeError` / `result` dict 约定 / `Mod` 子类"这三个**框架概念**。
B 层不脏的定义是：**脏的只能是游戏知识，不能是传输层知识。**

---

## 5. 实现方案

### 5.1 Python 侧：文件清单（路径 + 职责 + 预估行数 + 手写/生成）

#### 5.1.1 新增（手写）

| 文件 | 职责 | 行数 |
|---|---|---|
| `python/limbuspy/_ctx.py` | 当前回调的 `Game` 绑定（`contextvars`）、`current()` / `bind()` / `unbind()` / `background()`；**唯一的线程局部状态** | ~110 |
| `python/limbuspy/_registry.py` | 模块级注册的待注册表：`CURRENT_MOD`（`ContextVar`）、`register(timing, fn, filters)`、`flush()`、`reset()`；供 `mod/loader.py` 调用 | ~120 |
| `python/limbuspy/game/facade.py` | **`lp.*` 的实现**：`Game` 类（`units/unit/player/battle/round/buff_stacks/status/snapshot/damage/heal/...`）+ 模块级薄函数。全部真签名、真 docstring、`Cost:` 段 | ~340 |
| `python/limbuspy/game/value.py` | `Unit` / `Buff` / `Battle` / `UnitState` / `ActionResult`。`Unit` 继承生成的属性 mixin（§5.1.2），只有"带成本"的方法手写 | ~300 |
| `python/limbuspy/game/actions.py` | 动作函数的参数校验 + op 调用 + `ActionResult` 组装（`BadArgument` 只在这里抛） | ~200 |
| `python/limbuspy/game/events.py` | `on_hit/on_damage/on_death/on_break/on_turn_start/on_damage_taken` + 过滤器装配（`buff=`/`keyword=`/`side=`→C# 侧 filter；`where=`→Python 谓词） | ~260 |
| `python/limbuspy/game/errors.py` | `LimbusPyError` / `LimbusPyUnavailable` / `BadArgument` / `StaleTarget` | ~60 |
| `python/limbuspy/game/dev.py` | `lp.dev.cost()` / `explain()` / `reset()`；`LIMBUSPY_COST_WARN` 的计数器与警告 | ~130 |
| `python/limbuspy/game/_probe.py` | `Status` 组装：从 `bridge.hello` + `ops_manifest` + `problems` 推出 `ready/kernel/ops_available/game_build/problems` | ~90 |
| `python/limbuspy/api/raw.py` | `RawHandle`（现 `base.py::RawView` 的搬迁 + `.describe()` + `# raw:` 标注检查入口） | ~90 |
| **小计** | | **~1 700** |

#### 5.1.2 新增（继续由 `tools/gen_contract.py` 生成）

| 文件 | 内容 | 行数 |
|---|---|---|
| `python/limbuspy/api/unit_props.py` | `UnitProps` mixin：16 个真 `@property`（含 `display_name` 的本地查表逻辑）+ `state()`/`buff_stack()` 等方法的签名桩；**每个属性一段 docstring，含 `Cost: 0 crossing (snapshot field)`** | ~320 |
| `python/limbuspy/api/_stub.py` | 与真实现同签名的降级桩（`import limbuspy` 失败时顶上，见 §4.8.1） | ~140 |
| `python/limbuspy/api/ops.py` | 扩：新增 4 个 op 方法 + `Cost:` 段 + `ActionResult` 返回类型 | ~260（现 199） |
| `python/limbuspy/api/hooks.py` | 扩：6 个 payload dataclass（含 `battle`/`buffs` 字段）+ `on_*` 的 overload + `where` 参数 | ~300（现 188） |
| `python/limbuspy/api/_contract_manifest.json` | + `kind` / `cost` / `batchable` / `filters` / `lane` | — |
| `kernel/LimbusPy.Ops/OpsManifest.g.cs` | + 新 op / 新 hook / filter 表 | ~120（现 88） |

#### 5.1.3 改写（手写）

| 文件 | 改动 | 行数变化 |
|---|---|---|
| `python/limbuspy/__init__.py` | 导出 14 个教程级名字 + 3 个子命名空间 + 类型；`try/except` 落到 `_stub`；**不 import `bindings`** | 20 → ~120 |
| `python/limbuspy/api/__init__.py` | `bindings` 改懒加载（PEP 562 只此一处）+ `api/__init__.pyi` | 50 → ~70 |
| `python/limbuspy/game/__init__.py` | 旧名（`Unit`/`Buff`/`Battle`/`Session`）保留并加一次性 `DeprecationWarning`；新名重导出 | 43 → ~90 |
| `python/limbuspy/mod/base.py` | `Mod.session`/`Mod.ops` 变弃用垫片（返回 `lp.game()` 的等价物）；`Mod` 的 `cfg`/`patch`/`log` 保留 | 150 → ~190 |
| `python/limbuspy/mod/context.py` | `ctx` 不再交给作者；`Context` 内部化，新增 `battle` 字段；`ops/session` 属性保留但弃用 | 137 → ~170 |
| `python/limbuspy/mod/runtime.py` | 派发前 `_ctx.bind(game)` / `finally unbind`；把批级 `battle` 注入每个 `event`；`_on_sync_call` 同样绑定 | 526 → ~590 |
| `python/limbuspy/mod/loader.py` | `import_mod` 前后设置/清理 `CURRENT_MOD`；`register_instance` 先 `flush()` 待注册表 | 430 → ~460 |
| `python/limbuspy/_mock/ops.py` | 新增 op 的参考实现：`unit.state` / `battle.snapshot` / `battle.buff_stacks` / `unit.buff_stacks_many` / 动作返回 `ActionResult` 形状 | 13 op → 18 op（~+200 行） |
| `python/limbuspy/_mock/kernel.py` | `EVENTBATCH` 里带批级 `battle`；`OPCALL` 未知 op 的报错文案 | +40 |

#### 5.1.4 新增测试与示例

| 文件 | 覆盖 | 行数 |
|---|---|---|
| `python/tests/test_facade_api.py` | §4.2 全部函数 + §4.3 值对象语义（`None`、`__eq__`、`__repr__`、快照不变量）+ §4.11 六组对照的"新写法"逐条跑通 | ~320 |
| `python/tests/test_facade_degrade.py` | A 层缺失 / 桥断 / 未连接三种降级矩阵（读返回空、写 `ok=False`、导入不失败） | ~140 |
| `python/tests/test_facade_cost.py` | 用 `MockBridge.counters` 断言**每类操作的 crossing 数与 docstring `Cost:` 一致**（这是防"文档漂移"的硬闸） | ~160 |
| `python/tests/test_registry_module_level.py` | 模块顶层注册、热重载后注册表重建、多 mod 归属正确 | ~110 |
| `python/examples/sinking_double/__init__.py` | **新的旗舰示例**：20 行做完整件事（对照 2 的"新"） | ~35 |
| `python/examples/run_facade_api.py` | 驱动器：在 mock kernel 上跑 6 个场景，打印 crossing 计数 | ~160 |

### 5.2 契约侧：`ops/spec/api.toml` 要扩哪些字段

生成器 `tools/gen_contract.py` 目前只认 `name/class/method/summary/doc/stability/args/returns/returns_doc`
（`gen_contract.py:107-193`）与 `timing/csharp/summary/payload`（`:274-339`）。需要扩成：

#### 5.2.1 新增的 `op` 字段

```toml
[meta]
version = "2.0"                 # 契约版本：A 层新增 5 个 op/2 个 hook，且返回类型集合扩大
stability = "stable"

# ======================================================================================
# 读：一次 crossing 拿一份完整快照（属性 0 往返的基础）
# ======================================================================================

[[op]]
name = "battle.snapshot"
group = "battle"
class = "BattleOps"
method = "snapshot"
kind = "read"                   # read | action | experimental  ← 文档分区与 lint 用
cost = 1                        # crossing 次数（写进 docstring 的 Cost: 段，且被 lint 校验）
batchable = true                # 可进 BATCH 帧（一次往返 N 个）
summary = "一次往返拿全场：单位表（含基础字段）+ 回合 + 波次。"
doc = """
`lp.battle()` / `lp.units()` / `lp.unit()` / `lp.player()` 全部走它。

**这是"属性 0 往返"的地基**：单位的基础字段（hp/max_hp/mp/speed/level/生死/阵营…）在这里
一次拿全，之后 `u.hp` 只是内存读。要 buff 列表就加 `buffs = true`（多约 1 KB/单位）。
"""
args = [
  { name = "buffs", type = "bool", default = "False", doc = "是否连带每个单位的 buff 列表" },
  { name = "limit", type = "int",  default = "0",     doc = "最多几个单位（0 = 全部，调试用）" },
]
returns = "Battle"
returns_doc = "round/wave/units（每项 16 字段，可选 buffs）"

[[op]]
name = "unit.state"
group = "unit"
class = "UnitOps"
method = "state"
kind = "read"
cost = 1
batchable = true
summary = "一个单位的 16 个字段（含 buff 层数摘要），一次 crossing。"
args = [
  { name = "unit",  type = "unit", doc = "目标单位" },
  { name = "buffs", type = "bool", default = "False", doc = "是否带 buff 层数摘要" },
]
returns = "UnitState"
returns_doc = "见 api/types.py::UnitState"

[[op]]
name = "battle.buff_stacks"
group = "battle"
class = "BattleOps"
method = "buff_stacks"
kind = "read"
cost = 1
batchable = true
summary = "全场某关键字的 buff 层数，一次 crossing 拿 N 个单位（消灭 N+1）。"
doc = """
`{instance_id: stacks}`。等价于对每个单位各调一次 `unit.buff_stacks`，但只有 1 次往返。
没有该 buff 的单位**不出现在结果里**（用 `.get(id, 0)`）。
"""
args = [
  { name = "keyword", type = "buff_keyword", doc = "Buff 主关键字，如 \"SINKING\"" },
]
returns = "Dict[int, int]"

# ======================================================================================
# 写：动作类。返回 ActionResult（ok/新状态/原因），失败不抛错
# ======================================================================================

[[op]]
name = "unit.damage"
group = "unit"
class = "UnitOps"
method = "damage"
kind = "action"
cost = 1
summary = "对单位造成伤害（内部处理 ChangeHp 的语义与枚举）。"
returns = "ActionResult"        # ← 由 UnitInfo 改为 ActionResult（Python 侧包装，wire 仍是扁平 map）
returns_fields = ["ok", "hp_before", "hp_after", "applied", "reason"]
returns_doc = "动作前后的 HP；ok=False 时 reason 说明原因"
args = [
  { name = "unit",   type = "unit",   doc = "目标单位（Unit 或 instance_id）" },
  { name = "amount", type = "int",    doc = "伤害点数（正数）", validate = "positive" },
  { name = "source", type = "source", doc = "伤害来源枚举名", default = "\"PASSIVE\"" },
  { name = "timing", type = "timing", doc = "事件时机枚举名", default = "\"ON_SUCCESS_ATTACK\"", hidden = true },
]
```

**字段语义表**（给 `gen_contract.py` 的读者）：

| 字段 | 取值 | 生成物里体现为 | 谁用 |
|---|---|---|---|
| `kind` | `read` / `action` / `experimental` | 文档分区；`experimental` 在 docstring 顶部加 `**实验性**` 横幅 | 文档 + `lp.status()` |
| `cost` | 整数 crossing | docstring 的 `Cost:` 段 | `tools/lint_cost.py`、`lp.dev.explain()` |
| `batchable` | bool | `Ops` 类上的 `_BATCHABLE` 集合；Python 侧 `lp.batch([...])` 用它决定能否合并 | P2 的批处理优化 |
| `group` | op 名第一段 | 生成类的归属（现在是 `class`，`group` 作为显式的第二维度，允许 `unit.read` 与 `unit.write` 分文件） | 文档分层 |
| `hidden` | bool | 参数不出现在生成的签名里（但仍能传 kwarg） | 让 `damage()` 只露 2 个参数 |
| `validate` | `positive` / `enum:...` | 生成 `BadArgument` 的前置检查 | 动作函数 |
| `returns_fields` | 字段名列表 | 生成 `ActionResult` 的构造调用与 `Returns:` 文档 | 返回形态统一 |

#### 5.2.2 新增的 `hook` 字段

```toml
[[hook]]
timing = "AttackSucceeded"
lane = "async"                  # async（帧末 EVENTBATCH）| sync（阻塞，硬预算）
include_battle = true           # 批级 battle 快照是否随这个时机一起送（§4.5 机制 3）
cost = 0                        # 事件读字段本身的 crossing（payload 自带 → 0）
summary = "一次攻击命中结算完成（带伤害分解）。"
csharp = { target = "BattleUnitModel", method = "OnSucceedAttack", kind = "postfix" }

# 声明式过滤：C# 侧入队前判定（0 次往返）→ 生成 on_hit(fn, buff=..., side=...)
filters = [
  { name = "buff",    type = "buff_keyword", where = "target", csharp = "GetActivatedBuffStack" },
  { name = "keyword", type = "unit_keyword", where = "target", csharp = "HasUnitKeyword" },
  { name = "side",    type = "enum", choices = ["player", "enemy"], where = "target" },
  { name = "critical", type = "bool", where = "payload" },   # payload 已有，纯 Python 侧判
]

payload = [
  { name = "unit",         type = "unit", doc = "攻击方", alias = "attacker" },
  { name = "target",       type = "unit", optional = true, alias = "on", doc = "受击方" },
  { name = "final_damage", type = "int",  alias = "damage" },
  { name = "real_damage",  type = "int" },
  { name = "hp_damage",    type = "int" },
  { name = "is_critical",  type = "bool", alias = "critical" },
  { name = "timing",       type = "str" },
  { name = "battle",       type = "battle", doc = "批级全场快照（0 往返）" },
]

# ======================================================================================
# 同步倍率泳道（新增）：声明 watch 的关键字 → C# 侧当场读进 payload → 0 往返
# ======================================================================================

[[hook]]
timing = "DamageTaken"
lane = "sync"
include_battle = false          # 同步泳道不能等帧末快照
fallback = 100                  # Python 没在预算内回答 → 用 100（游戏不卡）
cost = 0
summary = "单位即将受到伤害时给一个百分比倍率（100 = 原样）。"
doc = """
**这条泳道会阻塞游戏主线程**等 Python 回包（默认 20 ms，`docs/02:189`）。所以它的 payload
在 C# 侧**当场读好**：`watch` 里声明的每个 Buff 关键字各读一次层数（主线程内存读，微秒级）。

这也是"为什么今天做不到'受击者有 SINKING 就翻倍'"的解法：不是物理限制，是契约没声明要读什么。
"""
watch = { keyword_type = "buff_keyword", default = ["SINKING"] }
csharp = { target = "BattleUnitModel", method = "ChangeTakeDamage", kind = "prefix", returns = "int" }
payload = [
  { name = "unit",     type = "unit", doc = "受击方", alias = "on" },
  { name = "attacker", type = "unit", optional = true },
  { name = "buffs",    type = "buff_map", doc = "watch 声明的关键字 → 层数（0 往返）" },
  { name = "incoming", type = "int", doc = "原始伤害" },
  { name = "source",   type = "str" },
  { name = "timing",   type = "str" },
]
```

#### 5.2.3 生成器要改的具体位置

| `gen_contract.py` | 改动 |
|---|---|
| `ARG_TYPES`（`:37-48`） | 不变（已有 `unit/int/float/bool/str/timing/source/buff_keyword/unit_keyword`） |
| `RETURN_TYPES`（`:51-62`） | **新增** `"UnitState"` / `"Battle"` / `"ActionResult"` / `"Dict[int, int]"` / `"Optional[Unit]"` / `"Buff"`；每项给 `(注解, 转换表达式, imports)` 三元组 |
| `emit_ops`（`:107-193`） | 从 `op.get("kind")` 决定 docstring 横幅；从 `cost` 生成 `Cost:` 段；从 `hidden` 过滤参数；从 `validate` 生成前置检查；`returns_fields` 生成 `ActionResult` 构造 |
| `emit_hooks`（`:274-339`） | 新生成 `unit_props.py`（新函数 `emit_unit_props`）；payload 支持 `alias`（生成同名 `@property` 别名）；支持 `type = "battle"` / `"buff_map"`；从 `filters` 生成 `on_*` 的关键字参数与 docstring 表格 |
| `emit_csharp`（`:347-377`） | `OpSpec` 增字段；`HookSpec` 增 `lane`/`filters`/`watch`/`include_battle`；新增 `FilterSpec[]` |
| `emit_manifest`（`:385-412`） | 带上 `kind`/`cost`/`batchable`/`lane`/`filters`（运行时协商与 `lp.status()` 用） |
| 新增 `emit_unit_props` | 从新的 spec 段 `[[unit_property]]` 生成 `UnitProps` mixin |
| 新增 `emit_stub` | 从同一份 spec 生成 `api/_stub.py`（签名一致、实现全降级） |

`[[unit_property]]` 段示例（`Unit` 的属性也进契约，才能"单一真相 + 静态可查 + 成本可标注"）：

```toml
[[unit_property]]
name = "hp"
type = "Optional[int]"
source = "field"          # field = 快照字段 | op = 要往返 | computed = 派生
summary = "当前血量（已解混淆）。"
cost = 0
csharp = "_hp"            # ObscuredInt → int，C# 侧已解开

[[unit_property]]
name = "hp_ratio"
type = "Optional[float]"
source = "computed"
expr = "None if not self.max_hp else self.hp / self.max_hp"
cost = 0
summary = "血量比例 0..1；max_hp 为 0 或读不到时 None（**不是 0.0**）。"
```

### 5.3 C# 侧：`IOpsProvider` / `OpsApi.cs` / `LimbusPy.Ops`

| 文件 | 改动 | 行数 |
|---|---|---|
| `kernel/LimbusPy.Bridge.Core/Ops/OpsApi.cs` | `OpSpec` 增 `Kind` / `Cost` / `Batchable` / `ReturnsFields`；`HookSpec` 增 `Lane` / `IncludeBattle` / `Filters` / `Watch`；新增 `FilterSpec`（`Name/ArgType/Where/CSharp`）；`IOpsHost` 增 **`EmitBatch(IReadOnlyList<object> events, object battle)`**；`IOpsProvider` 增 `object InvokeBatch(object[] calls)`（进 `BATCH` 帧） | 113 → ~230 |
| `kernel/LimbusPy.Ops/OpsArgs.cs` | 新增 `State(BattleUnitModel, bool withBuffs)`（16 字段）、`BattleTable(bool withBuffs, int limit)`（批级快照）、`BuffMap(unit, IReadOnlyList<keyword>)`、`FilterMatch(FilterSpec, unit, payload)`；`Info()` 保留（向后兼容） | 6.8 KB → ~9.5 KB |
| `kernel/LimbusPy.Ops/UnitOps.cs` | 新增 `State`、`BuffStacksMany`（单单位多关键字）；`Damage/Heal/SetHp` 的返回 map 加 `ok` / `reason`；`AddBuff` 从 `experimental` 升 `stable`（真机验证后，见 P2） | 196 → ~290 |
| `kernel/LimbusPy.Ops/BattleOps.cs` | 新增 `Snapshot`（= 现 `Units` + `Round` 合并，**含 `buffs` 开关与 `limit`**）、`BuffStacks(keyword)`（遍历一次字典，产出 `{instance_id: stacks}`）；`Round` 保留供 `battle.round` op | 119 → ~240 |
| `kernel/LimbusPy.Ops/HookTable.cs` | ① `Capture` 里 **filter gate**（入队前 `FilterMatch`）；② `Tick` 里 **每次 flush 只走一次 `BattleTable`**，通过 `EmitBatch` 送批级快照；③ 新增 `CuratedHooks.SyncPrefix`（`ChangeTakeDamage` 的 prefix，读 `watch` 关键字 → 塞进事件 → 由内核 SYNCALL 拿回包 → 返回倍率） | 272 → ~470 |
| `kernel/LimbusPy.Ops/OpsEntry.cs` | 注册 5 个新 op；`Tick` 改调 `_hooks.Tick()`（内部已批量）；自检覆盖新 op（清单↔实现）；`Manifest()` 输出新字段 | 150 → ~210 |
| `kernel/LimbusPy.Ops/CuratedHooksSync.cs`（**新增**） | 同步泳道的 Harmony 静态入口（与现有 `CuratedHooks` 同款：IL2CPP 无 JIT 必须手写静态方法） | ~110 |
| `kernel/LimbusPy.Bridge.Core/Bridge/RequestRouter.cs` | `BATCH` 帧：让 `OPCALL` 也能进批（现在只支持 `reflect.get`），走 `InvokeBatch` | +~50 |
| `kernel/LimbusPy.Ops/OpsManifest.g.cs` | 生成物 | ~120 |

**C# 侧的两条硬约束**（写进实现注释，避免后面的人踩）：

1. **`Tick()` 里的 `BattleTable` 一帧只走一次**。现在的 `Shape()`（`HookTable.cs:123-179`）是按
   事件拆参数、不碰管理器（O(1)），这是它敢挂在主线程上的原因。加批级快照后必须**摊到帧级**，
   不能按事件摊（否则一次连击 20 段就是 20 × 11 次属性读）。
2. **同步泳道不碰 Python 的批级快照**。`DamageTaken` 的 payload 必须在补丁回调里当场拼完
   （只读 `watch` 声明的关键字），否则会撞 20 ms 预算。

### 5.4 向后兼容：明确建议（**保留 + 弃用 + 双轨，不直接移除**）

| 旧写法 | 处理 | 期限 |
|---|---|---|
| `class X(Mod)` | **保留**，且不再是必须 | 永久（有状态 mod 仍推荐） |
| `ctx.ops` / `ctx.session` | **保留 + `DeprecationWarning`**，转发到新 `Game` | 0.3 移除 `ctx.session`，0.4 移除 `ctx.ops` |
| `Mod.ops` / `Mod.session` | **保留 + `DeprecationWarning`**，`Mod.ops` 转发到一个后台 `Game`（语义：无缓存、读当次真值，**与现在完全一致**） | 0.3 从文档移除，0.4 删代码 |
| `@hook("Timing")` | **保留**（底层补丁/自定义时机的唯一入口） | 永久 |
| `@on("Timing")` + payload 类 | **保留**（`on()` 内部转发到 `lp.on_*` 的实现），签名 `(self, ev[, ctx])` 继续工作 | 0.3 文档不推荐，0.4 保留但不宣传 |
| `ApiTypes.UnitInfo` | **保留**为 `Unit` 的"扁平视图"（`UnitInfo.from_unit(u)` + `===` 不成立但字段同名） | 永久（它是 `unit.state()` 的值类型） |
| `api.bindings`（2 MB） | **保留**，但从默认导入路径摘除（§4.10.3） | 永久（B 层工具） |
| `game.objects.Unit/Buff/Battle` | **保留**，`game/battle.py::current(session)` 等入口加弃用警告 | 0.4 用 `lp.battle()` 取代 |

**为什么不全删**：`tools/requirements/limbuspy.json` 与 `doctors` 校验的是**成员名**（B 层契约），
`api.bindings` 的 5243 个成员是"什么都能调"的保底（`docs/05:229`）。把 B 层删掉等于砍掉能力上限；
我们要砍的是**B 层出现在主路径上**这件事。

弃用警告的实现要点（否则会刷屏）：

```python
# limbuspy/_deprecation.py
_WARNED: set[str] = set()
def warn_once(key: str, message: str) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    warnings.warn(message, DeprecationWarning, stacklevel=3)
```

同一个名字一个进程只警告一次（`ctx.session` 在第一回合警告一次即可）；
`LIMBUSPY_DEPRECATION=error` 时改成抛错，给"我要确认自己迁移干净了"的人用。

### 5.5 分阶段落地

#### P0 — 骨架能跑（5–6 人日）

**范围**：`battle.snapshot` + `unit.state` op → `Game`/`Unit`/`Buff`/`Battle` 值对象 →
`lp.units/unit/player/battle/round/damage/heal` → 模块顶层注册（`lp.on_hit/on_damage`）→
降级与 `lp.status()` → mock 实现 + 3 个新测试文件 → 一个新的 20 行旗舰示例。

**验收**（硬指标，可执行）：

1. `python -m pytest python/tests -q` 全绿，**旧测试一个都不改**；
2. `python tools/gen_contract.py --check` 退出码 0；
3. `python python/examples/run_facade_api.py` 打印 `6 场景 / 0 失败`，且
   `lp.dev.cost()` 与 §4.11 里标注的 crossing 数逐条一致；
4. 新的 20 行 `sinking_double` 示例在 mock kernel 上触发一次翻倍，`counters.op_calls == 0`
   （同步泳道 0 往返）；
5. `python -c "import limbuspy"` **不** import `api.bindings`（用 `sys.modules` 断言）；
6. `pyright --strict` 在 `limbuspy/__init__.py` + `game/facade.py` + `game/value.py` 上 0 error。

#### P1 — 门面补全（7–8 人日）

**范围**：全部动作函数与 `ActionResult`、buff 相关方法（`u.buff_stack` / `battle.buff_stacks`）、
`battle(buffs=True)`、6 个 `on_*` + 声明式过滤（C# filter gate）、`on_damage_taken` 同步泳道、
`lp.dev.cost/explain`、弃用垫片 + `warn_once`、`tools/migrate_api.py`、`docs/02` §0/§2.5 重写。

**验收**：

1. §4.11 六组对照的"新写法"逐条可跑（进 `test_facade_api.py`）；
2. 声明式过滤的 crossing 数为 0（`test_facade_cost.py` 断言：加 `buff="SINKING"` 后
   `counters.op_calls` 不增加）；
3. `on_damage_taken` 在 mock 上返回 200 且**不超预算**（mock 的 `SYNCALL` 超时 20 ms 不触发）；
4. 旧写法（3 个现有示例 mod）在新代码上**行为不变**，只多一条 `DeprecationWarning`
   （= `test_example_mod.py` 的 11 项断言全绿）；
5. `tools/conformance.py` 201 项绿（新增 op 进 `manifest` 后 C# harness 同步更新）。

#### P2 — 打磨（~5 人日）

**范围**：`Literal[...]` 枚举别名（`Source`/`Timing`/`BuffKey`，从 `_gen/enums.py` 生成）、
`display_name`（本地静态表查名字）、`lp.patch(...)`（Harmony 的模块级形态）、
`lp.expose(name, fn)`、`lp.config(...)` 的模块级形态、`lp.dev.lint_raw()`（CI 检查 B 层泄漏）、
`tools/lint_cost.py`、`docs/06-facade-api.md`（教程 + 参考两层）、`buff.icon/type_id`。

**验收**：

1. 拼错 buff 关键字在 **pyright 阶段**就报（`lp.add_buff(u, "SINKNG")` → type error）；
2. `tools/lint_cost.py` 对全部 op 校验"docstring 的 `Cost:` == 实测 crossing"；
3. `tools/lint_raw.py` 在 3 个示例 mod 上报告"raw 用法处数 ≤ 声明数"；
4. `docs/06-facade-api.md` 的 9 篇教程里，出现过的名字总数 ≤ 15；
5. `run_facade_api.py` 的总 crossing 数比 P0 下降 ≥ 30%（批量与过滤生效）。

### 5.6 迁移工具：`tools/migrate_api.py`（P1 交付，**AST 优先**）

**结论：提供，但只做"机械可判定"的改写，其余交给 `DeprecationWarning`。**

| 能自动改写 | 改写规则 |
|---|---|
| `ctx.ops.unit.info(x)` → 直接读属性 | 只有当 op 结果只被读字段时；否则改写为 `lp.unit(x)` |
| `ctx.ops.unit.buff_stacks(x, "K")` → `lp.unit(x).buff_stack("K")` | 语义等价（都是 1 次 `GetActivatedBuffStack`） |
| `ctx.ops.unit.damage(x, n)` → `lp.damage(x, n)` | 返回值从 `UnitInfo` 变 `ActionResult` → **同时改写使用点**：`info.hp` → `r.hp_after` |
| `@on("AttackSucceeded")` + `(self, ev, ctx)` → 模块级 `lp.on_hit(fn)` | 仅当函数不使用 `self` 且类里只有一个方法（"纯函数 mod"） |
| `self.log("...")` → `print("...")` / `lp.log(...)` | 机械 |

| 不能自动改写（**报出来，人改**） | 原因 |
|---|---|
| `gb.current(ctx.session)` 之后对 `unit.hp` 的读 | 语义从"每次现读"变成"快照"，需要人判断是否要 `refresh()` |
| `ctx.state` / `on_save` / `on_restore` | 生命周期与状态语义，机械改会丢数据 |
| 同步钩子里读 buff 的代码 | 要改成 `watch=[...]`，涉及"要读哪几个关键字"的决策 |
| `Mod.patch(...)` | B 层，保留 |

实现：`ast.NodeVisitor` + `ast.fix_missing_locations` + `ast.unparse`（3.9+），**默认 dry-run**，
`--write` 才落盘，落盘前写 `<file>.bak`。不做正则（`docs/02` 的写法千变万化，正则必错）。

---

## 6. 风险与取舍

### 6.1 静态检查器友好 vs 懒加载属性 —— 用"生成真属性"两全

| 风险 | 缓解 |
|---|---|
| `__getattr__` 动态属性 → pyright 看不见 | **一个都不用**：`Unit` 的 16 个属性由 `[[unit_property]]` 契约生成**真 `@property`**，注解与 docstring 都在源码里 |
| 模块级 `lp.xxx` 若用 PEP 562 → 类型丢失 | 只用「函数 + 类」，值挂在对象属性（§4.2.2） |
| `bindings` 懒加载需要 `__getattr__` | 这是**唯一**例外，且只暴露一个名字（`limbuspy.api.bindings`），配手写 `api/__init__.pyi` 声明它的类型 |
| 降级桩与真实现漂移 | 桩由**同一份 spec** 生成（`emit_stub`），并加测试断言"桩的签名 == 真实现的签名"（用 `inspect.signature` 逐条比对） |
| 动态枚举（`source="PASSIVE"`） | P2 生成 `Literal[...]` 别名；过渡期接受 `str` + 运行期校验（`BadArgument`） |

### 6.2 `None` 语义掩盖真实错误

**风险**：`u.hp` 返回 `None`，作者写 `if u.hp and u.hp < 100` 就静默跳过一个其实存在的单位。

**三层缓解**：

1. **文档把三种 `None` 原因写死**（照 FaustLauncher `tutorial/04-unit` 的表，但加第三种）：
   ① 不在战斗里/内核没连 ② 该字段对这个单位不适用（例如杂兵的 `origin_id` 是 0 而不是 None，
   这类"不适用"我们**返回 0 而不是 None**，只有"真的读不到"才 None） ③ 内核版本过旧（`lp.status().problems` 有原因）。
2. **`None` 与 `0`/`False` 永不混淆**：`hp_ratio` 在 `max_hp == 0` 时返回 `None`（不是 `0.0`）；
   布尔字段返回 `bool | None`（不是 `bool`），强制作者写 `is not None`。
3. **`LIMBUSPY_STRICT=1` 时升级为异常**（环境变量已在 `session.py:58` 存在，语义一致）：
   任何"读不到"都抛 `StaleTarget`，配合 P2 的 `lp.dev.strict()` 在开发期打开、发布时关闭。
4. **动作路径不用 `None`**：`ActionResult.ok` 是 `bool`，`reason` 是 `str`——动作失败**永远有理由**。

### 6.3 「属性看起来免费」vs 实际往返 —— 我们把它变成事实，但引入新风险

**原风险**（属性看起来免费，实际是一次往返）在 A1 下**消失**：属性真的 0 往返。
**新风险**是可预测的另一种：**快照会过期**。

| 新风险 | 缓解 |
|---|---|
| 作者以为 `u.hp` 是实时值，其实是一次采集时刻的值 | ① docstring 第一段就写「这是快照」；② `Unit` 的 `__doc__` 与 `repr` 都带时刻感（`<Unit #201 … @round3>`）；③ 提供 `u.refresh()` 并把"要新值"变成显式动作；④ **同一 tick 内读到的值本来就是同一个**（内核主线程单时间片），所以"过期"只在跨事件时发生 |
| 跨事件的旧 `Unit` 被存进 `self.state`（作者最常见错误） | `Unit.__reduce__`/序列化时**不保留**（存 id 就行）；`u.raw` 在下一帧后抛 `StaleTarget`；文档 §"不要做的事"专列一条 |
| 属性有成本的地方（`u.buffs` / `buff_stack`）被当免费 | ① `Cost:` 段 + `cost` 契约字段；② `lp.dev.cost()` 实测；③ `LIMBUSPY_COST_WARN=50` 时一个回调超 50 次 crossing 打警告并**指出热点方法名**；④ §4.5 机制 2 提供 1 次往返的批量写法（作者有便宜的替代品时才会用） |

**README / 教程里必须放的那张表**（照 FaustLauncher `tutorial/05-props` 的成本表，但我们的数字更好看）：

| 想要 | 写法 | 成本 |
|---|---|---|
| 一个单位的一堆基础字段 | `u = lp.unit(201)`，然后 `u.hp / u.hp_ratio / u.speed` | **1**（获取时）+ 0 |
| 全场的基础字段 | `lp.units()` | **1** |
| 全场 + 每人的 buff 列表 | `lp.battle(buffs=True)` | **1** |
| 全场某 buff 的层数 | `lp.battle().buff_stacks("SINKING")` | **1** |
| 单个单位的 buff 列表（未预取） | `u.buffs` | 1 |
| 一次动作 | `lp.damage(u, 18)` | 1（结果自带新 HP） |
| 事件里读 payload 字段 | `ev.damage` / `ev.on.hp` / `ev.battle` | **0** |

### 6.4 与现有测试 / `conformance.py` 的冲突点：**哪些会红、怎么改**

| 会红的东西 | 原因 | 处理 |
|---|---|---|
| `python/tests/test_curated_api.py`（spec ↔ 壳 ↔ 参考实现 ↔ payload 四方比对） | 新增 `kind/cost/batchable/hidden/alias/filters` 字段，生成物形状变化 | 断言改成"**声明的字段**必须出现在四处"，而不是"字段集合完全相等"（放宽到语义层） |
| `python/tests/test_curated_api.py::test_manifest_matches_spec` | `_contract_manifest.json` 多了字段 | 只比 `ops[].name` / `hooks[].timing` 集合 + `version` |
| `python/limbuspy/_mock/ops.py` 的 `dispatch` 未知 op 抛错 | 新 op 需要参考实现 | P0 就补齐 5 个（否则 `test_facade_api.py` 无法跑） |
| `tools/conformance.py` 里 "ops manifest 与 C# 实现一致"（`manifest.available == true`） | `OpsEntry` 自检要求"清单有、实现有"，新 op 未实现 → `Available=false` | **同一 commit 里加 C# 实现**（P0 只加 2 个 op，风险可控）；`Problems` 里给出缺哪个 |
| `tools/conformance.py` 的 `BATCH` 4 项 | 若给 `OPCALL` 开放批量（§5.3 `RequestRouter`） | 新检查项；老 4 项不动 |
| `python/tests/test_example_mod.py`（11 项，跑 `sinking_tide`） | 若 `Mod.ops` 转发实现有偏差 | `Mod.ops` **必须**保持"无缓存 + 读当次真值"；用同一个 `Game` 语义实现，测试不改 |
| `docs/` 里的文字（`02:96`、`05:96-113` 的 op 表、`README:41`） | 13 op → 18 op | 文档同步（P1），且 `README` 表格只写数量与链接 |
| `python -m limbuspy doctor` 的离线检查 | 可能校验 `api/ops.py` 存在 | 加新文件到白名单 |
| `pyright --strict` 新引入（P0 验收 6） | 现有代码大量 `Any` | **只对新增/改写的 3 个文件开 strict**，逐步扩大；不上全仓库闸门 |

**明确不改的**：`docs/01-protocol.md`（协议冻结）、`runtime/*`（除 `RequestRouter` 的 BATCH 扩展）、
`mod/loader.py` 的目录约定、`tools/requirements/limbuspy.json`（B 层成员契约）。

### 6.5 性能与字节量风险

| 风险 | 量化 | 缓解 |
|---|---|---|
| 批级 `battle` 快照让 `EVENTBATCH` 变大 | 约 +1 KB/批（11 单位 × 16 字段），与现有 payload 同量级；一次连击 20 段仍是 1 份 | `include_battle` 按时机开关；`limit` 参数兜底 |
| `Tick()` 每帧走一次单位字典 | O(11) 属性读，主线程 µs 级（现有 `battle.units` 已经在做同样的事） | **只在有事件要发时才走**（`_queue` 非空）；空帧零开销 |
| `battle(buffs=True)` 的返回体积 | 11 单位 × ~9 buff × 6 字段 ≈ 600 行 ≈ 6 KB | 默认关；文档给"只要层数用 `buff_stacks`"的替代 |
| `contextvars` 在每次回调的 set/reset 开销 | CPython 中约 0.2 µs/次 | 可忽略；若帧预算吃紧，改为"每批一次 bind"（语义不变：同批各事件的时间片相同） |

### 6.6 模块级注册的两个真风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| **多 mod 归属** | 模块顶层调 `lp.on_hit(fn)` 时，框架怎么知道是哪个 mod？ | `loader.import_mod` 里 set `CURRENT_MOD`（`ContextVar`）；`lp.on_*` 把它记进待注册表；`import` 结束由 `register_instance` flush。若 `CURRENT_MOD` 为空（例如 REPL 里直接 import），归属到 `"__user__"` 伪 mod 并告警 |
| **热重载泄漏** | 重载时旧的模块级函数必须从注册表消失 | 沿用现有机制：`runtime.reload()` 按代际清空并重建 `Registry`（`runtime.py:296-300` 已有 `__hooks__` 状态的代际处理）；待注册表存在 mod 对象上，随实例一起丢弃 → 天然不泄漏。P0 加 `test_registry_module_level.py` 专门盯这条 |

### 6.7 线程安全

`lp.*` 依赖 `contextvars`，而 `mod/runtime.py` 的派发是**单线程循环**（`run_forever` 里读帧 → 派发）。
子线程（作者自己 spawn 的）**没有绑定上下文** → `lp.units()` 走 `background()` 通道（每次新建往返、
无缓存）。这与 FaustLauncher 的线程模型结论一致（`tutorial/08-lifecycle`：作者的代码不在游戏线程上，
`lp.*` 永不在游戏线程执行，所以**慢回调只会堆积事件、不会卡游戏**）。

文档必须写的一条禁忌（对齐规则 7）：**不要在 handler 里 spawn 线程再调 `lp.*` 读状态** ——
读到的会是"另一个时刻"的值；要后台处理请把 `ev`（纯值）传出去。

---

## 7. 交付物与验收附录

### 7.1 本计划的落地清单（按 PR 拆分）

| PR | 内容 | 依赖 | 规模 |
|---|---|---|---|
| PR-1 | 契约 v2：`api.toml` 新字段 + `gen_contract.py` 扩展 + 4 份生成物重生 | — | ~450 行改 |
| PR-2 | C# 侧 2 个新 op（`battle.snapshot` / `unit.state`）+ `OpsArgs.BattleTable/State` + `OpsEntry` 注册 | PR-1 | ~350 行 |
| PR-3 | Python 门面骨架：`_ctx` / `facade` / `value` / `actions` / `errors` / `_probe` + `__init__` 导出 | PR-1,2 | ~1 200 行 |
| PR-4 | 模块级注册（`_registry` + loader/runtime 接线 + 测试） | PR-3 | ~300 行 |
| PR-5 | 事件与新泳道：`events.py` + filter gate + `on_damage_taken`（`CuratedHooksSync`） | PR-2,3 | ~500 行 |
| PR-6 | 降级 + 弃用 + 迁移工具 + 文档重写（`02` §0/§2.5、`05` 新增 §0、`06` 新建） | PR-3,4,5 | ~600 行 |
| PR-7 | 打磨：`Literal` 枚举、`display_name`、`lp.patch/expose/config`、两个 lint、pyright strict | PR-6 | ~450 行 |

### 7.2 一页验收表（P0 完成即可对外宣称"新的 A 层 API"）

| # | 标准 | 证据 |
|---|---|---|
| V1 | 作者 API 面 ≤ 15 个名字 + 3 个子命名空间，无 `session/bridge/handle/ref/BoundObject/from_wire` | `python -c "import limbuspy as lp; print([n for n in dir(lp) if not n.startswith('_')])"` |
| V2 | 属性 0 往返（除了文档标注的 buff 方法与动作） | `test_facade_cost.py` 逐属性断言 |
| V3 | A 层不可用 / 桥断 / 未连接三种场景都不抛错 | `test_facade_degrade.py` 3×3 矩阵 |
| V4 | §4.11 六组"新写法"可跑 | `test_facade_api.py` |
| V5 | D9 的任务：**8 行、2 个概念、0 往返** | `python/examples/sinking_double/` + `counters.op_calls == 0` |
| V6 | 契约不漂移 | `tools/gen_contract.py --check` |
| V7 | 旧写法全部仍可用（只多弃用警告） | `test_example_mod.py` 11 项 + 3 个示例驱动器 |
| V8 | 静态检查 | `pyright --strict` 对 3 个新文件 0 error |
| V9 | 跨语言一致 | `tools/conformance.py` 201 项 + 新增 BATCH-OPCALL 项 |
| V10 | `import limbuspy` 不再拉起 2 MB bindings | `assert "limbuspy.api.bindings" not in sys.modules` |

### 7.3 未做 / 明确不做

| 不做 | 理由 |
|---|---|
| 把 `props()`（60 字段 / 60 次往返）搬进 A 层 | 那是 B 层能力（`u.raw.scan()`）；A 层的价值是"只暴露能一次拿到的" |
| 让 `Unit` 自动刷新（后台轮询） | 会引入"什么时候读到的"这类不可推理语义；快照 + 显式 `refresh()` 更可预测 |
| `lp.unit` 同时当函数和命名空间（`lp.unit.damage`） | 命名冲突 + 多一个"该用哪个"的决策（§4.2.3 方案 B 被否） |
| 移除 `Mod` 类 | 有状态/配置/持久化的 mod 仍需要它，且它是现有 255 项测试的基础 |
| 移除 `api.bindings` | 它是 B 层的补全工具（5243 成员），只是不再默认加载 |
| 改线协议 | `docs/01-protocol.md` 已冻结；批级 `battle` 只是 `EVENTBATCH` payload 的一个新键（向后兼容：老内核没有这个键 → `ev.battle` 为 `None`，`on_*` 的 wrapper 会退化为"空 Battle"，不抛错） |

