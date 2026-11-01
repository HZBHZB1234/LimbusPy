using System;
using System.Collections.Generic;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Bridge.Core.Bridge
{
    public readonly struct HandleEntryInfo
    {
        public HandleEntryInfo(HandleKind kind, int scope, bool pinned, long expiresAtUnixMs, object target)
        {
            Kind = kind;
            Scope = scope;
            Pinned = pinned;
            ExpiresAtUnixMs = expiresAtUnixMs;
            Target = target;
        }

        public HandleKind Kind { get; }
        public int Scope { get; }
        public bool Pinned { get; }
        public long ExpiresAtUnixMs { get; }
        public object Target { get; }
    }

    public sealed class HandleStats
    {
        public int Live;
        public int Pinned;
        public int FreeSlots;
        public long Allocated;
        public long Released;
        public long Expired;
        public long DeadObjects;
        public long StaleAccesses;
        public override string ToString()
            => "live=" + Live + " pinned=" + Pinned + " free=" + FreeSlots + " allocated=" + Allocated +
               " released=" + Released + " expired=" + Expired + " dead=" + DeadObjects + " stale=" + StaleAccesses;
    }

    /// <summary>
    /// Handle table: u32 handle = (kind &lt;&lt; 28) | slot.
    ///
    /// Scopes implement the "arena" contract from docs/01-protocol.md §4.1: everything allocated
    /// inside a callback scope dies when the scope ends, so scripts can never observe a dangling
    /// pointer. Pinned entries survive scopes with a TTL.
    /// </summary>
    public sealed class HandleTable
    {
        private const int MaxSlots = 1 << 28;
        private const uint SlotMask = (1u << 28) - 1;

        private readonly object _lock = new object();
        private readonly Func<object, bool> _isAlive;
        private readonly Func<long> _clockMs;
        private readonly TimeSpan _defaultPinTtl;

        private object[] _targets = new object[256];
        private HandleKind[] _kinds = new HandleKind[256];
        private int[] _scopes = new int[256];
        private long[] _expires = new long[256];
        private bool[] _pinned = new bool[256];

        // Slot 0 is reserved: a handle with slot 0 means "no handle" on the wire.
        private int _highWater = 1;
        private readonly Stack<int> _freeSlots = new Stack<int>();

        private int _currentScope;
        private int _nextScope = 1;
        private readonly List<int> _scopeStack = new List<int>();

        public HandleTable(Func<object, bool> isAlive = null, Func<long> clockMs = null, TimeSpan? defaultPinTtl = null)
        {
            _isAlive = isAlive ?? (_ => true);
            _clockMs = clockMs ?? (() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
            _defaultPinTtl = defaultPinTtl ?? TimeSpan.FromMinutes(5);
            _currentScope = _nextScope++;
        }

        public HandleStats Stats { get; } = new HandleStats();

        public int CurrentScope
        {
            get { lock (_lock) return _currentScope; }
        }

        /// <summary>Opens a new arena scope and makes it current.</summary>
        public int BeginScope()
        {
            lock (_lock)
            {
                _scopeStack.Add(_currentScope);
                _currentScope = _nextScope++;
                return _currentScope;
            }
        }

        /// <summary>Closes a scope, freeing every non-pinned entry allocated in it.</summary>
        public int EndScope(int scope)
        {
            lock (_lock)
            {
                var freed = 0;
                for (var slot = 1; slot < _highWater; slot++)
                {
                    if (_targets[slot] == null) continue;
                    if (_scopes[slot] != scope) continue;
                    if (_pinned[slot]) continue;
                    FreeSlotLocked(slot);
                    freed++;
                }

                if (_scopeStack.Count > 0)
                {
                    _currentScope = _scopeStack[_scopeStack.Count - 1];
                    _scopeStack.RemoveAt(_scopeStack.Count - 1);
                }
                return freed;
            }
        }

        public uint Alloc(HandleKind kind, object target)
        {
            if (target == null) return 0;
            lock (_lock)
            {
                var slot = _freeSlots.Count > 0 ? _freeSlots.Pop() : _highWater++;
                if (slot >= MaxSlots) throw new InvalidOperationException("handle table exhausted");
                EnsureCapacityLocked(slot + 1);

                _targets[slot] = target;
                _kinds[slot] = kind;
                _scopes[slot] = _currentScope;
                _pinned[slot] = false;
                _expires[slot] = 0;
                Stats.Allocated++;
                Stats.Live++;
                SyncStatsLocked();
                return Encode(kind, slot);
            }
        }

        /// <summary>Promotes a handle to pinned (survives scope end) with an optional TTL.</summary>
        public bool Pin(uint handle, TimeSpan? ttl = null)
        {
            lock (_lock)
            {
                if (!TryDecode(handle, out var kind, out var slot)) return false;
                if (slot >= _highWater || _targets[slot] == null) return false;
                if (_kinds[slot] != kind) return false;
                _pinned[slot] = true;
                _expires[slot] = _clockMs() + (long)(ttl ?? _defaultPinTtl).TotalMilliseconds;
                SyncStatsLocked();
                return true;
            }
        }

        public bool Release(uint handle)
        {
            lock (_lock)
            {
                if (!TryDecode(handle, out var kind, out var slot)) return false;
                if (slot >= _highWater || _targets[slot] == null || _kinds[slot] != kind) return false;
                FreeSlotLocked(slot);
                return true;
            }
        }

        /// <summary>Resolves a handle to its target. Tracks stale access for diagnostics.</summary>
        public bool TryResolve(uint handle, out HandleKind kind, out object target)
        {
            kind = HandleKind.None;
            target = null;
            if (!TryDecode(handle, out var handleKind, out var slot))
            {
                lock (_lock) { Stats.StaleAccesses++; }
                return false;
            }

            lock (_lock)
            {
                if (slot >= _highWater || _targets[slot] == null || _kinds[slot] != handleKind)
                {
                    Stats.StaleAccesses++;
                    return false;
                }

                var candidate = _targets[slot];
                if (!_isAlive(candidate))
                {
                    Stats.DeadObjects++;
                    FreeSlotLocked(slot);
                    return false;
                }

                kind = handleKind;
                target = candidate;
                return true;
            }
        }

        public bool TryDescribe(uint handle, out HandleEntryInfo info)
        {
            info = default;
            if (!TryDecode(handle, out var kind, out var slot)) return false;
            lock (_lock)
            {
                if (slot >= _highWater || _targets[slot] == null) return false;
                info = new HandleEntryInfo(kind, _scopes[slot], _pinned[slot], _expires[slot], _targets[slot]);
                return true;
            }
        }

        /// <summary>Drops expired pinned handles and dead targets. Call periodically (e.g. once per second).</summary>
        public int Sweep()
        {
            lock (_lock)
            {
                var now = _clockMs();
                var removed = 0;
                for (var slot = 1; slot < _highWater; slot++)
                {
                    if (_targets[slot] == null) continue;
                    var expired = _pinned[slot] && _expires[slot] != 0 && now >= _expires[slot];
                    if (!expired && _isAlive(_targets[slot])) continue;
                    if (_pinned[slot] && expired) Stats.Expired++;
                    else Stats.DeadObjects++;
                    FreeSlotLocked(slot);
                    removed++;
                }
                return removed;
            }
        }

        public static uint Encode(HandleKind kind, int slot) => ((uint)kind << 28) | ((uint)slot & SlotMask);

        public static bool TryDecode(uint handle, out HandleKind kind, out int slot)
        {
            kind = (HandleKind)(handle >> 28);
            slot = (int)(handle & SlotMask);
            return kind != HandleKind.None && slot > 0;
        }

        private void FreeSlotLocked(int slot)
        {
            _targets[slot] = null;
            _pinned[slot] = false;
            _expires[slot] = 0;
            _kinds[slot] = HandleKind.None;
            _scopes[slot] = 0;
            _freeSlots.Push(slot);
            Stats.Released++;
            Stats.Live--;
            SyncStatsLocked();
        }

        private void EnsureCapacityLocked(int needed)
        {
            if (needed <= _targets.Length) return;
            var size = _targets.Length;
            while (size < needed) size *= 2;
            Array.Resize(ref _targets, size);
            Array.Resize(ref _kinds, size);
            Array.Resize(ref _scopes, size);
            Array.Resize(ref _expires, size);
            Array.Resize(ref _pinned, size);
        }

        private void SyncStatsLocked()
        {
            Stats.FreeSlots = _freeSlots.Count;
            var pinned = 0;
            for (var slot = 1; slot < _highWater; slot++)
            {
                if (_targets[slot] != null && _pinned[slot]) pinned++;
            }
            Stats.Pinned = pinned;
        }
    }
}
