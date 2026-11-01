# 分发与安装

LimbusPy 有**两个构建变体**，布局契约由内核决定（`kernel/LimbusPy.Bridge/Config/BridgeSettings.cs`）：

```
BepInEx/plugins/LimbusPy/
  LimbusPy.Bridge.dll           内核（BepInEx 插件入口）
  LimbusPy.Bridge.Core.dll      协议/句柄/反射/快照（无 Unity）
  LimbusPy.Bridge.Harmony.dll   表驱动补丁
  python/                       PYTHONPATH 根
    python.exe                  仅 bundled 变体（内核优先用它）
    limbuspy/                   我们的包（+ _gen/ 若已生成）
  mods/<name>/__init__.py       用户 mod
  .state/                       热重载状态（首次运行创建）
```

## 变体

| 变体 | 说明 | 谁提供解释器 |
|---|---|---|
| **system** | 体积约 1 MB，依赖 PATH 上的 `python.exe`（或配置 `python.Executable`） | 用户 |
| **bundled** | 额外内嵌一个 CPython embeddable（约 15–40 MB），用户无需装 Python | 我们 |

内核选择解释器的顺序：`config.python.Executable` → `python/python.exe`（bundled）→ PATH。
所以同一个 `LimbusPy.Bridge.dll` 两种布局都能跑，切换只需要换目录形态或改一行配置。

## 打包

```powershell
# 两个变体一起打（system 总是能打；bundled 需要先准备 embeddable）
python tools/pack.py --out dist --variant both --python-embed <解压的 cpython-embeddable 目录>

# 只打 system（CI 里跑）
python -m limbuspy pack --variant system
```

`--python-embed` 缺失时，bundled 布局仍会被**完整铺好**并在 `manifest.json` 里标
`"complete": false` —— 不假装能跑。`manifest.json` 还记录程序集清单、包文件数、是否带索引。

**不打包 `methods.json`**（34 MB 的完整 IL2CPP 索引）：安装包里只带 `enums.py` / `types.pyi` /
`caps.json`。需要索引查询（`limbuspy index`、成员表校验）时在开发机运行 stubgen。

**不打包 `0Harmony.dll` / `MonoMod.*`**：它们必须由 `BepInEx/core` 提供，随包复制会导致
程序集标识冲突（见 `docs/03-architecture.md §8` 的踩坑记录）。packer 会主动删掉误入的副本。

## 验收（不需要游戏）

```powershell
cd dist\system
$env:PYTHONPATH = "$pwd\python"
python -m limbuspy doctor --mods mods
```

`doctor` 从**打包产物**里导入 `limbuspy` 并自检：解释器、stubs（有索引则校验 requirements，
只有 enums 则 warn）、mods 依赖、内核（未连接则 skip）。`result: healthy` 即布局可用。

更严格的自测：`python tools/conformance.py`（协议/反射/补丁/门面/真进程互通，196 项）。

## 升级

1. 覆盖三个 `LimbusPy.Bridge*.dll`；
2. 覆盖 `python/limbuspy/`（保留 `mods/` 与 `.state/`）；
3. 游戏版本更新后重新生成索引（`python -m limbuspy stubs --dump <dump.cs>`）并看 `doctor` 的 caps 报告。

## 工具箱（`python -m limbuspy …`）

| 命令 | 作用 |
|---|---|
| `doctor` | stubs / 解释器 / mods / 运行中内核（含 caps、handles、dispatcher、patches）自检 |
| `repl` | 连运行中的内核交互式试脚本；`-c "…"` / `--exec file` 非交互；`--no-connect` 离线 |
| `stubs` | 从 `dump.cs` 生成 IL2CPP 索引（enums/types.pyi/methods.json/caps.json） |
| `statics` | 从 bundle 抽出静态数据表（需要 UnityPy，仅这一步需要） |
| `pack` | 产出 system / bundled 两个分发布局 |
| `mods` / `index` / `version` | 列 mod 依赖、查索引、看版本 |

REPL 里预置：`bridge`（原始 LPCP）、`session`（反射 get/set/call + snapshot）、`gb`（战斗门面）、
`Modular`（单向兼容层）、`members`（已校验成员表）、`il2cpp`（索引查询）。
