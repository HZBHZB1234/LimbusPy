using System;
using System.IO;
using System.Reflection;
using BepInEx;
using LimbusPy.Bridge.Core.Ops;

namespace LimbusPy.Bridge.Runtime
{
    /// <summary>
    /// 动态加载 A 层程序集（``LimbusPy.Ops.dll``）。
    ///
    /// **为什么不 ProjectReference**：那个程序集编译期引用游戏 interop 程序集，游戏大更可能让它
    /// 加载失败（类型/方法签名变了）。用 Assembly.LoadFrom + 接口转型，失败时内核照常运行，
    /// 只是不广告 ``ops.v1`` —— Python 侧降级到 B 层（反射 + 生成绑定）。
    /// 这是 docs/05-typed-api.md §4 的「封装层可独立失效」。
    /// </summary>
    internal static class OpsLoader
    {
        internal const string AssemblyName = "LimbusPy.Ops.dll";
        internal const string EntryType = "LimbusPy.Ops.OpsEntry";

        internal static IOpsProvider Load(Action<string> log)
        {
            var path = Resolve();
            if (path == null)
            {
                log?.Invoke("LimbusPy.Ops not installed: A-layer ops disabled (B-layer fallback)");
                return null;
            }

            try
            {
                var assembly = Assembly.LoadFrom(path);
                var type = assembly.GetType(EntryType, throwOnError: false);
                if (type == null)
                {
                    log?.Invoke("LimbusPy.Ops loaded but " + EntryType + " is missing");
                    return null;
                }

                var provider = Activator.CreateInstance(type) as IOpsProvider;
                if (provider == null)
                {
                    log?.Invoke("LimbusPy.Ops." + EntryType + " does not implement IOpsProvider");
                    return null;
                }

                log?.Invoke("LimbusPy.Ops loaded: " + provider.Name +
                            (provider.Available ? " (available)" : " (self-check failed, degraded)"));
                return provider;
            }
            catch (Exception ex)
            {
                // 最常见的失败：游戏更新导致 interop 类型变了 → TypeLoadException / MissingMethod。
                log?.Invoke("LimbusPy.Ops failed to load (A-layer disabled, B-layer still works): " +
                            ex.GetType().Name + ": " + ex.Message);
                return null;
            }
        }

        private static string Resolve()
        {
            try
            {
                var location = typeof(Plugin).Assembly.Location;
                if (!string.IsNullOrEmpty(location))
                {
                    var directory = Path.GetDirectoryName(location);
                    if (!string.IsNullOrEmpty(directory))
                    {
                        var candidate = Path.Combine(directory, AssemblyName);
                        if (File.Exists(candidate)) return candidate;
                    }
                }
            }
            catch
            {
                // 落到下面的约定路径
            }

            try
            {
                var pluginPath = Path.Combine(Paths.PluginPath, "LimbusPy", AssemblyName);
                return File.Exists(pluginPath) ? pluginPath : null;
            }
            catch
            {
                return null;
            }
        }
    }
}
