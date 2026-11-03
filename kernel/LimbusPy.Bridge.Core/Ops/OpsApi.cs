using System;
using System.Collections.Generic;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Reflect;

namespace LimbusPy.Bridge.Core.Ops
{
    /// <summary>
    /// A 层（封装 API）在内核侧的提供者契约。
    ///
    /// 实现放在**独立程序集**（``LimbusPy.Ops``）里，由插件用 ``Assembly.Load`` 动态加载：
    /// 那个程序集编译期引用游戏 interop 程序集，游戏更新可能导致它加载失败 —— 这时内核必须
    /// 照常工作（B 层兜底），只是不广告 ``ops.v1``。这就是"封装层可独立失效"。
    /// </summary>
    public interface IOpsProvider
    {
        /// <summary>实现名（日志/诊断用）。</summary>
        string Name { get; }

        /// <summary>是否可用（加载后自检通过）。</summary>
        bool Available { get; }

        /// <summary>契约清单（HELLO/DOCTOR 里原样回给 Python）。</summary>
        Dictionary<string, object> Manifest();

        /// <summary>执行一个 op。未实现 / 参数不合法时抛 <see cref="LpcpFault"/>。</summary>
        object Invoke(string op, Dictionary<string, object> args);

        /// <summary>把游戏单例登记到内核的全局表（``global:`` 目标与 SNAPSHOT 依赖它）。</summary>
        void RegisterGlobals(GlobalRegistry globals);

        /// <summary>接上内核：序列化、事件推送、句柄解析。</summary>
        void Attach(IOpsHost host, IRefStore store);

        /// <summary>帧末：把本帧攒下的时机事件推给 Python（EVENTBATCH）。</summary>
        void Tick();

        /// <summary>插件卸载：摘掉 Harmony 补丁。</summary>
        void Shutdown();
    }

    /// <summary>内核提供给 Ops 的宿主能力（序列化、事件推送、主线程）。</summary>
    public interface IOpsHost
    {
        /// <summary>把一个游戏对象/值转成 wire 形式（引用 → ``{"t","h"}``）。</summary>
        object Wire(object value);

        /// <summary>把一个时机事件排进 EVENTBATCH（帧末一次发）。</summary>
        void EmitEvent(Dictionary<string, object> evt);

        /// <summary>
        /// 同步问 Python 要一个值。**只在必须改游戏返回值的时点用**（伤害倍率、威力修正、
        /// 免死判定……），普通观测时点一律走 <see cref="EmitEvent"/> 的异步批。
        ///
        /// 实现约定（与 B 层补丁派发同款）：运行在游戏主线程；带 deadline；拒绝重入；
        /// 任何失败/超时都返回 null 让调用方 fail-open —— 改值也不能让游戏卡死。
        /// </summary>
        /// <param name="fn">Python 侧注册的函数名。</param>
        /// <param name="context">给 Python 的上下文（已 wire 化）。</param>
        object CallPython(string fn, Dictionary<string, object> context);
    }

    /// <summary>一个 op 的契约描述（由 ``tools/gen_contract.py`` 生成的清单构造）。</summary>
    public sealed class OpSpec
    {
        public OpSpec(string name, string group, string stability, string[] argNames,
            string[] argTypes, string returns,
            IReadOnlyDictionary<string, object> extra = null)
        {
            Name = name ?? throw new ArgumentNullException(nameof(name));
            Group = group ?? string.Empty;
            Stability = stability ?? "stable";
            ArgNames = argNames ?? Array.Empty<string>();
            ArgTypes = argTypes ?? Array.Empty<string>();
            Returns = returns ?? "void";
            Extra = extra;
        }

        public string Name { get; }
        public string Group { get; }
        public string Stability { get; }
        public string[] ArgNames { get; }
        public string[] ArgTypes { get; }
        public string Returns { get; }

        /// <summary>
        /// 扩展元数据袋（cost / batchable / group / hidden / alias ……）。
        ///
        /// 之所以是一个字典而不是一个个具名字段：加字段会逼内核与 <c>LimbusPy.Ops</c> 同步重编译，
        /// 而那个同步正是"A 层可独立失效"要避免的。留一个袋子，之后所有元数据扩展都是纯加法。
        /// </summary>
        public IReadOnlyDictionary<string, object> Extra { get; }

        public Dictionary<string, object> ToWire()
        {
            var wire = Lpcp.Map(
                "name", Name,
                "class", Group,
                "stability", Stability,
                "args", ArgNames,
                "arg_types", ArgTypes,
                "returns", Returns);
            if (Extra != null)
            {
                foreach (var pair in Extra)
                {
                    if (!wire.ContainsKey(pair.Key)) wire[pair.Key] = pair.Value;
                }
            }

            return wire;
        }
    }

    /// <summary>一个时机的契约描述：挂点 + payload 字段（Python 侧 dataclass 与它对齐）。</summary>
    public sealed class HookSpec
    {
        public HookSpec(string timing, string target, string method, string kind,
            string[] payloadNames, string[] payloadTypes,
            IReadOnlyDictionary<string, object> extra = null)
        {
            Timing = timing ?? throw new ArgumentNullException(nameof(timing));
            Target = target ?? string.Empty;
            Method = method ?? string.Empty;
            Kind = kind ?? "postfix";
            PayloadNames = payloadNames ?? Array.Empty<string>();
            PayloadTypes = payloadTypes ?? Array.Empty<string>();
            Extra = extra;
        }

        public string Timing { get; }
        public string Target { get; }
        public string Method { get; }

        /// <summary>
        /// <c>postfix</c>：只观测（异步批，零阻塞）。
        /// <c>postfix_result</c>：在方法返回后改它的返回值（伤害倍率、威力修正一类；同步）。
        /// <c>prefix</c>：在方法执行前改它的入参（同步）。
        /// </summary>
        public string Kind { get; }

        public string[] PayloadNames { get; }
        public string[] PayloadTypes { get; }

        /// <summary>扩展元数据袋（同上；lane / watch / mutate ……）。</summary>
        public IReadOnlyDictionary<string, object> Extra { get; }

        /// <summary>是否需要同步问 Python（改值型时点）。</summary>
        public bool IsMutating => Kind == "prefix" || Kind == "postfix_result";

        public Dictionary<string, object> ToWire()
        {
            var wire = Lpcp.Map(
                "timing", Timing,
                "target", Target,
                "method", Method,
                "kind", Kind,
                "payload", PayloadNames,
                "payload_types", PayloadTypes);
            if (Extra != null)
            {
                foreach (var pair in Extra)
                {
                    if (!wire.ContainsKey(pair.Key)) wire[pair.Key] = pair.Value;
                }
            }

            return wire;
        }
    }
}
