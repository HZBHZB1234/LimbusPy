using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using LimbusPy.Bridge.Core.Bridge;
using LimbusPy.Bridge.Core.Codec;
using LimbusPy.Bridge.Core.Diagnostics;
using LimbusPy.Bridge.Core.Framing;
using LimbusPy.Bridge.Core.Protocol;

namespace LimbusPy.Bridge.Tests
{
    internal sealed class LpcpException : Exception
    {
        public LpcpException(string code, string message) : base(message)
        {
            Code = code;
        }

        public string Code { get; }
    }

    /// <summary>
    /// Scripted kernel used by tools/conformance.py. Exercises the same core classes the real
    /// plugin uses (PeerConnection, HandleTable, msgpack codec) with no Unity dependency.
    /// </summary>
    internal sealed class TestKernel : IFrameHandler
    {
        private readonly HandleTable _handles = new HandleTable();
        private readonly Dictionary<string, Func<object[], object>> _methods;
        private readonly CancellationTokenSource _cts = new CancellationTokenSource();

        public TestKernel()
        {
            _methods = BuildMethods();
        }

        public Counters Counters { get; } = new Counters();
        public List<Dictionary<string, object>> LogLines { get; } = new List<Dictionary<string, object>>();
        public List<string> Failures { get; } = new List<string>();
        public int CallsHandled { get; private set; }
        public int RequestFrames { get; private set; }
        public HandleStats Handles => _handles.Stats;

        public PeerConnection Peer { get; private set; }

        public void Attach(PeerConnection peer) => Peer = peer;

        public void Stop() => _cts.Cancel();

        // -- IFrameHandler ------------------------------------------------------------------

        public async Task HandleAsync(PeerConnection peer, Frame frame, CancellationToken cancellationToken)
        {
            switch (frame.Type)
            {
                case MsgType.Call:
                    await HandleCallAsync(peer, frame, cancellationToken).ConfigureAwait(false);
                    return;

                case MsgType.Ping:
                    Counters.Increment("ping");
                    await peer.SendAsync(MsgType.Pong, cancellationToken).ConfigureAwait(false);
                    return;

                case MsgType.Pong:
                    Counters.Increment("pong");
                    return;

                case MsgType.Log:
                    LogLines.Add(PeerConnection.DecodePayload(frame));
                    return;

                case MsgType.Bye:
                    Counters.Increment("bye");
                    return;

                case MsgType.Error:
                {
                    var payload = PeerConnection.DecodePayload(frame);
                    Failures.Add("peer error: " + Lpcp.GetString(payload, "code") + ": " + Lpcp.GetString(payload, "msg"));
                    return;
                }
            }

            var message = PeerConnection.DecodePayload(frame);
            var id = Lpcp.GetLong(message, "id", -1);
            if (id >= 0)
            {
                RequestFrames++;
                await peer.SendAsync(MsgType.CallResult,
                    Lpcp.ErrorResult(id, Lpcp.Error(ErrorCode.Unsupported, "harness has no handler for " + frame.Type)),
                    cancellationToken).ConfigureAwait(false);
            }
        }

        private async Task HandleCallAsync(PeerConnection peer, Frame frame, CancellationToken cancellationToken)
        {
            var message = PeerConnection.DecodePayload(frame);
            var id = Lpcp.GetLong(message, "id");
            var method = Lpcp.GetString(message, "method", string.Empty);
            var args = Lpcp.GetArray(message, "args");
            CallsHandled++;
            Counters.Increment("call");

            // Deliberately replies late so the client's correlation is tested against out-of-order results.
            if (method == "slow")
            {
                var delayMs = args.Length > 0 && args[0] is long requested ? requested : 50L;
                var capturedId = id;
                _ = Task.Run(async () =>
                {
                    await Task.Delay((int)delayMs).ConfigureAwait(false);
                    await ReplyOkAsync(peer, capturedId, "late").ConfigureAwait(false);
                }, cancellationToken);
                return;
            }

            try
            {
                var value = Dispatch(method, args);
                await ReplyOkAsync(peer, id, value).ConfigureAwait(false);
            }
            catch (LpcpException ex)
            {
                await peer.SendAsync(MsgType.CallResult, Lpcp.ErrorResult(id, Lpcp.Error(ex.Code, ex.Message)),
                    cancellationToken).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                await peer.SendAsync(MsgType.CallResult,
                    Lpcp.ErrorResult(id, Lpcp.Error(ErrorCode.Internal, ex.GetType().Name + ": " + ex.Message)),
                    cancellationToken).ConfigureAwait(false);
            }
        }

        private async Task ReplyOkAsync(PeerConnection peer, long id, object value)
        {
            try
            {
                await peer.SendAsync(MsgType.CallResult, Lpcp.OkResult(id, value), _cts.Token).ConfigureAwait(false);
            }
            catch (MsgPackException ex)
            {
                Counters.Increment("marshal_error");
                await peer.SendAsync(MsgType.CallResult,
                    Lpcp.ErrorResult(id, Lpcp.Error(ErrorCode.Marshal, ex.Message)), _cts.Token).ConfigureAwait(false);
            }
        }

        // -- kernel -> python ---------------------------------------------------------------

        public async Task<List<Dictionary<string, object>>> EmitEventsAsync(int count, TimeSpan timeout)
        {
            var events = new List<object>();
            for (var i = 0; i < count; i++)
            {
                events.Add(new Dictionary<string, object>
                {
                    ["timing"] = "OnSucceedAttack",
                    ["gen"] = 1L,
                    ["ctx"] = Lpcp.Ref(HandleKind.Unit, (uint)(0x1000 + i)),
                    ["index"] = (long)i,
                });
            }

            await Peer.SendAsync(MsgType.EventBatch, Lpcp.Map(
                "seq", 1L,
                "frame", 1180L,
                "events", events.ToArray()), _cts.Token).ConfigureAwait(false);

            var frame = await Peer.WaitForAsync(MsgType.Act, timeout).ConfigureAwait(false);
            var payload = PeerConnection.DecodePayload(frame);
            Counters.Increment("event_batch");
            var actions = new List<Dictionary<string, object>>();
            foreach (var item in Lpcp.GetArray(payload, "actions"))
            {
                if (item is Dictionary<string, object> action) actions.Add(action);
            }
            return actions;
        }

        public async Task<object> SyncAsync(string fn, TimeSpan timeout)
        {
            var result = await Peer.RequestAsync(MsgType.SyncCall, Lpcp.Map(
                "fn", fn,
                "ctx", Lpcp.Map("timing", "OnSucceedAttack", "gen", 1L),
                "deadline_ms", 20L), timeout).ConfigureAwait(false);

            if (!Lpcp.GetBool(result, "ok"))
            {
                var error = Lpcp.GetMap(result, "err") ?? Lpcp.Map();
                throw new LpcpException(Lpcp.GetString(error, "code", "internal"),
                    Lpcp.GetString(error, "msg", "sync call failed"));
            }

            Counters.Increment("sync_call");
            return result.TryGetValue("value", out var value) ? value : null;
        }

        // -- method table -------------------------------------------------------------------

        private Dictionary<string, Func<object[], object>> BuildMethods()
        {
            return new Dictionary<string, Func<object[], object>>(StringComparer.Ordinal)
            {
                ["echo"] = args => args.Length > 0 ? args[0] : null,
                ["add"] = args => ToLong(args, 0) + ToLong(args, 1),
                ["concat"] = args => ToStr(args, 0) + ToStr(args, 1),
                ["nested"] = _ => new Dictionary<string, object>
                {
                    ["ok"] = new object[] { 1L, new Dictionary<string, object> { ["deep"] = null } },
                    ["bin"] = new byte[] { 0x00, 0x01 },
                    ["f"] = 2.5d,
                },
                ["bytes"] = args => new byte[] { 0, 1, 2, 255 },
                ["big"] = args =>
                {
                    var size = (int)ToLong(args, 0, 1024);
                    return new string('z', size);
                },
                ["boom"] = _ => throw new LpcpException(ErrorCode.Script, "kaboom"),
                ["internal_boom"] = _ => throw new InvalidOperationException("harness internal failure"),
                ["marshal_bad"] = _ => new object(),
                ["ping"] = _ => "pong",

                // handle table semantics -----------------------------------------------------
                ["handles.scope.begin"] = _ => (long)_handles.BeginScope(),
                ["handles.scope.end"] = args => (long)_handles.EndScope((int)ToLong(args, 0)),
                ["handles.alloc"] = args =>
                {
                    var tag = ToStr(args, 0);
                    var handle = _handles.Alloc(HandleKind.Obj, new TaggedObject(tag));
                    return Lpcp.Ref(HandleKind.Obj, handle);
                },
                ["handles.tag"] = args =>
                {
                    var handle = ResolveRef(args, 0);
                    if (!_handles.TryResolve(handle, out var kind, out var target))
                        throw new LpcpException(ErrorCode.StaleHandle, "handle 0x" + handle.ToString("x") + " is not live");
                    if (!(target is TaggedObject tagged))
                        throw new LpcpException(ErrorCode.Internal, "handle target is not a TaggedObject");
                    return (object)tagged.Tag;
                },
                ["handles.pin"] = args => (object)_handles.Pin(ResolveRef(args, 0)),
                ["handles.release"] = args => (object)_handles.Release(ResolveRef(args, 0)),
                ["handles.describe"] = args =>
                {
                    var handle = ResolveRef(args, 0);
                    if (!_handles.TryDescribe(handle, out var info))
                        throw new LpcpException(ErrorCode.StaleHandle, "handle not live");
                    return new Dictionary<string, object>
                    {
                        ["kind"] = HandleKindNames.ToWire(info.Kind),
                        ["scope"] = (long)info.Scope,
                        ["pinned"] = info.Pinned,
                    };
                },
                ["handles.stats"] = _ =>
                {
                    var stats = _handles.Stats;
                    return new Dictionary<string, object>
                    {
                        ["live"] = (long)stats.Live,
                        ["pinned"] = (long)stats.Pinned,
                        ["allocated"] = stats.Allocated,
                        ["released"] = stats.Released,
                        ["stale"] = stats.StaleAccesses,
                    };
                },

                // failure injection ----------------------------------------------------------
                ["self.close"] = args =>
                {
                    var ignored = Task.Run(async () =>
                    {
                        await Task.Delay(30).ConfigureAwait(false);
                        await Peer.CloseAsync("harness asked to close").ConfigureAwait(false);
                    });
                    GC.KeepAlive(ignored);
                    return true;
                },
            };
        }

        private object Dispatch(string method, object[] args)
        {
            if (!_methods.TryGetValue(method, out var fn))
                throw new LpcpException(ErrorCode.NotFound, "unknown method: " + method);
            return fn(args);
        }

        private static uint ResolveRef(object[] args, int index)
        {
            var value = args.Length > index ? args[index] : null;
            if (!Lpcp.TryReadRef(value, out var kind, out var handle, out _))
                throw new LpcpException(ErrorCode.Marshal, "argument " + index + " is not a handle reference");
            return handle;
        }

        private static long ToLong(object[] args, int index, long fallback = 0)
        {
            if (args.Length <= index) return fallback;
            switch (args[index])
            {
                case long l: return l;
                case ulong u: return u <= long.MaxValue ? (long)u : fallback;
                case int i: return i;
                case double d: return (long)d;
                case Dictionary<string, object> map: return Lpcp.GetLong(map, "h", fallback);
                default: return fallback;
            }
        }

        private static string ToStr(object[] args, int index)
        {
            return args.Length > index && args[index] is string s ? s : string.Empty;
        }

        private sealed class TaggedObject
        {
            public TaggedObject(string tag) => Tag = tag;
            public string Tag { get; }
        }
    }
}
