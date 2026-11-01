using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using LimbusPy.Bridge.Core.Reflect;

namespace LimbusPy.Bridge.Runtime
{
    /// <summary>
    /// Type index over the IL2CPP interop assemblies.
    ///
    /// At startup it indexes everything already loaded (Assembly-CSharp and friends are loaded by
    /// BepInEx before plugins run). Unknown names trigger a one-shot best-effort load of
    /// <c>BepInEx/interop/*.dll</c>, because a mod may touch a module that nothing else loaded yet.
    /// </summary>
    internal sealed class InteropTypeIndex : ITypeIndex
    {
        private static readonly string[] BlacklistPrefixes =
        {
            "System", "Microsoft", "netstandard", "mscorlib", "Mono.", "BepInEx", "Il2CppInterop",
            "0Harmony", "HarmonyLib", "LimbusPy", "AsmResolver", "Cpp2IL", "LibCpp2IL", "Disarm",
            "Iced", "Gee.External", "AssetRipper", "Newtonsoft", "JetBrains", "SharpCompress",
        };

        private readonly List<Assembly> _assemblies = new List<Assembly>();
        private readonly HashSet<string> _loadedNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private readonly string _interopDirectory;
        private AssemblyTypeIndex _index;
        private bool _scannedInteropDirectory;
        private readonly object _lock = new object();

        public InteropTypeIndex(string build, string interopDirectory)
        {
            Build = build ?? string.Empty;
            _interopDirectory = interopDirectory;
            Rebuild();
        }

        public string Build { get; }

        public int Count
        {
            get
            {
                lock (_lock) return _index?.Count ?? 0;
            }
        }

        public IEnumerable<Type> AllTypes
        {
            get
            {
                lock (_lock) return _index?.AllTypes ?? Array.Empty<Type>();
            }
        }

        public bool TryGetType(string name, out Type type)
        {
            lock (_lock)
            {
                if (_index != null && _index.TryGetType(name, out type)) return true;
            }

            EnsureInteropScanned();

            lock (_lock)
            {
                if (_index != null && _index.TryGetType(name, out type)) return true;
            }

            // Last resort: let the runtime resolve a fully qualified name.
            type = Type.GetType(name, throwOnError: false);
            return type != null;
        }

        private void Rebuild()
        {
            lock (_lock)
            {
                _assemblies.Clear();
                _loadedNames.Clear();
                foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
                {
                    TryAdd(assembly);
                }

                _index = new AssemblyTypeIndex(Build, _assemblies.ToArray());
            }
        }

        private void TryAdd(Assembly assembly)
        {
            if (assembly == null) return;
            var name = assembly.GetName().Name;
            if (string.IsNullOrEmpty(name)) return;
            foreach (var prefix in BlacklistPrefixes)
            {
                if (name.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return;
            }

            if (!_loadedNames.Add(name)) return;
            _assemblies.Add(assembly);
        }

        private void EnsureInteropScanned()
        {
            lock (_lock)
            {
                if (_scannedInteropDirectory) return;
                _scannedInteropDirectory = true;
            }

            if (string.IsNullOrEmpty(_interopDirectory) || !Directory.Exists(_interopDirectory)) return;

            string[] files;
            try
            {
                files = Directory.GetFiles(_interopDirectory, "*.dll");
            }
            catch
            {
                return;
            }

            var added = false;
            lock (_lock)
            {
                foreach (var file in files)
                {
                    var name = Path.GetFileNameWithoutExtension(file);
                    if (_loadedNames.Contains(name)) continue;
                    foreach (var prefix in BlacklistPrefixes)
                    {
                        if (name.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) break;
                    }

                    try
                    {
                        var assembly = Assembly.LoadFrom(file);
                        var before = _assemblies.Count;
                        TryAdd(assembly);
                        if (_assemblies.Count != before) added = true;
                    }
                    catch
                    {
                        // a module that fails to load simply stays unindexed
                    }
                }

                if (added) _index = new AssemblyTypeIndex(Build, _assemblies.ToArray());
            }
        }

        public IEnumerable<string> AssemblyNames
        {
            get
            {
                lock (_lock) return _assemblies.Select(a => a.GetName().Name).ToArray();
            }
        }
    }
}
