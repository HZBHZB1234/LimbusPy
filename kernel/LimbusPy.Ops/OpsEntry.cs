using System;
using System.Collections.Generic;
using LimbusPy.Bridge.Core.Ops;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Reflect;

namespace LimbusPy.Ops
{
    /// <summary>
    /// A 层（封装 API）的入口：注册表 + 清单自检 + 时机安装。
    ///
    /// 由内核用 ``Assembly.Load`` 动态加载后实例化（见 kernel/LimbusPy.Bridge/Runtime/OpsLoader.cs），
    /// 因此这个程序集加载失败**只影响 A 层**：内核照常跑，Python 侧降级到 B 层反射。
    /// </summary>
    public sealed class OpsEntry : IOpsProvider
    {
        /// <summary>句柄表（单位引用解析用）；由 Attach 注入。</summary>
        internal static IRefStore Store;

        private readonly Dictionary<string, Func<Dictionary<string, object>, object>> _handlers;

        private readonly UnitOps _units = new UnitOps();
        private readonly BattleOps _battle = new BattleOps();
        private readonly RngOps _rng = new RngOps();
        private readonly ContentOps _content = new ContentOps();
        private readonly HookTable _hooks = new HookTable("LimbusPy.Curated");
        private readonly List<string> _problems = new List<string>();

        public OpsEntry()
        {
            Name = "limbuspy.ops";

            // 注册表：op 名 → 原生实现。**没有反射**，只有一次字典查找。
            // 表本身是生成物（kernel/LimbusPy.Ops/OpsRegistry.g.cs）：契约里的 class/method
            // 已经足够推出接线，所以「新增一个 op」不再需要改这里。
            // 用具名参数：生成物的形参顺序跟着契约里 op 的出现顺序走，具名传参才不会错位。
            _handlers = OpsRegistry.Build(unitOps: _units, battleOps: _battle,
                rngOps: _rng, contentOps: _content);

            // 契约 ↔ 实现自检：清单里有、实现里没有 → 开发期就报出来（而不是真机调用时）。
            foreach (var spec in OpsManifest.Ops)
            {
                if (!_handlers.ContainsKey(spec.Name))
                {
                    _problems.Add("missing implementation: " + spec.Name);
                }
            }

            // 时机安装失败（游戏大更改了签名）不致命：只是这些时机不触发，记进 problems。
            foreach (var failure in _hooks.Install())
            {
                _problems.Add("hook: " + failure);
            }

            Available = _problems.Count == 0;
        }

        public string Name { get; }

        /// <summary>清单与实现、时机挂点全部对得上才算可用。</summary>
        public bool Available { get; private set; }

        /// <summary>自检发现的问题（DOOR/日志用）。</summary>
        public IReadOnlyList<string> Problems => _problems;

        // -- IOpsProvider -------------------------------------------------------------------

        public Dictionary<string, object> Manifest()
        {
            var ops = new List<object>(OpsManifest.Ops.Length);
            foreach (var spec in OpsManifest.Ops)
            {
                var wired = spec.ToWire();
                wired["available"] = _handlers.ContainsKey(spec.Name);
                ops.Add(wired);
            }

            var hooks = new List<object>(OpsManifest.Hooks.Length);
            foreach (var spec in OpsManifest.Hooks)
            {
                var wired = spec.ToWire();
                wired["installed"] = _hooks.Installed.Contains(spec.Timing);
                hooks.Add(wired);
            }

            return Lpcp.Map(
                "provider", Name,
                "version", OpsManifest.Version,
                "available", Available,
                "problems", _problems.ToArray(),
                "ops", ops.ToArray(),
                "hooks", hooks.ToArray());
        }

        public object Invoke(string op, Dictionary<string, object> args)
        {
            if (!_handlers.TryGetValue(op ?? string.Empty, out var handler))
            {
                var known = new List<object>();
                foreach (var name in _handlers.Keys)
                {
                    known.Add(name);
                    if (known.Count >= 24) break;
                }

                throw LpcpFault.Unsupported("unknown op '" + op + "'", Lpcp.Map("known", known.ToArray()));
            }

            return handler(args ?? new Dictionary<string, object>(StringComparer.Ordinal));
        }

        public void RegisterGlobals(GlobalRegistry globals)
        {
            if (globals == null) return;
            try
            {
                // 内核此前没有注册任何单例 —— 于是 ``global:BattleObjectManager`` 与 SNAPSHOT
                // 在真机上其实是死路。A 层顺手把它接上（SNAPSHOT 也依赖它）。
                globals.Register("BattleObjectManager", () => OpsArgs.Manager);
            }
            catch (Exception ex)
            {
                _problems.Add("register globals: " + ex.Message);
            }
        }

        public void Attach(IOpsHost host, IRefStore store)
        {
            Store = store;
            _hooks.Attach(host);
        }

        public void Tick() => _hooks.Tick();

        /// <summary>插件卸载时摘掉 Harmony 补丁。</summary>
        public void Shutdown()
        {
            _hooks.Uninstall();
            MaskPlan.Clear();
        }
    }
}
