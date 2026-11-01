# 写一个 LimbusPy mod

面向 mod 作者。内核/协议细节见 [`01-protocol.md`](01-protocol.md)、[`03-architecture.md`](03-architecture.md)。

---

## 0. 30 秒版

```
BepInEx/plugins/LimbusPy/mods/hello/
├─ __init__.py      ← 唯一必需的入口
└─ mod.toml         ← 可选清单
```

```python
from limbuspy.mod import Mod, hook

class Hello(Mod):
    @hook("OnSucceedAttack")
    def on_hit(self, ctx):
        hp = ctx.get(ctx.target, "Hp")
        ctx.call(ctx.target, "Heal", 1)
        self.log("healed a unit at hp=%s" % hp)
```

存盘即生效（文件监视 + 热重载），不需要重启游戏。

---

## 1. 目录与清单

```
mods/<name>/
├─ __init__.py        必须：作为包导入（支持 `from .helpers import x` 相对导入）
├─ mod.toml           可选：name / version / description / author / requires
├─ helpers.py         随便放
└─ assets/            随便放（注意：不要放进 __pycache__）
```

`mod.toml`：

```toml
name = "sinking_overload"
version = "1.2.0"
description = "Sinking stacks differently"
author = "you"
requires = ["core_lib>=1.0"]
```

> 没有 `tomllib`（Python < 3.11）时用 `mod.json`（同名字段）。

清单里的 `requires` 与类属性 `requires` 会被合并；**依赖按拓扑序加载**，缺失或成环会在日志里报明确错误，不会半加载。

---

## 2. 声明式装饰器

| 装饰器 | 作用 | 谁来调 |
|---|---|---|
| `@hook(timing, ...)` | 绑定一个战斗时机 | 内核 `EVENTBATCH`（批量、异步） |
| `@hook(..., sync=True, fallback=X)` | 需要**立刻**把值返回给游戏的钩子 | 内核 `SYNCALL`（阻塞，见 §5） |
| `@expose("name")` | 暴露成可被内核按名调用 | `SYNCALL` / Modular 的 `py()` |
| `@consequence("name")` | 同时登记到 Modular 的 `consequenceDict` | Modular 指令串 `name(...)` |
| `@acquirer("name")` | 登记到 `acquirerDict` | Modular `VALUE_n(name(...))` |
| `@lua_function("name")` | 登记到 `luaFunctionDict` | 现有 Lua 脚本 |

时机名沿用 Modular 的（`WhenUse` / `OnSucceedAttack` / `ChangeTakeDamage` / `OnGainBuff` …），
所以已有知识直接迁移。

过滤器（等价 Modular 的 `TIMING:OnSucceedAttack(Head)`）：

```python
@hook("OnSucceedAttack", heads=True)     # 只正面
@hook("OnSucceedAttack", crit=True)      # 只暴击
@hook("OnGainBuff", keyword="SINKING")   # 只该关键字
@hook("OnSucceedAttack", priority=10)    # 同时机内的执行顺序（大者先）
```

**继承是被支持的**：基类里声明的 hook/暴露函数会被子类继承（按 MRO 收集，同名覆盖），
所以可以做共享行为基类，几个 mod 复用。

---

## 3. `ctx`：回调里能做什么

| 成员 | 说明 |
|---|---|
| `ctx.target` | 事件的主对象（`{"t":"unit","h":123}` 这样的类型化引用；没有则为 `None`） |
| `ctx.event` | 原始事件 payload（含 `timing`/`gen`/`ctx`/过滤器字段） |
| `ctx.snapshot` | 可选的状态快照（带 `snapshot.v1` 能力时才有） |
| `ctx.call(target, "Method", *args)` | 反射调用（内核主线程执行） |
| `ctx.invoke(target, "Method", [args])` | 同上，参数用 list |
| `ctx.get(target, "Member")` / `ctx.set(target, "Member", v)` | 字段/属性读写（含只读属性） |
| `ctx.static("Ns.Type::Method", *args)` | 静态调用 |
| `ctx.effect(op, **kw)` | **声明式效果**：进 `ACT` 回包（内核在后续里程碑统一应用；在此之前请用 `call`/`set`） |
| `ctx.state` | 本 hook 的私有字典，**跨热重载保留** |
| `ctx.log(msg)` | 同时进 BepInEx 日志与 Python 日志 |

引用与类型：Python 侧永远只持有 `{"t","h"}` 引用；`ctx.get`/`call` 的返回值可能是标量、
`list`、`dict`、`bytes` 或新的引用。**不要自己构造引用**。

大对象不要过桥：`str`/`bytes` 单值上限 64 KiB，超了会报 `marshal` 而不是截断。
资产类需求（贴图/CG/BGM/bundle）根本不该过桥——那一层用纯 Python 的 UnityPy 做。

---

## 4. 生命周期与状态

```python
class MyMod(Mod):
    cfg = config(gain=2, verbose=False)     # 有默认值、会持久化、可选 choices 校验

    def on_load(self):        ...           # 注册完、开始派发前
    def on_unload(self):      ...           # 卸载前（重载/退出），要快
    def on_save(self) -> dict:              # 返回跨重载需要保留的额外状态
        return {"counter": self.counter}
    def on_restore(self, state): ...        # 重载后恢复

    def __init__(self):
        super().__init__()
        self.counter = 0
```

状态保存位置：`LimbusPy/.state/<mod>.json`（`{"config": {...}, "state": {...}}`）。
`on_save()` 返回的、`self.state` 里的、以及每个 hook 的 `ctx.state` 都会一起持久化。

---

## 5. 同步钩子（少量、谨慎）

```python
@hook("ChangeTakeDamage", sync=True, fallback=100)
def damage_multiplier(self, ctx):
    return 150        # 100 = 原样
```

同步钩子会**阻塞游戏主线程**等 Python 回包（默认 20 ms 上限，可配）。Python 侧如果超时没答，
内核用 `fallback` 继续跑——游戏不会卡住，但伤害会是 fallback 的值。

规则：
1. 能用副作用（`ctx.call`/`ctx.set`）表达的，就不要用同步钩子；
2. 同步钩子里**不要**再触发需要等待的深层调用；
3. 一个同步钩子只做一件事，别在里面做 IO 或遍历全战场。

---

## 6. 热重载

- 改 `mods/` 下任何 `.py`/`.toml`/`.json` → 0.4 s 内自动重载（`--watch-interval` 可调）。
- 重载流程：`on_save` → `on_unload` → 清注册表 → 重新 import（**清 `__pycache__`**，避免同秒同尺寸
  的 stale bytecode）→ 重新注册 → `on_restore`。
- 重载是**按代际（generation）**的：旧代码的迟到回包不会被新代码执行。
- 一个 mod 写错语法不会拖垮别人：它进 `failed`，其它 mod 继续跑；`stats()` 里能看到原因。
- 战斗中重载是支持的；但**引用（句柄）不跨重载**，重载后要重新获取。

---

## 6.2 读战场状态（`limbuspy.game`）

```python
from limbuspy.game import battle as gb

def on_hit(self, ctx):
    b = gb.current(ctx.session)     # 每次回调一个新的 Session；一次往返拿到全场（快照）
    for unit in b.enemy_units:
        if unit.hp_ratio < 0.3:
            sinking = unit.buff("SINKING")     # 按关键字取 buff
            stack = sinking.stack if sinking else 0
            self.log("%s hp=%s/%s sinking=%s" % (unit.instance_id, unit.hp, unit.max_hp, stack))
```

要点：
- `b.player_units` / `b.enemy_units` / `b.alive_units` / `b.unit(instance_id)` 来自**快照**，`unit.hp`、`unit.max_hp`、`unit.instance_id` 这些**不再往返**。
- `unit.buffs`、`skill.coins` 这类需要调游戏的成员才往返，且按会话缓存（同一个 hook 内只查一次）。
- 所有成员名都在 `limbuspy/game/members.py` 里，且被 `tools/requirements/limbuspy.json` 对着真实 dump 校验；写错名字 `doctor` 会报，`LIMBUSPY_STRICT=1` 时直接抛错。
- 想读没包装的东西？`unit.get("某成员")` / `unit.call("某方法", 1, 2)` / `unit.set(...)` 就是逃生口。

## 6.3 读静态数据表（`limbuspy.game.statics`）

游戏里 2 185 张数据表（技能/人格/被动/Buff/遭遇…）是 TextAsset JSON。分成两步：

```powershell
# 1) 只做一次：从 bundle 里抽成普通目录（这一步需要 UnityPy + 游戏本体）
python -m limbuspy statics --catalog "<catalog.bin>" --out extracted/statics
```

```python
# 2) 脚本/工具里纯 Python 读，不依赖 UnityPy
from limbuspy.game import statics

db = statics.open_tables("extracted/statics")
db.tables                                   # ["BuffData", "PassiveData", "SkillData", ...]
row = db.get("SkillData", 601101)           # 单条记录
tier3 = db.rows("SkillData", tier=3)        # 字段过滤（等值或多值）

patch = db.patch("SkillData", 601101, {"tier": 5})     # 记录一条覆盖
patch.save("mods/my_mod/patches.json")                 # 随 mod 发布
```

`Patch` 可以 `apply_to(db)` 在内存里立刻验证，也可以 `save`/`load` 与 mod 一起分发。

## 6.5 Harmony 补丁（原生钩子的最后一公里）

时机系统覆盖不到的方法，可以直接打补丁：

```python
from limbuspy.mod import Mod, expose

class HealTweak(Mod):
    def on_load(self):
        # 参数类型用于重载消歧；名字不唯一时内核拒绝猜，而不是打错方法
        self.patch("BattleUnitModel", "Heal", "on_heal", kind="postfix", sig=["int"])

    @expose("on_heal")
    def on_heal(self, ctx):
        return (ctx.event.get("result") or 0) + 5     # 返回值 +5
```

- `kind="postfix"` 改写返回值；`kind="prefix"` 可跳过原方法（`return {"skip": True}`）或替换入参（`return {"args": [...]}`）。
- **fail-open**：钩子抛错/超时 → 游戏按原逻辑走，只记一次失败。不卡帧、不崩。
- **热重载安全**：`on_load` 里装的补丁在重载前会被 `unpatch_all()` 自动摘掉，旧代码不会在重载后被调用（`self._patches` 由基类维护）。
- 能写回的类型：`void/bool/int/long/float/double/string/引用类型`；枚举/结构体只能观测。
- 优先用 `@hook`（时机系统，批量异步）；补丁是同步阻塞的，只用于时机系统没有的缝。

## 6.6 资产替换（`limbuspy.game.assets`）

常规模组的主力：贴图/立绘/CG/BGM/文本替换。**完全不过桥**（游戏从磁盘加载 bundle），所以内核忙或游戏没开都能做。

```python
from limbuspy.game import assets

bundle = assets.open("<某个>.bundle")
bundle.objects                      # {"TextAsset": 1, "Texture2D": 4, "Sprite": 4, ...}
text = bundle.get("TextAsset", "SkillData")
raw = bundle.export(text)           # bytes

patch = assets.Patch().replace("TextAsset", "SkillData", new_bytes)
patch.apply(bundle)                 # {"applied": 1, "missing": 0}
bundle.save("mods/my_mod/data.bundle")   # 游戏可直接加载的 bundle

# 图片替换（需要 Pillow）
assets.Patch().replace_image("Texture2D", "DUI_mode_2", "new.png").apply(bundle)
```

- UnityPy 是**可选依赖**：`import limbuspy.game.assets` 不花钱，真正用的时候才需要 `pip install UnityPy`，
  缺了会给出可执行的提示而不是 traceback。
- 自动设置 `UnityPy.config.FALLBACK_UNITY_VERSION = "6000.3.12f1"`（Limbus 的 Unity 6 构建）。
- 找 bundle 用 catalog：`assets.catalog_lookup(catalog_path, r"dui_.*_assets_all_")`
  （底层就是 `tools/parse_catalog.py`，**不要**去 grep catalog 二进制）。
- 改完先 `reopen` 验证一遍再发布：`assets.open(saved).export(assets.open(saved).get(...)) == new_bytes`。

## 7. 调试

```powershell
# 连到正在运行的内核（不 spawn），看握手与日志
python -m limbuspy.bootstrap --pipe limbuspy.<pid> --once

# mod 侧统计（hooks/失败数/跳过/超预算/加载错误）
#   在内核日志里搜 "limbuspy 0.1.0 ready"，或用 STATS 消息
```

常见错误对照：

| 现象 | 原因 |
|---|---|
| `no exposed function 'x'` | `@expose` 名字写错，或该 mod 加载失败（看 `load_errors`） |
| `stale_handle` | 引用了已释放/已回收的对象；arena 作用域外不要存引用 |
| `marshal: ... max 65535` | 想把 >64 KiB 的 str/bytes 过桥 |
| `frame budget exceeded` | 一个 batch 里 hook 太慢；拆分或改用副作用 |
| 改了代码没生效 | 看是不是写到了 `__pycache__` 里，或者文件不在 `mods/` 下 |

---

## 8. 与 Modular 的关系（**单向**）

按设计裁决：**LimbusPy 兼容 Modular，Modular 不需要知道 LimbusPy 存在**。所以这里只有"用 Modular"这一个方向。

```python
from limbuspy.modular.dsl import script

def on_hit(self, ctx):
    m = ctx.modular                      # 或 Modular(ctx.session)
    if not m.available:
        return                           # 没装 Modular：原生 hook 照常工作

    # 1) 直接跑一段 Modular 指令串（等价于脚本名里的 Modular/...）
    m.run("TIMING:OnSucceedAttack(Head)/buff(MainTarget,SINKING,2,1)", unit=ctx.target)

    # 2) 只调一个 consequence / acquirer
    m.consequence("healhp", "MainTarget", 25, unit=ctx.target)
    stacks = m.acquire("getbuffcount", "Self", "SINKING")

    # 3) 用 builder 拼，避免手写分隔符
    text = script().timing("WhenUse", crit=True).value(0, "getdmg").buff("MainTarget", "CHARGE", 1, 1).render()
    m.run(text, unit=ctx.target)
```

实现方式：全程走反射调用 Modular 自己的对象（`MainClass.timingDict/consequenceDict/acquirerDict`、
`ModularSA.SetupModular/Enact`、`IModularAcquirer.ExecuteAcquirer`），**没有编译期引用**——Modular 升级
不会让内核的 dll 加载失败。查不到就 `available == False`，mod 优雅降级。

**性能取向不变**：原生 `@hook` 是批量异步的；Modular 指令串是同步解析 + 派发，属于兼容层与逃生口，
不要放进每回合高频路径。
