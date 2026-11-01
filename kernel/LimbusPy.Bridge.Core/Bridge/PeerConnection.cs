using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using LimbusPy.Bridge.Core.Codec;
using LimbusPy.Bridge.Core.Framing;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Bridge.Core.Bridge
{
    /// <summary>Receives every inbound frame that is not a correlated result.</summary>
    public interface IFrameHandler
    {
        Task HandleAsync(PeerConnection peer, Frame frame, CancellationToken cancellationToken);
    }

    public sealed class PeerClosedException : Exception
    {
        public PeerClosedException(string message) : base(message) { }
    }

    public sealed class RequestTimeoutException : Exception
    {
        public RequestTimeoutException(string message) : base(message) { }

        public Dictionary<string, object> ToError() => Lpcp.Error(ErrorCode.Timeout, Message);
    }

    /// <summary>
    /// One LPCP connection: frame IO + request/response correlation.
    /// Thread-safety: SendAsync/RequestAsync may be called from any thread; HandleAsync is
    /// invoked from the read loop and must therefore marshal to the main thread itself when it
    /// needs to touch engine objects (the shell does this in MainThreadPump).
    /// </summary>
    public sealed class PeerConnection : IAsyncDisposable
    {
        private readonly Stream _stream;
        private readonly IFrameHandler _handler;
        private readonly SemaphoreSlim _writeLock = new SemaphoreSlim(1, 1);
        private readonly ConcurrentDictionary<long, TaskCompletionSource<Dictionary<string, object>>> _pending =
            new ConcurrentDictionary<long, TaskCompletionSource<Dictionary<string, object>>>();
        private readonly Dictionary<MsgType, TaskCompletionSource<Frame>> _oneShot =
            new Dictionary<MsgType, TaskCompletionSource<Frame>>();

        /// <summary>
        /// Frame types the owner is going to wait for. Such frames are buffered instead of being
        /// handed to the handler, which removes the "peer replied before WaitForAsync registered"
        /// race (the HELLO/READY handshake hits it reliably).
        /// </summary>
        private readonly HashSet<MsgType> _awaiting = new HashSet<MsgType>();
        private readonly Dictionary<MsgType, Queue<Frame>> _buffered = new Dictionary<MsgType, Queue<Frame>>();
        private const int MaxBufferedPerType = 64;
        private readonly object _oneShotLock = new object();
        private readonly CancellationTokenSource _cts = new CancellationTokenSource();
        private readonly Action<string> _log;
        private Task _readLoop = Task.CompletedTask;
        private long _nextId;
        private int _closed;
        private TaskCompletionSource<bool> _closeSignal = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);

        public PeerConnection(Stream stream, IFrameHandler handler, Action<string> log = null)
        {
            _stream = stream ?? throw new ArgumentNullException(nameof(stream));
            _handler = handler;
            _log = log ?? (_ => { });
        }

        public bool IsClosed => Volatile.Read(ref _closed) != 0;

        /// <summary>When true, every frame is echoed to the log sink (kernel diagnostics).</summary>
        public bool TraceFrames { get; set; }

        public long NextId() => Interlocked.Increment(ref _nextId);

        public void Start() => _readLoop = Task.Run(ReadLoopAsync);

        /// <summary>Completes when the peer disconnects or the connection is closed.</summary>
        public Task WaitForCloseAsync() => _closeSignal.Task;

        public async Task SendAsync(MsgType type, Dictionary<string, object> payload, CancellationToken cancellationToken = default)
        {
            if (IsClosed) throw new PeerClosedException("connection is closed");
            var bytes = EncodePayload(payload);
            await _writeLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                await FrameCodec.WriteAsync(_stream, type, bytes, cancellationToken).ConfigureAwait(false);
                if (TraceFrames) _log("-> " + type + " (" + bytes.Length + "B)");
            }
            finally
            {
                _writeLock.Release();
            }
        }

        public Task SendAsync(MsgType type, CancellationToken cancellationToken = default)
            => SendAsync(type, null, cancellationToken);

        /// <summary>
        /// Sends a request frame and awaits the matching CALLRESULT/SYNCRESULT.
        /// The coroutine accepts either result frame type keyed by id so that a version skew in the
        /// result frame type never deadlocks the caller.
        /// </summary>
        public async Task<Dictionary<string, object>> RequestAsync(
            MsgType type,
            Dictionary<string, object> payload,
            TimeSpan timeout,
            CancellationToken cancellationToken = default)
        {
            var id = NextId();
            if (payload == null) payload = Lpcp.Map();
            payload["id"] = id;

            var tcs = new TaskCompletionSource<Dictionary<string, object>>(TaskCreationOptions.RunContinuationsAsynchronously);
            _pending[id] = tcs;
            try
            {
                await SendAsync(type, payload, cancellationToken).ConfigureAwait(false);
                using (var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken))
                {
                    timeoutCts.CancelAfter(timeout);
                    using (timeoutCts.Token.Register(() =>
                           {
                               if (tcs.TrySetException(new RequestTimeoutException(
                                       type + " id=" + id + " timed out after " + timeout.TotalMilliseconds + " ms")))
                                   _pending.TryRemove(id, out _);
                           }))
                    {
                        return await tcs.Task.ConfigureAwait(false);
                    }
                }
            }
            catch
            {
                _pending.TryRemove(id, out _);
                throw;
            }
        }

        /// <summary>
        /// Declares frame types that will be awaited. Their frames are buffered (up to
        /// <see cref="MaxBufferedPerType"/>) rather than dispatched, so a reply that arrives
        /// before the waiter is registered is not lost.
        /// </summary>
        public void SetAwaiting(params MsgType[] types)
        {
            if (types == null) return;
            lock (_oneShotLock)
            {
                foreach (var type in types)
                {
                    _awaiting.Add(type);
                    if (!_buffered.ContainsKey(type)) _buffered[type] = new Queue<Frame>();
                }
            }
        }

        public void ClearAwaiting(params MsgType[] types)
        {
            if (types == null) return;
            lock (_oneShotLock)
            {
                foreach (var type in types)
                {
                    _awaiting.Remove(type);
                    _buffered.Remove(type);
                }
            }
        }

        /// <summary>Awaits the next frame of the given type (used for the HELLO/READY handshake).</summary>
        public async Task<Frame> WaitForAsync(MsgType type, TimeSpan timeout, CancellationToken cancellationToken = default)
        {
            TaskCompletionSource<Frame> tcs;
            lock (_oneShotLock)
            {
                if (_buffered.TryGetValue(type, out var buffered) && buffered.Count > 0)
                {
                    return buffered.Dequeue();
                }

                if (!_oneShot.TryGetValue(type, out tcs))
                {
                    tcs = new TaskCompletionSource<Frame>(TaskCreationOptions.RunContinuationsAsynchronously);
                    _oneShot[type] = tcs;
                }
            }

            try
            {
                using (var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken))
                {
                    timeoutCts.CancelAfter(timeout);
                    using (timeoutCts.Token.Register(() =>
                               tcs.TrySetException(new RequestTimeoutException("waiting for " + type + " timed out after " + timeout.TotalMilliseconds + " ms"))))
                    {
                        return await tcs.Task.ConfigureAwait(false);
                    }
                }
            }
            finally
            {
                lock (_oneShotLock) _oneShot.Remove(type);
            }
        }

        public async Task CloseAsync(string reason = "closed")
        {
            if (Interlocked.Exchange(ref _closed, 1) != 0) return;
            try
            {
                await SendAsync(MsgType.Bye, Lpcp.Bye(reason), _cts.Token).ConfigureAwait(false);
            }
            catch
            {
                // best effort only
            }

            try { _cts.Cancel(); } catch { }
            try { _stream.Dispose(); } catch { }

            foreach (var pair in _pending)
            {
                pair.Value.TrySetException(new PeerClosedException("connection closed while waiting for id=" + pair.Key));
            }
            _pending.Clear();

            lock (_oneShotLock)
            {
                foreach (var pair in _oneShot) pair.Value.TrySetException(new PeerClosedException("connection closed while waiting for " + pair.Key));
                _oneShot.Clear();
            }

            _closeSignal.TrySetResult(true);
        }

        public async ValueTask DisposeAsync()
        {
            await CloseAsync("disposed").ConfigureAwait(false);
            await Task.WhenAny(_readLoop, Task.Delay(500)).ConfigureAwait(false);
            _writeLock.Dispose();
            _cts.Dispose();
        }

        private async Task ReadLoopAsync()
        {
            var token = _cts.Token;
            try
            {
                while (!token.IsCancellationRequested)
                {
                    var frame = await FrameCodec.ReadAsync(_stream, token).ConfigureAwait(false);
                    if (frame == null) break;
                    await DispatchAsync(frame.Value, token).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                _log("read loop faulted: " + ex.GetType().Name + ": " + ex.Message);
            }
            finally
            {
                if (Interlocked.Exchange(ref _closed, 1) == 0)
                {
                    try { _stream.Dispose(); } catch { }
                    foreach (var pair in _pending)
                        pair.Value.TrySetException(new PeerClosedException("peer disconnected while waiting for id=" + pair.Key));
                    _pending.Clear();
                    lock (_oneShotLock)
                    {
                        foreach (var pair in _oneShot)
                            pair.Value.TrySetException(new PeerClosedException("peer disconnected while waiting for " + pair.Key));
                        _oneShot.Clear();
                    }
                }
                _closeSignal.TrySetResult(true);
            }
        }

        private async Task DispatchAsync(Frame frame, CancellationToken cancellationToken)
        {
            if (TraceFrames) _log("<- " + frame.Type + " (" + frame.Payload.Length + "B)");

            if (frame.Type == MsgType.CallResult || frame.Type == MsgType.SyncResult)
            {
                Dictionary<string, object> payload = null;
                try
                {
                    payload = DecodePayload(frame);
                }
                catch (Exception ex)
                {
                    _log("undecodable result frame: " + ex.Message);
                }

                if (payload != null && payload.TryGetValue("id", out var idValue))
                {
                    var id = ToLong(idValue);
                    if (_pending.TryRemove(id, out var tcs))
                    {
                        tcs.TrySetResult(payload);
                        return;
                    }
                    _log("orphan " + frame.Type + " id=" + id);
                    return;
                }
            }

            TaskCompletionSource<Frame> waiter;
            lock (_oneShotLock)
            {
                _oneShot.TryGetValue(frame.Type, out waiter);
                if (waiter == null && _awaiting.Contains(frame.Type))
                {
                    if (!_buffered.TryGetValue(frame.Type, out var queue))
                    {
                        queue = new Queue<Frame>();
                        _buffered[frame.Type] = queue;
                    }

                    queue.Enqueue(frame);
                    while (queue.Count > MaxBufferedPerType) queue.Dequeue();
                }
            }

            if (waiter != null)
            {
                waiter.TrySetResult(frame);
                return;
            }

            if (_awaiting.Contains(frame.Type))
            {
                // Buffered for a future waiter.
                return;
            }

            if (_handler == null) return;
            try
            {
                await _handler.HandleAsync(this, frame, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                _log("handler faulted on " + frame.Type + ": " + ex);
                try
                {
                    await SendAsync(MsgType.Error, Lpcp.Map("code", ErrorCode.Internal, "msg", ex.Message, "fatal", false), cancellationToken)
                        .ConfigureAwait(false);
                }
                catch
                {
                    // peer may be gone
                }
            }
        }

        public static Dictionary<string, object> DecodePayload(Frame frame)
        {
            if (!frame.HasPayload) return Lpcp.Map();
            var reader = new MsgPackReader(frame.Payload);
            return reader.ReadMapPayload();
        }

        public static byte[] EncodePayload(Dictionary<string, object> payload)
        {
            if (payload == null) return Array.Empty<byte>();
            var writer = new MsgPackWriter();
            writer.WriteValue(payload);
            return writer.ToArray();
        }

        private static long ToLong(object value)
        {
            switch (value)
            {
                case long l: return l;
                case ulong u: return unchecked((long)u);
                case int i: return i;
                case double d: return (long)d;
                case string s: return long.TryParse(s, out var parsed) ? parsed : 0;
                default: return 0;
            }
        }
    }
}
