using System;
using System.Collections.Generic;
using LimbusPy.Bridge.Core.Framing;
using LimbusPy.Bridge.Core.Ops;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Reflect;

namespace LimbusPy.Bridge.Core.Bridge
{
    /// <summary>
    /// Py→C# 请求的统一调度器：CALL（含 reflect 伪方法）、OPCALL（A 层 op）、BATCH、SNAPSHOT。
    ///
    /// 抽成 Core 里的独立类，是为了让**内核与测试 harness 跑同一份代码**：
    /// 早先 KernelHost 自己写了 CALL 分支、harness 自己写了另一套，结果 Python 门面用到的
    /// ``reflect.*`` 只在 harness 里存在，一致性测试全绿而真机不可用。
    ///
    /// 引擎相关的部分（Harmony 补丁表、Doctor 报告）仍留在 KernelHost —— 它们本来就只有内核有。
    /// </summary>
    public sealed class RequestRouter
    {
        private readonly ReflectionInvoker _invoker;

        public RequestRouter(ReflectionInvoker invoker)
        {
            _invoker = invoker ?? throw new ArgumentNullException(nameof(invoker));
        }

        /// <summary>A 层 op 提供者（可选；为空时 OPCALL 报 unsupported）。</summary>
        public IOpsProvider Ops { get; set; }

        /// <summary>SN 个字段快照的提供者（KernelHost 注入，便于 Core 不依赖 Unity）。</summary>
        public Func<Dictionary<string, object>> SnapshotProvider { get; set; }

        /// <summary>处理一条请求；不认识的类型返回 null（由调用方继续处理）。</summary>
        public Dictionary<string, object> Dispatch(MsgType type, Dictionary<string, object> request, long id)
        {
            switch (type)
            {
                case MsgType.Call:
                    return Lpcp.OkResult(id, Call(request));

                case MsgType.OpCall:
                    return Lpcp.OkResult(id, OpCall(request));

                case MsgType.Batch:
                    return Lpcp.OkResult(id, Batch(request));

                case MsgType.Snapshot:
                {
                    var provider = SnapshotProvider;
                    if (provider == null) throw LpcpFault.Unsupported("snapshot service is not available");
                    return Lpcp.OkResult(id, provider());
                }

                default:
                    return null;
            }
        }

        // -- 单条 ---------------------------------------------------------------------------

        private object Call(Dictionary<string, object> request)
        {
            var target = request.TryGetValue("target", out var targetValue) ? targetValue : null;
            var method = Lpcp.GetString(request, "method", string.Empty);
            var args = Lpcp.GetArray(request, "args");

            // 先看是不是 reflect 伪方法（Python 门面/绑定层走这条），否则按真实成员调用。
            if (ReflectOps.TryInvoke(_invoker, method, args, out var reflected)) return reflected;
            return _invoker.Invoke(target, method, args);
        }

        private object OpCall(Dictionary<string, object> request)
        {
            var ops = Ops;
            if (ops == null || !ops.Available)
            {
                throw LpcpFault.Unsupported("ops are not available (LimbusPy.Ops not loaded)",
                    Lpcp.Map("hint", "check the BepInEx log for the LimbusPy.Ops load failure"));
            }

            var name = Lpcp.GetString(request, "op", string.Empty);
            if (string.IsNullOrEmpty(name)) throw LpcpFault.Marshal("op name is required");
            var rawArgs = request.TryGetValue("args", out var value) ? value : null;
            var args = rawArgs as Dictionary<string, object> ?? new Dictionary<string, object>(StringComparer.Ordinal);
            return ops.Invoke(name, args);
        }

        // -- 批量 ---------------------------------------------------------------------------

        private object Batch(Dictionary<string, object> request)
        {
            var entries = Lpcp.GetArray(request, "ops");
            var results = new List<object>(entries.Length);
            foreach (var entry in entries)
            {
                results.Add(BatchOne(entry));
            }

            return results.ToArray();
        }

        private Dictionary<string, object> BatchOne(object entry)
        {
            try
            {
                if (!(entry is object[] parts) || parts.Length < 2)
                    throw LpcpFault.Marshal("batch op must be [target, method, args[]]");
                var target = parts[0];
                var method = Convert.ToString(parts[1]);
                var args = parts.Length > 2 ? parts[2] as object[] ?? Array.Empty<object>() : Array.Empty<object>();

                object value;
                if (ReflectOps.TryInvoke(_invoker, method, args, out var reflected)) value = reflected;
                else value = _invoker.Invoke(target, method, args);
                return Lpcp.OkResult(0, value);
            }
            catch (LpcpFault fault)
            {
                return Lpcp.ErrorResult(0, fault.ToError());
            }
            catch (Exception ex)
            {
                return Lpcp.ErrorResult(0, Lpcp.Error(ErrorCode.Internal, ex.GetType().Name + ": " + ex.Message));
            }
        }
    }
}
