using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using LimbusPy.Bridge.Core.Bridge;
using LimbusPy.Bridge.Core.Diagnostics;
using LimbusPy.Bridge.Core.Framing;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Reflect;
using LimbusPy.Bridge.Core.Transport;
using LimbusPy.Bridge.Core.Snapshot;
using LimbusPy.Bridge.Patching;

namespace LimbusPy.Bridge.Runtime
{
    /// <summary>
    /// Owns the LPCP connection: accepts the pipe pair, performs the HELLO/READY handshake, and
    /// routes every inbound message. Anything that can touch game objects is posted to the main
    /// thread through <see cref="MainThreadDispatcher"/>; replies are written straight from that
    /// callback (sending never blocks on the game).
    /// </summary>
    internal sealed class KernelHost : IFrameHandler
    {
        private readonly BridgeSettings _settings;
        private readonly Action<string> _log;
        private readonly MainThreadDispatcher _dispatcher;
        private readonly CallMarshaller _marshaller;
        private readonly ReflectionInvoker _invoker;
        private readonly Il2CppRefStore _store;
        private readonly InteropTypeIndex _types;
        private readonly GlobalRegistry _globals;
        private readonly HarmonyPatchHub _patches;
        private readonly SnapshotService _snapshot;
        private readonly CancellationTokenSource _cts = new CancellationTokenSource();

        /// <summary>Re-entry guard for kernel->Python->kernel->... cycles (protocol §6.2).</summary>
        [ThreadStatic] private static int _syncDepth;

        private PeerConnection _peer;
        private string _pipePrefix;
        private int _tcpPort;
        private string _gameVersion = string.Empty;
        private string _unityVersion = string.Empty;
        private int _generation;

        public KernelHost(
            BridgeSettings settings,
            Action<string> log,
            MainThreadDispatcher dispatcher,
            CallMarshaller marshaller,
            ReflectionInvoker invoker,
            Il2CppRefStore store,
            InteropTypeIndex types,
            GlobalRegistry globals,
            HarmonyPatchHub patches,
            SnapshotService snapshot)
        {
            _settings = settings;
            _log = log;
            _dispatcher = dispatcher;
            _marshaller = marshaller;
            _invoker = invoker;
            _store = store;
            _types = types;
            _globals = globals;
            _patches = patches;
            _snapshot = snapshot;
            if (_patches != null) _patches.Dispatch = DispatchToPython;
        }

        public Counters Counters { get; } = new Counters();
        public TraceRing Trace { get; } = new TraceRing(256);

        public bool Connected => _peer != null && !_peer.IsClosed;
        public string PipePrefix => _pipePrefix;
        public int TcpPort => _tcpPort;
        public int Generation => _generation;

        public void Start()
        {
            _ = Task.Run(() => AcceptLoopAsync(_cts.Token));
        }

        public void Stop()
        {
            try
            {
                _cts.Cancel();
            }
            catch
            {
                // ignore
            }

            var peer = _peer;
            if (peer != null)
            {
                try
                {
                    peer.CloseAsync("kernel shutting down").GetAwaiter().GetResult();
                }
                catch
                {
                    // ignore
                }
            }
        }

        public void SetGameInfo(string gameVersion, string unityVersion)
        {
            _gameVersion = gameVersion ?? string.Empty;
            _unityVersion = unityVersion ?? string.Empty;
        }

        // -- accept loop --------------------------------------------------------------------

        private async Task AcceptLoopAsync(CancellationToken cancellationToken)
        {
            _pipePrefix = NamedPipeTransport.NameForProcess(Environment.ProcessId);

            while (!cancellationToken.IsCancellationRequested)
            {
                Stream stream = null;
                try
                {
                    if (string.Equals(_settings.Transport.Value, "tcp", StringComparison.OrdinalIgnoreCase))
                    {
                        var listener = TcpTransport.CreateListener(_settings.TcpPort.Value);
                        _tcpPort = TcpTransport.PortOf(listener);
                        _log("tcp endpoint 127.0.0.1:" + _tcpPort);
                        stream = await TcpTransport.AcceptAsync(listener, cancellationToken).ConfigureAwait(false);
                    }
                    else
                    {
                        _log("pipe endpoint " + _pipePrefix + "{.toPy,.fromPy}");
                        stream = await NamedPipeTransport.AcceptPairAsync(_pipePrefix, cancellationToken).ConfigureAwait(false);
                    }

                    await ServeAsync(stream, cancellationToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    return;
                }
                catch (Exception ex)
                {
                    Trace.Add("accept failed: " + ex.Message);
                    _log("accept failed: " + ex.Message);
                }
                finally
                {
                    try
                    {
                        stream?.Dispose();
                    }
                    catch
                    {
                        // ignore
                    }

                    _peer = null;
                }

                if (cancellationToken.IsCancellationRequested) return;
                await Task.Delay(500, cancellationToken).ConfigureAwait(false);
            }
        }

        private async Task ServeAsync(Stream stream, CancellationToken cancellationToken)
        {
            var peer = new PeerConnection(stream, this, message =>
            {
                if (_settings.Verbose.Value) _log(message);
            })
            {
                TraceFrames = _settings.Verbose.Value,
            };

            _peer = peer;
            peer.SetAwaiting(MsgType.Ready);
            peer.Start();
            _log("python connected");

            try
            {
                await peer.SendAsync(MsgType.Hello, Lpcp.Hello(
                    Plugin.Version,
                    _settings.StubsBuild.Value ?? string.Empty,
                    _gameVersion + "/" + _unityVersion,
                    AdvertisedCaps()), cancellationToken).ConfigureAwait(false);

                Frame readyFrame;
                try
                {
                    readyFrame = await peer.WaitForAsync(MsgType.Ready,
                        TimeSpan.FromMilliseconds(Math.Max(500, _settings.HandshakeTimeoutMs.Value)),
                        cancellationToken).ConfigureAwait(false);
                }
                catch (Exception ex)
                {
                    _log("handshake failed: " + ex.Message);
                    await peer.CloseAsync("handshake timeout").ConfigureAwait(false);
                    return;
                }

                var ready = PeerConnection.DecodePayload(readyFrame);
                var peerProto = Lpcp.GetString(ready, "proto");
                if (!ProtocolInfo.IsCompatible(peerProto))
                {
                    _log("incompatible protocol: kernel " + ProtocolInfo.Version + ", python " + peerProto);
                    await peer.SendAsync(MsgType.Error, Lpcp.Map(
                        "code", ErrorCode.Proto,
                        "msg", "kernel speaks " + ProtocolInfo.Version + ", python speaks " + peerProto,
                        "fatal", true)).ConfigureAwait(false);
                    await peer.CloseAsync("protocol mismatch").ConfigureAwait(false);
                    return;
                }

                _generation++;
                _log("python ready (py=" + Lpcp.GetString(ready, "py") + ", mods=" +
                     string.Join(",", Lpcp.GetStringArray(ready, "mods")) + ", gen=" + _generation + ")");

                var stubs = Lpcp.GetString(ready, "stubs_build");
                var kernelStubs = _settings.StubsBuild.Value;
                if (!string.IsNullOrEmpty(stubs) && !string.IsNullOrEmpty(kernelStubs) && stubs != kernelStubs)
                {
                    _log("WARNING: python stubs were generated for build '" + stubs + "' but the kernel expects '" +
                         kernelStubs + "'; run `limbpy stubs --refresh`");
                }

                await peer.WaitForCloseAsync().ConfigureAwait(false);
                _log("python disconnected");
            }
            finally
            {
                await peer.DisposeAsync().ConfigureAwait(false);
            }
        }

        private static string[] AdvertisedCaps()
        {
            // Only claim what this build actually implements; Python degrades gracefully otherwise.
            return new[]
            {
                Caps.ReflectManaged,
                Caps.ReflectField,
                Caps.HandleArena,
                Caps.HandlePinned,
                Caps.BudgetFrame,
            };
        }

        // -- inbound -----------------------------------------------------------------------

        public Task HandleAsync(PeerConnection peer, Frame frame, CancellationToken cancellationToken)
        {
            switch (frame.Type)
            {
                case MsgType.Ping:
                    Counters.Increment("ping");
                    ReplyLater(peer, MsgType.Pong, null);
                    return Task.CompletedTask;

                case MsgType.Pong:
                    Counters.Increment("pong");
                    return Task.CompletedTask;

                case MsgType.Bye:
                    Counters.Increment("bye");
                    Trace.Add("python sent BYE");
                    return Task.CompletedTask;

                case MsgType.Log:
                {
                    var payload = PeerConnection.DecodePayload(frame);
                    _log("[py/" + Lpcp.GetString(payload, "mod", "-") + "] " + Lpcp.GetString(payload, "msg"));
                    return Task.CompletedTask;
                }

                case MsgType.Error:
                {
                    var payload = PeerConnection.DecodePayload(frame);
                    _log("python error: " + Lpcp.GetString(payload, "code") + " " + Lpcp.GetString(payload, "msg"));
                    return Task.CompletedTask;
                }
            }

            // Everything else may touch engine objects -> main thread.
            Counters.Increment("inbound." + frame.Type);
            _dispatcher.Post(() => ProcessOnMainThread(peer, frame));
            return Task.CompletedTask;
        }

        private void ProcessOnMainThread(PeerConnection peer, Frame frame)
        {
            Dictionary<string, object> request;
            try
            {
                request = PeerConnection.DecodePayload(frame);
            }
            catch (Exception ex)
            {
                Trace.Add("undecodable " + frame.Type + ": " + ex.Message);
                _log("undecodable frame " + frame.Type + ": " + ex.Message);
                return;
            }

            var id = Lpcp.GetLong(request, "id", -1);
            Dictionary<string, object> reply;
            try
            {
                reply = Dispatch(frame.Type, request, id);
            }
            catch (LpcpFault fault)
            {
                Counters.Increment("fault." + fault.Code);
                reply = Lpcp.ErrorResult(id, fault.ToError());
            }
            catch (Exception ex)
            {
                Counters.Increment("fault.internal");
                Trace.Add("handler " + frame.Type + " threw: " + ex);
                reply = Lpcp.ErrorResult(id, Lpcp.Error(ErrorCode.Internal, ex.GetType().Name + ": " + ex.Message));
            }

            if (reply != null) ReplyLater(peer, MsgType.CallResult, reply);
        }

        private Dictionary<string, object> Dispatch(MsgType type, Dictionary<string, object> request, long id)
        {
            switch (type)
            {
                case MsgType.Call:
                {
                    var target = request.TryGetValue("target", out var targetValue) ? targetValue : null;
                    var method = Lpcp.GetString(request, "method", string.Empty);
                    var args = Lpcp.GetArray(request, "args");
                    var value = _invoker.Invoke(target, method, args);
                    Counters.Increment("call");
                    return Lpcp.OkResult(id, value);
                }

                case MsgType.Doctor:
                    return Lpcp.OkResult(id, BuildDoctor());

                case MsgType.Snapshot:
                {
                    if (_snapshot == null) throw LpcpFault.Unsupported("snapshot service is not available");
                    var battle = _snapshot.Capture();
                    Counters.Increment("snapshot");
                    return Lpcp.OkResult(id, battle);
                }

                case MsgType.Stats:
                    return Lpcp.OkResult(id, BuildStats());

                case MsgType.PatchAdd:
                {
                    if (_patches == null) throw LpcpFault.Unsupported("patch hub is not available");
                    var binding = _patches.Apply(PatchSpec.FromWire(request));
                    Counters.Increment("patch.add");
                    Trace.Add("patched " + binding.Describe());
                    _log("patched " + binding.Describe());
                    return Lpcp.OkResult(id, Lpcp.Map(
                        "id", binding.Id,
                        "target", PatchMethodResolver.Describe(binding.Method),
                        "kind", binding.Spec.Kind == PatchKind.Prefix ? "prefix" : "postfix",
                        "fn", binding.Spec.Fn));
                }

                case MsgType.PatchDel:
                {
                    if (_patches == null) throw LpcpFault.Unsupported("patch hub is not available");
                    var patchId = Lpcp.GetString(request, "patch") ?? Lpcp.GetString(request, "id_name");
                    var removed = _patches.Remove(patchId);
                    if (!removed) throw LpcpFault.NotFound("no patch with id '" + patchId + "'");
                    Counters.Increment("patch.del");
                    Trace.Add("unpatched " + patchId);
                    return Lpcp.OkResult(id, true);
                }

                case MsgType.RegistryAdd:
                case MsgType.RegistryDel:
                    throw LpcpFault.Unsupported(
                        "Modular registry wiring is not implemented in this build",
                        Lpcp.Map("milestone", "modular.registry"));

                case MsgType.State:
                    throw LpcpFault.Unsupported("state transfer is handled by the Python side in this build");

                default:
                    throw LpcpFault.Unsupported("unhandled message type " + type);
            }
        }

        /// <summary>
        /// One patch dispatch: ask Python for a decision and wait for it.
        ///
        /// This runs on the game's main thread (Harmony patch), so it drains queued main-thread
        /// work while waiting. Re-entry is refused: a Python hook that triggers another patched
        /// method gets its default instead of a deadlock. Unknown/timeout keeps the original
        /// behaviour (fail-open).
        /// </summary>
        private object DispatchToPython(PatchCall call)
        {
            var peer = _peer;
            if (peer == null || peer.IsClosed || call == null) return null;
            if (_syncDepth >= 1)
            {
                Counters.Increment("patch.reentry_refused");
                call.Failure = "nested patch dispatch refused";
                return null;
            }

            var binding = call.Binding;
            var context = Lpcp.Map(
                "patch", binding.Id,
                "kind", binding.Spec.Kind == PatchKind.Prefix ? "prefix" : "postfix",
                "target", PatchMethodResolver.Describe(binding.Method),
                "instance", SafeWire(call.Instance),
                "args", SafeWireArray(call.Args),
                "result", SafeWire(call.Result));

            var timeoutMs = _patches == null ? 20 : Math.Max(1, _patches.TimeoutMs);
            _syncDepth++;
            try
            {
                var requestId = peer.RequestStart(MsgType.SyncCall, Lpcp.Map(
                    "fn", binding.Spec.Fn,
                    "ctx", context,
                    "deadline_ms", (long)timeoutMs));
                Counters.Increment("patch.dispatch");
                var completed = peer.TryWaitResult(requestId, TimeSpan.FromMilliseconds(timeoutMs),
                    () => _dispatcher.Drain(32, TimeSpan.FromMilliseconds(2)), out var reply);
                if (!completed)
                {
                    Counters.Increment("patch.timeout");
                    call.Failure = "timeout";
                    return binding.Spec.HasFallback ? binding.Spec.Fallback : null;
                }

                if (!Lpcp.GetBool(reply, "ok"))
                {
                    Counters.Increment("patch.error");
                    var error = Lpcp.GetMap(reply, "err");
                    call.Failure = Lpcp.GetString(error, "msg", "python error");
                    return binding.Spec.HasFallback ? binding.Spec.Fallback : null;
                }

                return reply.TryGetValue("value", out var value) ? value : null;
            }
            catch (Exception ex)
            {
                Counters.Increment("patch.error");
                call.Failure = ex.GetType().Name + ": " + ex.Message;
                return binding.Spec.HasFallback ? binding.Spec.Fallback : null;
            }
            finally
            {
                _syncDepth--;
            }
        }

        private object SafeWire(object value)
        {
            try
            {
                return value == null ? null : _marshaller.ToWire(value);
            }
            catch (Exception)
            {
                return null;
            }
        }

        private object[] SafeWireArray(object[] values)
        {
            if (values == null) return Array.Empty<object>();
            var result = new object[values.Length];
            for (var index = 0; index < values.Length; index++) result[index] = SafeWire(values[index]);
            return result;
        }

        private static void ReplyLater(PeerConnection peer, MsgType type, Dictionary<string, object> payload)
        {
            if (peer == null || peer.IsClosed) return;
            // Never block the main thread on socket IO.
            var ignored = Task.Run(async () =>
            {
                try
                {
                    if (payload == null) await peer.SendAsync(type).ConfigureAwait(false);
                    else await peer.SendAsync(type, payload).ConfigureAwait(false);
                }
                catch
                {
                    // peer went away; the read loop will notice
                }
            });
            GC.KeepAlive(ignored);
        }

        // -- diagnostics -------------------------------------------------------------------

        private Dictionary<string, object> BuildDoctor()
        {
            var doctor = Lpcp.Map(
                "proto", ProtocolInfo.Version,
                "kernel", Plugin.Version,
                "game", _gameVersion + "/" + _unityVersion,
                "stubs_build", _settings.StubsBuild.Value ?? string.Empty,
                "connected", Connected,
                "generation", (long)_generation,
                "pid", (long)Environment.ProcessId,
                "pipe", _pipePrefix ?? string.Empty,
                "caps", AdvertisedCaps(),
                "thread", (long)_dispatcher.OwnerThreadId,
                "types", (long)_types.Count,
                "globals", _globals.Names,
                "handles", HandlesAsMap(),
                "dispatcher", _dispatcher.Stats(),
                "patches", _patches == null ? Lpcp.Map("patches", 0L) : _patches.Stats(),
                "counters", Counters.Snapshot());

            var trace = Trace.Snapshot();
            doctor["trace"] = new System.Collections.Generic.List<object>(trace).ToArray();
            return doctor;
        }

        private Dictionary<string, object> BuildStats()
            => Lpcp.Map(
                "counters", Counters.Snapshot(),
                "handles", HandlesAsMap(),
                "dispatcher", _dispatcher.Stats(),
                "patches", _patches == null ? Lpcp.Map("patches", 0L) : _patches.Stats(),
                "connected", Connected);

        private Dictionary<string, object> HandlesAsMap()
        {
            var stats = _store.Stats;
            return Lpcp.Map(
                "live", (long)stats.Live,
                "pinned", (long)stats.Pinned,
                "allocated", stats.Allocated,
                "released", stats.Released,
                "stale", stats.StaleAccesses,
                "dead", stats.DeadObjects);
        }
    }
}
