using System;
using System.Collections.Generic;
using System.Reflection;

namespace LimbusPy.Bridge.Core.Reflect
{
    /// <summary>
    /// Type index over a set of assemblies. The plugin hands it the IL2CPP interop assemblies;
    /// the conformance harness hands it the harness assembly. Lookup accepts C# display names
    /// ("Ns.Type", nested as "Ns.Outer.Inner" or "Ns.Outer+Inner").
    /// </summary>
    public sealed class AssemblyTypeIndex : ITypeIndex
    {
        private readonly Dictionary<string, Type> _byName = new Dictionary<string, Type>(StringComparer.Ordinal);
        private readonly List<Type> _all = new List<Type>();
        private bool _built;

        public AssemblyTypeIndex(string build = "", params Assembly[] assemblies)
        {
            Assemblies = assemblies ?? Array.Empty<Assembly>();
            Build = build ?? string.Empty;
            EnsureBuilt();
        }

        public Assembly[] Assemblies { get; }
        public string Build { get; }
        public int Count => _all.Count;

        /// <summary>Adds another assembly after construction (plugin registers lazily generated types).</summary>
        public void Add(Assembly assembly)
        {
            if (assembly == null) return;
            Index(assembly);
        }

        public bool TryGetType(string name, out Type type)
        {
            EnsureBuilt();
            if (name == null)
            {
                type = null;
                return false;
            }

            if (_byName.TryGetValue(name, out type)) return true;

            // "Ns.Outer.Inner" -> "Ns.Outer+Inner" (metadata nested separator)
            var nested = name.Replace('.', '+');
            if (nested != name && _byName.TryGetValue(nested, out type)) return true;

            foreach (var assembly in Assemblies)
            {
                type = assembly.GetType(name, throwOnError: false);
                if (type != null)
                {
                    _byName[name] = type;
                    return true;
                }
            }

            type = null;
            return false;
        }

        public IEnumerable<Type> AllTypes
        {
            get
            {
                EnsureBuilt();
                return _all;
            }
        }

        private void EnsureBuilt()
        {
            if (_built) return;
            _built = true;
            foreach (var assembly in Assemblies) Index(assembly);
        }

        private void Index(Assembly assembly)
        {
            if (assembly == null) return;
            Type[] types;
            try
            {
                types = assembly.GetTypes();
            }
            catch (ReflectionTypeLoadException ex)
            {
                types = ex.Types;
            }
            catch (Exception)
            {
                return;
            }

            if (types == null) return;
            foreach (var type in types)
            {
                if (type == null) continue;
                _all.Add(type);
                var fullName = type.FullName;
                if (!string.IsNullOrEmpty(fullName) && !_byName.ContainsKey(fullName)) _byName[fullName] = type;
            }
        }
    }

    /// <summary>Small named-singleton registry ("global:BattleObjectManager" -> instance).</summary>
    public sealed class GlobalRegistry : IGlobalRegistry
    {
        private readonly Dictionary<string, Func<object>> _providers = new Dictionary<string, Func<object>>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, object> _cached = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);

        public GlobalRegistry Register(string name, Func<object> provider)
        {
            if (string.IsNullOrEmpty(name) || provider == null) return this;
            _providers[name] = provider;
            return this;
        }

        public bool TryGet(string name, out object value)
        {
            value = null;
            if (string.IsNullOrEmpty(name)) return false;
            if (_cached.TryGetValue(name, out value) && value != null) return true;
            if (!_providers.TryGetValue(name, out var provider)) return false;
            try
            {
                value = provider();
            }
            catch (Exception)
            {
                value = null;
            }

            if (value != null) _cached[name] = value;
            return value != null;
        }

        public IEnumerable<string> Names => _providers.Keys;
    }
}
