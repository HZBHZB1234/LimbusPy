using System;
using System.Collections.Concurrent;
using System.Collections.Generic;

namespace LimbusPy.Bridge.Core.Diagnostics
{
    /// <summary>Lock-free named counters surfaced through STATS and limbuspy doctor.</summary>
    public sealed class Counters
    {
        private readonly ConcurrentDictionary<string, long> _values = new ConcurrentDictionary<string, long>(StringComparer.Ordinal);

        public void Increment(string name, long delta = 1) => _values.AddOrUpdate(name, delta, (_, current) => current + delta);

        public void Set(string name, long value) => _values[name] = value;

        public long Get(string name) => _values.TryGetValue(name, out var value) ? value : 0;

        public Dictionary<string, object> Snapshot()
        {
            var map = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (var pair in _values) map[pair.Key] = pair.Value;
            return map;
        }
    }

    /// <summary>Ring buffer of the last N notable events, dumped on crash or via DOCTOR.</summary>
    public sealed class TraceRing
    {
        private readonly object _lock = new object();
        private readonly string[] _items;
        private int _next;

        public TraceRing(int capacity = 256)
        {
            _items = new string[Math.Max(8, capacity)];
        }

        public void Add(string message)
        {
            lock (_lock)
            {
                _items[_next] = DateTime.UtcNow.ToString("HH:mm:ss.fff") + " " + message;
                _next = (_next + 1) % _items.Length;
            }
        }

        public IReadOnlyList<string> Snapshot()
        {
            lock (_lock)
            {
                var result = new List<string>(_items.Length);
                for (var i = 0; i < _items.Length; i++)
                {
                    var item = _items[(_next + i) % _items.Length];
                    if (!string.IsNullOrEmpty(item)) result.Add(item);
                }
                return result;
            }
        }
    }
}
