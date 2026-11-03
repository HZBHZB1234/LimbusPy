using System;
using System.Collections.Generic;
using System.Reflection;
using LimbusPy.Bridge.Core.Ops;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Ops
{
    /// <summary>
    /// 把一次补丁回调的原始参数，按契约声明装成一个扁平 payload。
    ///
    /// 这个类**不随覆盖范围增长**：时点从 5 个涨到 76 个，这里一行都不用改 —— 因为
    /// "哪个字段来自哪个参数"是 <c>HookPayloadMap.g.cs</c>（生成物）里的数据，不是代码。
    ///
    /// 设计要点：
    /// <list type="bullet">
    /// <item>按**参数名**解析下标，不按位置。参数名能在安装期校验，位置不能。</item>
    /// <item>解析同时消解重载歧义：<c>AccessTools.Method(type, name)</c> 不传类型时会选错，
    ///       而 <c>dump.cs</c> 里 <c>OnSucceedAttack</c> 有四种签名共存。</item>
    /// <item>任何一次取值失败都只让那一个字段变成空值，绝不抛出 —— 观测钩子不能影响游戏。</item>
    /// </list>
    /// </summary>
    internal sealed class PayloadShaper
    {
        private readonly IOpsHost _host;
        private readonly Dictionary<string, HookFieldBinding[]> _bound =
            new Dictionary<string, HookFieldBinding[]>(StringComparer.Ordinal);

        /// <summary>
        /// 时点 → 「受击者」在目标方法参数里的下标。
        ///
        /// 位图（<c>mask_victim</c>）要按受击者算，而装配 payload 时只拿到 args 数组，
        /// 所以安装期就要把这个下标记下来（约定：payload 里叫 <c>target</c> 的 unit 字段）。
        /// </summary>
        private readonly Dictionary<string, int> _victimIndex =
            new Dictionary<string, int>(StringComparer.Ordinal);

        internal PayloadShaper(IOpsHost host)
        {
            _host = host;
        }

        /// <summary>已绑定的时点（诊断用）。</summary>
        internal IEnumerable<string> Bound => _bound.Keys;

        /// <summary>
        /// 安装期：把契约里的参数名解析成下标，并校验签名有没有漂移。
        ///
        /// 返回问题列表（空 = 一切正常）。问题不会静默：<see cref="OpsEntry"/> 会把它们并进
        /// <c>problems</c>，`/doctor` 和启动日志都能看到。
        /// </summary>
        internal List<string> Bind(string timing, MethodInfo method)
        {
            var problems = new List<string>();
            if (method == null)
            {
                problems.Add(timing + ": method is null");
                return problems;
            }

            var parameters = method.GetParameters();
            var declared = HookPayloadMap.Params.TryGetValue(timing, out var names) ? names : null;

            // ① 签名漂移：契约声明的参数名表 vs 真实方法。名字对不上说明游戏改过签名，
            //    继续用位置下标会静默取到错位的值（例如 bool 变成 int 的 0/1）。
            if (declared != null)
            {
                if (declared.Length != parameters.Length)
                {
                    problems.Add(string.Format(
                        "{0}: declared {1} params but {2}.{3} has {4}",
                        timing, declared.Length, method.DeclaringType?.Name, method.Name, parameters.Length));
                }
                else
                {
                    for (var i = 0; i < declared.Length; i++)
                    {
                        if (!string.Equals(declared[i], parameters[i].Name, StringComparison.Ordinal))
                        {
                            problems.Add(string.Format(
                                "{0}: param #{1} is '{2}' but contract says '{3}'",
                                timing, i, parameters[i].Name, declared[i]));
                        }
                    }
                }
            }

            var map = new Dictionary<string, int>(StringComparer.Ordinal);
            for (var i = 0; i < parameters.Length; i++)
            {
                var name = parameters[i].Name;
                if (!string.IsNullOrEmpty(name) && !map.ContainsKey(name)) map[name] = i;
            }

            // ② 字段绑定：每个 payload 字段按参数名找下标。
            if (!HookPayloadMap.Fields.TryGetValue(timing, out var bindings))
            {
                problems.Add(timing + ": no payload bindings in HookPayloadMap");
                return problems;
            }

            var resolved = new HookFieldBinding[bindings.Length];
            for (var i = 0; i < bindings.Length; i++)
            {
                var source = bindings[i];
                var clone = new HookFieldBinding
                {
                    Name = source.Name,
                    Type = source.Type,
                    Source = source.Source,
                    ArgName = source.ArgName,
                    MemberPath = source.MemberPath,
                    Literal = source.Literal,
                    ArgIndex = -1,
                };

                if (source.Source == HookFieldSource.Argument || source.Source == HookFieldSource.Member)
                {
                    if (string.IsNullOrEmpty(source.ArgName))
                    {
                        problems.Add(timing + "." + source.Name + ": empty arg name");
                    }
                    else if (!map.TryGetValue(source.ArgName, out var index))
                    {
                        problems.Add(string.Format(
                            "{0}.{1}: param '{2}' not found on {3}.{4} (have: {5})",
                            timing, source.Name, source.ArgName,
                            method.DeclaringType?.Name, method.Name, string.Join(", ", Names(parameters))));
                    }
                    else
                    {
                        clone.ArgIndex = index;
                    }
                }
                else if (source.Source == HookFieldSource.Return && method.ReturnType == typeof(void))
                {
                    problems.Add(timing + "." + source.Name + ": 'return' used on a void method");
                }

                resolved[i] = clone;

                if (clone.Source == HookFieldSource.Argument && clone.Type == "unit" &&
                    string.Equals(clone.Name, "target", StringComparison.Ordinal) && clone.ArgIndex >= 0)
                {
                    _victimIndex[timing] = clone.ArgIndex;
                }
            }

            _bound[timing] = resolved;
            return problems;
        }

        /// <summary>
        /// 运行期：装一个 payload。失败只影响单个字段，永不抛。
        /// </summary>
        /// <param name="returnValue">prefix_result 钩子的返回值（其余为 null）。</param>
        internal Dictionary<string, object> Shape(string timing, object instance, object[] args, object returnValue)
        {
            if (!_bound.TryGetValue(timing, out var bindings)) return null;

            var payload = new Dictionary<string, object>(bindings.Length + 2, StringComparer.Ordinal);
            for (var i = 0; i < bindings.Length; i++)
            {
                var binding = bindings[i];
                payload[binding.Name] = Take(binding, instance, args, returnValue);
            }

            // 零往返谓词的落地：把"这个 mod 关心的 buff 在不在"预计算成整数位图。
            // 没有计划就一个字节都不多花 —— 未注册内容的模组不付这个成本。
            if (MaskPlan.TryGet(timing, out var victimBits, out var selfBits))
            {
                var victim = _victimIndex.TryGetValue(timing, out var index) ? At(args, index) : null;
                payload["mask_victim"] = MaskPlan.Mask(victim as BattleUnitModel, victimBits);
                payload["mask_self"] = MaskPlan.Mask(instance as BattleUnitModel, selfBits);
            }

            return payload;
        }

        private object Take(HookFieldBinding binding, object instance, object[] args, object returnValue)
        {
            try
            {
                switch (binding.Source)
                {
                    case HookFieldSource.Self:
                        return Wire(instance);

                    case HookFieldSource.Return:
                        return ConvertValue(returnValue, binding.Type);

                    case HookFieldSource.Argument:
                        return ConvertValue(At(args, binding.ArgIndex), binding.Type);

                    case HookFieldSource.Member:
                        return ConvertValue(Member(At(args, binding.ArgIndex), binding.MemberPath), binding.Type);

                    case HookFieldSource.Constant:
                        return ConvertValue(binding.Literal, binding.Type);

                    case HookFieldSource.Static:
                        return ConvertValue(CallStatic(binding.Literal), binding.Type);

                    default:
                        return null;
                }
            }
            catch
            {
                return null;
            }
        }

        private object Wire(object value)
        {
            if (value == null) return null;
            try
            {
                return _host?.Wire(value);
            }
            catch
            {
                return null;
            }
        }

        private static object At(object[] args, int index)
            => args != null && index >= 0 && index < args.Length ? args[index] : null;

        /// <summary>"loseBuffInfo.keyword" 这种成员链：逐段按属性/字段取。</summary>
        private static object Member(object holder, string path)
        {
            if (holder == null || string.IsNullOrEmpty(path)) return null;
            var current = holder;
            foreach (var segment in path.Split('.'))
            {
                if (current == null) return null;
                var type = current.GetType();
                var property = type.GetProperty(segment,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (property != null)
                {
                    current = property.GetValue(current);
                    continue;
                }

                var field = type.GetField(segment,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (field == null) return null;
                current = field.GetValue(current);
            }

            return current;
        }

        private static object CallStatic(string target)
        {
            if (string.IsNullOrEmpty(target)) return null;
            var split = target.LastIndexOf('.');
            if (split <= 0) return null;
            var type = ResolveType(target.Substring(0, split));
            var method = type?.GetMethod(target.Substring(split + 1),
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            return method?.Invoke(null, null);
        }

        /// <summary>
        /// 按契约声明的类型做一次宽容转换：失败返回该类型的空值，不抛。
        ///
        /// ``unit`` 走 <see cref="IOpsHost.Wire"/>（引用 → ``{"t","h"}``），其余走 BCL 转换。
        /// </summary>
        internal object ConvertValue(object value, string type)
        {
            switch (type)
            {
                case "unit":
                    return Wire(value);

                case "int":
                    try { return value == null ? 0L : Convert.ToInt64(value); }
                    catch { return 0L; }

                case "float":
                    try { return value == null ? 0d : Convert.ToDouble(value); }
                    catch { return 0d; }

                case "bool":
                    try { return value != null && Convert.ToBoolean(value); }
                    catch { return false; }

                default:
                    if (value == null) return string.Empty;
                    try { return value.ToString() ?? string.Empty; }
                    catch { return string.Empty; }
            }
        }

        private static string[] Names(ParameterInfo[] parameters)
        {
            var names = new string[parameters.Length];
            for (var i = 0; i < parameters.Length; i++) names[i] = parameters[i].Name;
            return names;
        }

        /// <summary>按名字找类型（扫已加载程序集；游戏类型没有稳定的程序集限定名）。</summary>
        internal static Type ResolveType(string name)
        {
            if (string.IsNullOrEmpty(name)) return null;
            var type = Type.GetType(name);
            if (type != null) return type;
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    type = assembly.GetType(name);
                    if (type != null) return type;
                }
                catch
                {
                    // 加载失败的反射异常：跳过这个程序集
                }
            }

            return null;
        }
    }
}
