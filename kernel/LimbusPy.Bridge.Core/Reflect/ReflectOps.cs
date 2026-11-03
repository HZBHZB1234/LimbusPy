using System;
using System.Collections.Generic;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Bridge.Core.Reflect
{
    /// <summary>
    /// ``reflect.get/set/invoke/types/describe`` 这组**伪方法**的唯一实现。
    ///
    /// 为什么单独抽出来：Python 侧的整个门面（以及老版本 limbuspy.game）都是用这几个名字发
    /// CALL 的，而早先只有测试 harness 实现了它们 —— 真内核没有这条路由，于是"一致性测试
    /// 全绿、真机全坏"。现在内核与 harness 都走这里（见 tools/conformance.py 的 router 检查）。
    /// </summary>
    public static class ReflectOps
    {
        public const string Prefix = "reflect.";

        /// <summary>是不是一个 reflect 伪方法。</summary>
        public static bool Handles(string method)
            => !string.IsNullOrEmpty(method) && method.StartsWith(Prefix, StringComparison.Ordinal);

        /// <summary>是不是标准 5 个之一（harness 还有 reflect.new_unit 之类的辅助方法）。</summary>
        public static bool IsStandard(string method)
            => method == "reflect.get" || method == "reflect.set" || method == "reflect.invoke"
               || method == "reflect.types" || method == "reflect.describe";

        /// <summary>执行一个标准 reflect 伪方法（harness 与内核共用这一条路径）。</summary>
        public static object Call(ReflectionInvoker invoker, string method, object[] args)
        {
            if (TryInvoke(invoker, method, args, out var result)) return result;
            throw LpcpFault.NotFound("not a reflect op: '" + method + "'");
        }

        /// <summary>执行一个 reflect 伪方法；不是伪方法返回 false。</summary>
        public static bool TryInvoke(ReflectionInvoker invoker, string method, object[] args, out object result)
        {
            result = null;
            if (invoker == null || !Handles(method)) return false;
            args = args ?? Array.Empty<object>();

            switch (method)
            {
                case "reflect.get":
                    Require(args, 2, method);
                    result = invoker.GetMember(args[0], AsString(args[1], "member"));
                    return true;

                case "reflect.set":
                    Require(args, 3, method);
                    result = invoker.SetMember(args[0], AsString(args[1], "member"), args[2]);
                    return true;

                case "reflect.invoke":
                    // 参数数组可省略（无参方法）；早期 harness 允许这种写法，保持一致。
                    Require(args, 2, method);
                    result = invoker.Invoke(args[0], AsString(args[1], "method"),
                        args.Length > 2 ? AsArray(args[2]) : Array.Empty<object>());
                    return true;

                case "reflect.types":
                    result = invoker.Types(args.Length > 0 ? AsString(args[0], "filter") : null,
                        args.Length > 1 ? Convert.ToInt32(args[1]) : 200);
                    return true;

                case "reflect.describe":
                    Require(args, 1, method);
                    result = invoker.Describe(args[0], args.Length > 1 ? Convert.ToInt32(args[1]) : 200);
                    return true;

                default:
                    throw LpcpFault.NotFound("unknown reflect op '" + method + "'",
                        Lpcp.Map("known", new object[]
                        {
                            "reflect.get", "reflect.set", "reflect.invoke", "reflect.types", "reflect.describe",
                        }));
            }
        }

        private static void Require(object[] args, int count, string method)
        {
            if (args.Length < count)
            {
                throw LpcpFault.Marshal(method + " needs " + count + " argument(s), got " + args.Length);
            }
        }

        private static string AsString(object value, string what)
        {
            if (value is string text) return text;
            if (value == null) throw LpcpFault.Marshal(what + " is required");
            return Convert.ToString(value);
        }

        private static object[] AsArray(object value)
        {
            switch (value)
            {
                case null: return Array.Empty<object>();
                case object[] array: return array;
                case IList<object> list: return new List<object>(list).ToArray();
                default: throw LpcpFault.Marshal("invoke arguments must be an array");
            }
        }
    }
}
