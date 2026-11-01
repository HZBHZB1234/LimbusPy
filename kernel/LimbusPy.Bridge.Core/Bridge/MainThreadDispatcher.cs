using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Threading;

namespace LimbusPy.Bridge.Core.Bridge
{
    /// <summary>
    /// Marshals work onto the "owner" thread (the game's main thread in the plugin, the test
    /// thread in the harness).
    ///
    /// Two modes:
    ///  * <see cref="Post"/> - fire-and-forget from any thread; drained by <see cref="Drain"/>.
    ///  * <see cref="RunNested"/> - the owner thread needs to *wait* on the other side (a
    ///    SYNCALL). While waiting it keeps draining, so replies still flow. Re-entry is refused
    ///    (depth 1) which is what turns an accidental Python-&gt;C#-&gt;Python cycle into a clear
    ///    fault instead of a hang.
    /// </summary>
    public sealed class MainThreadDispatcher
    {
        private readonly ConcurrentQueue<Action> _queue = new ConcurrentQueue<Action>();
        private readonly int _ownerThreadId;
        private int _nestedDepth;
        private int _maxNestedDepth = 1;

        public MainThreadDispatcher()
        {
            _ownerThreadId = Thread.CurrentThread.ManagedThreadId;
        }

        public int OwnerThreadId => _ownerThreadId;

        public bool IsOwnerThread => Thread.CurrentThread.ManagedThreadId == _ownerThreadId;

        /// <summary>Nested-pump budget in milliseconds; 0 disables nested pumping.</summary>
        public int NestedBudgetMs { get; set; } = 20;

        /// <summary>Maximum simultaneous nesting (1 = refuse re-entry, which prevents deadlocks).</summary>
        public int MaxNestedDepth
        {
            get => _maxNestedDepth;
            set => _maxNestedDepth = Math.Max(0, value);
        }

        public int PendingCount => _queue.Count;

        public long Posted { get; private set; }
        public long Executed { get; private set; }
        public long Faulted { get; private set; }
        public long NestedPumps { get; private set; }
        public long NestedRefused { get; private set; }

        public void Post(Action action)
        {
            if (action == null) return;
            Posted++;
            _queue.Enqueue(action);
        }

        /// <summary>Runs queued work on the owner thread. Returns how many actions ran.</summary>
        public int Drain(int maxItems = 256, TimeSpan? budget = null)
        {
            var stopwatch = budget.HasValue ? Stopwatch.StartNew() : null;
            var executed = 0;
            while (executed < maxItems && _queue.TryDequeue(out var action))
            {
                try
                {
                    action();
                }
                catch (Exception)
                {
                    Faulted++;
                }

                executed++;
                Executed++;
                if (stopwatch != null && stopwatch.Elapsed >= budget.Value) break;
            }
            return executed;
        }

        /// <summary>
        /// Blocks on the owner thread until <paramref name="isDone"/> or the deadline, draining
        /// posted work in between. Returns false on timeout or when nesting is refused.
        /// </summary>
        public bool RunNested(Func<bool> isDone, TimeSpan timeout, out string failure)
        {
            failure = null;
            if (!isDone()) return true;

            if (!Monitor.TryEnter(_nestedLock))
            {
                failure = "nested pump is already running on this thread";
                NestedRefused++;
                return false;
            }

            try
            {
                if (_nestedDepth >= _maxNestedDepth)
                {
                    failure = "nested pump depth limit reached (" + _nestedDepth + "/" + _maxNestedDepth + ")";
                    NestedRefused++;
                    return false;
                }

                _nestedDepth++;
                NestedPumps++;
                var stopwatch = Stopwatch.StartNew();
                var deadline = timeout <= TimeSpan.Zero ? TimeSpan.FromMilliseconds(NestedBudgetMs) : timeout;
                while (!isDone())
                {
                    Drain(64);
                    if (isDone()) break;
                    if (stopwatch.Elapsed >= deadline)
                    {
                        failure = "nested pump timed out after " + (int)stopwatch.Elapsed.TotalMilliseconds + " ms";
                        return false;
                    }

                    Thread.Sleep(1);
                }

                return true;
            }
            finally
            {
                _nestedDepth--;
                Monitor.Exit(_nestedLock);
            }
        }

        private readonly object _nestedLock = new object();

        public System.Collections.Generic.Dictionary<string, object> Stats()
            => new System.Collections.Generic.Dictionary<string, object>
            {
                ["posted"] = Posted,
                ["executed"] = Executed,
                ["faulted"] = Faulted,
                ["pending"] = (long)PendingCount,
                ["nested_pumps"] = NestedPumps,
                ["nested_refused"] = NestedRefused,
                ["owner_thread"] = (long)_ownerThreadId,
            };
    }
}
