using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using LimbusPy.Bridge.Core.Bridge;
using LimbusPy.Bridge.Core.Codec;
using LimbusPy.Bridge.Core.Diagnostics;
using LimbusPy.Bridge.Core.Framing;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Reflect;
using LimbusPy.Bridge.Core.Snapshot;
using LimbusPy.Bridge.Patching;
using LimbusPy.Bridge.Tests.Fixtures;

namespace LimbusPy.Bridge.Tests
{
    /// <summary>
    /// Scripted kernel used by tools/conformance.py. Exercises the same core classes the real
    /// plugin uses (PeerConnection, HandleTable, msgpack codec, CallMarshaller, ReflectionInvoker)
    /// with no Unity dependency - the only difference from the game kernel is what the handles
    /// point at.
    /// </summary>
    internal sealed class TestKernel : IFrameHandler, IRefStore
    {
        private readonly HandleTable _handles = new HandleTable();
        private readonly Dictionary<string, Func<object[], object>> _methods;
        private readonly CancellationTokenSource _cts = new CancellationTokenSource();
        private readonly CallMarshaller _marshaller;
        private readonly ReflectionInvoker _invoker;
        private HarmonyPatchHub _patches;
        private readonly GlobalRegistry _globals;
        private readonly SnapshotService _snapshot;
        private readonly TestBattleObjectManager _battle;

        public TestKernel()
        {
            _marshaller = new CallMarshaller(this);

            // Game-shaped battle behind a manager facade, exposed as global:BattleObjectManager
            // exactly like the real kernel exposes its singletons.
            _battle = BuildBattle();
            _globals = new GlobalRegistry().Register("BattleObjectManager", () => _battle);

            _invoker = new ReflectionInvoker(
                new AssemblyTypeIndex("harness", typeof(TestUnit).Assembly),
                _globals,
                _marshaller);

            _methods = BuildMethods();
            _patches = new HarmonyPatchHub("LimbusPy.Tests.PatchHub", new AssemblyTypeIndex("harness", typeof(TestUnit).Assembly), _marshaller);
            _patches.Dispatch = DispatchToPython;

            _snapshot = new SnapshotService(_globals, _marshaller, this);
        }

        private static TestBattleObjectManager BuildBattle()
        {
            var manager = new TestBattleObjectManager();

            var hero = new TestBattleUnit(101, 1001, 40) { Position = 1, Faction = TestUnitFaction.PLAYER };
            hero.Buffs.Add(new TestBattleBuff("SINKING", 3, 2));
            hero.Buffs.Add(new TestBattleBuff("CHARGE", 5, 1));
            var heroSkill = new TestBattleSkill(601101, "Slash");
            heroSkill.CoinList.Add(new TestBattleCoin(true, 5));
            heroSkill.CoinList.Add(new TestBattleCoin(false, 4));
            hero.Skills.Add(heroSkill);
            manager.Add(hero);

            var ally = new TestBattleUnit(102, 1002, 55) { Position = 2, Faction = TestUnitFaction.PLAYER };
            manager.Add(ally);

            var enemy = new TestBattleUnit(201, 2001, 300) { Position = 4, Faction = TestUnitFaction.ENEMY };
            enemy.Buffs.Add(new TestBattleBuff("VULNERABLE", 2, 3));
            manager.Add(enemy);

            var dying = new TestBattleUnit(202, 2002, 0) { Position = 5, Faction = TestUnitFaction.ENEMY };
            manager.Add(dying);

            return manager;
        }

        public Counters Counters { get; } = new Counters();
        public List<Dictionary<string, object>> LogLines { get; } = new List<Dictionary<string, object>>();
        public List<string> Failures { get; } = new List<string>();
        public int CallsHandled { get; private set; }
        public int RequestFrames { get; private set; }
        public HandleStats Handles => _handles.Stats;
        public ReflectionInvoker Invoker => _invoker;

        public PeerConnection Peer { get; private set; }

        public void Attach(PeerConnection peer)
        {
            Peer = peer;
            // ACT arrives in reply to EVENTBATCH and must not be swallowed by the CALL handler.
            Peer.SetAwaiting(MsgType.Act);
        }

        public void Stop() => _cts.Cancel();

        // -- IRefStore ----------------------------------------------------------------------

        public bool TryResolve(uint handle, out object target)
        {
            if (_handles.TryResolve(handle, out _, out target)) return true;
            target = null;
            return false;
        }

        public uint Store(object target)
        {
            if (target == null) return 0;
            return _handles.Alloc(KindOfEnum(target), target);
        }

        public string KindOf(object target)
        {
            return HandleKindNames.ToWire(KindOfEnum(target));
        }

        private static HandleKind KindOfEnum(object target)
        {
            if (target is TestUnit) return HandleKind.Unit;
            if (target is TestBuff) return HandleKind.Buff;
            return HandleKind.Obj;
        }

        // -- IFrameHandler ------------------------------------------------------------------

        public async Task HandleAsync(PeerConnection peer, Frame frame, CancellationToken cancellationToken)
        {
            switch (frame.Type)
            {
                case MsgType.Call:
                    await HandleCallAsync(peer, frame, cancellationToken).ConfigureAwait(false);
                    return;

                case MsgType.Batch:
                {
                    // 与内核同一条调度路径（Core/Bridge/RequestRouter.cs）。
                    var batchRequest = PeerConnection.DecodePayload(frame);
                    var batchId = Lpcp.GetLong(batchRequest, "id", -1);
                    var router = new LimbusPy.Bridge.Core.Bridge.RequestRouter(_invoker);
                    var routed = router.Dispatch(MsgType.Batch, batchRequest, batchId);
                    await peer.SendAsync(MsgType.CallResult, routed, cancellationToken).ConfigureAwait(false);
                    Counters.Increment("batch");
                    return;
                }

                case MsgType.Ping:
                    Counters.Increment("ping");
                    await peer.SendAsync(MsgType.Pong, cancellationToken).ConfigureAwait(false);
                    return;

                case MsgType.Doctor:
                {
                    // Mirrors KernelHost.BuildDoctor() so limbuspy.devkit.doctor can be exercised.
                    var doctorRequest = PeerConnection.DecodePayload(frame);
                    var doctorId = Lpcp.GetLong(doctorRequest, "id", -1);
                    var stats = _handles.Stats;
                    var doctor = Lpcp.Map(
                        "proto", ProtocolInfo.Version,
                        "kernel", "0.1.0-test",
                        "game", "harness",
                        "stubs_build", "e6312d92",
                        "connected", true,
                        "generation", 1L,
                        "pid", (long)Environment.ProcessId,
                        "pipe", "harness",
                        "caps", new object[] { Caps.ReflectManaged, Caps.ReflectField, Caps.HandleArena, Caps.HandlePinned },
                        "thread", (long)Environment.CurrentManagedThreadId,
                        "types", 3L,
                        "globals", new object[0],
                        "handles", Lpcp.Map(
                            "live", (long)stats.Live,
                            "pinned", (long)stats.Pinned,
                            "allocated", stats.Allocated,
                            "released", stats.Released,
                            "stale", stats.StaleAccesses,
                            "dead", stats.DeadObjects),
                        "dispatcher", Lpcp.Map(
                            "posted", 0L, "executed", 0L, "faulted", 0L, "pending", 0L,
                            "nested_pumps", 0L, "nested_refused", 0L, "owner_thread", 1L),
                        "patches", _patches.Stats(),
                        "counters", Counters.Snapshot(),
                        "trace", new object[0]);
                    await peer.SendAsync(MsgType.CallResult, Lpcp.OkResult(doctorId, doctor), cancellationToken)
                        .ConfigureAwait(false);
                    return;
                }

                case MsgType.Snapshot:
                {
                    var request = PeerConnection.DecodePayload(frame);
                    var requestId = Lpcp.GetLong(request, "id", -1);
                    var battle = _snapshot.Capture();
                    battle["round"] = 3L;
                    battle["wave"] = 1L;
                    Counters.Increment("snapshot");
                    await peer.SendAsync(MsgType.CallResult, Lpcp.OkResult(requestId, battle),
                        cancellationToken).ConfigureAwait(false);
                    return;
                }

                case MsgType.PatchAdd:
                {
                    var request = PeerConnection.DecodePayload(frame);
                    var requestId = Lpcp.GetLong(request, "id", -1);
                    try
                    {
                        var binding = _patches.Apply(PatchSpec.FromWire(request));
                        Counters.Increment("patch.add");
                        await peer.SendAsync(MsgType.CallResult, Lpcp.OkResult(requestId, Lpcp.Map(
                            "id", binding.Id,
                            "target", PatchMethodResolver.Describe(binding.Method),
                            "kind", binding.Spec.Kind == PatchKind.Prefix ? "prefix" : "postfix",
                            "fn", binding.Spec.Fn)), cancellationToken).ConfigureAwait(false);
                    }
                    catch (LpcpFault fault)
                    {
                        await peer.SendAsync(MsgType.CallResult, Lpcp.ErrorResult(requestId, fault.ToError()),
                            cancellationToken).ConfigureAwait(false);
                    }
                    return;
                }

                case MsgType.PatchDel:
                {
                    var request = PeerConnection.DecodePayload(frame);
                    var requestId = Lpcp.GetLong(request, "id", -1);
                    var patchId = Lpcp.GetString(request, "patch");
                    var removed = _patches.Remove(patchId);
                    Counters.Increment("patch.del");
                    var reply = removed
                        ? Lpcp.OkResult(requestId, true)
                        : Lpcp.ErrorResult(requestId, Lpcp.Error(ErrorCode.NotFound, "no patch with id '" + patchId + "'"));
                    await peer.SendAsync(MsgType.CallResult, reply, cancellationToken).ConfigureAwait(false);
                    return;
                }

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
                var ignored = Task.Run(async () =>
                {
                    await Task.Delay((int)delayMs).ConfigureAwait(false);
                    await ReplyOkAsync(peer, capturedId, "late").ConfigureAwait(false);
                }, cancellationToken);
                GC.KeepAlive(ignored);
                return;
            }

            // Invokes a method from a worker thread so the patch pipeline can block on Python
            // (blocking on the read loop would deadlock: the reply arrives on the read loop).
            if (method == "patch.trigger")
            {
                var capturedId = id;
                var targetWire = args.Length > 0 ? args[0] : null;
                var targetName = args.Length > 1 && args[1] is string name ? name : string.Empty;
                var callArgs = args.Length > 2 && args[2] is object[] array ? array : Array.Empty<object>();
                var ignored = Task.Run(async () =>
                {
                    object value = null;
                    string failure = null;
                    try
                    {
                        value = _invoker.Invoke(targetWire, targetName, callArgs);
                    }
                    catch (LpcpFault fault)
                    {
                        failure = fault.Code + ": " + fault.Message;
                    }
                    catch (Exception ex)
                    {
                        failure = ex.GetType().Name + ": " + ex.Message;
                    }

                    await ReplyOkAsync(peer, capturedId, Lcpn(failure, value)).ConfigureAwait(false);
                }, cancellationToken);
                GC.KeepAlive(ignored);
                return;
            }

            // Exercises the blocking (nested-pump) lane used by in-game SYNCALLs. It has to run
            // off the read loop, otherwise the reply it waits for could never be received.
            if (method == "sync.blocking")
            {
                var capturedId = id;
                var ignored = Task.Run(async () =>
                {
                    var pumps = 0;
                    var syncId = peer.RequestStart(MsgType.SyncCall, Lpcp.Map(
                        "fn", "mymod.blocked",
                        "ctx", Lpcp.Map("timing", "OnSucceedAttack"),
                        "deadline_ms", 25L));
                    var completed = peer.TryWaitResult(syncId, TimeSpan.FromSeconds(8),
                        () => Interlocked.Increment(ref pumps), out var reply);
                    object value = null;
                    if (completed && Lpcp.GetBool(reply, "ok") && reply.TryGetValue("value", out var answered)) value = answered;
                    await ReplyOkAsync(peer, capturedId, new Dictionary<string, object>
                    {
                        ["ok"] = completed,
                        ["value"] = value,
                        ["pumps"] = (long)pumps,
                    }).ConfigureAwait(false);
                }, cancellationToken);
                GC.KeepAlive(ignored);
                return;
            }

            try
            {
                var value = Dispatch(method, args);
                await ReplyOkAsync(peer, id, value).ConfigureAwait(false);
            }
            catch (LpcpFault fault)
            {
                Counters.Increment("fault:" + fault.Code);
                await peer.SendAsync(MsgType.CallResult, Lpcp.ErrorResult(id, fault.ToError()), cancellationToken)
                    .ConfigureAwait(false);
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
            var unitHandle = Store(new TestUnit { Name = "event-unit", Hp = 10 });
            var events = new List<object>();
            for (var i = 0; i < count; i++)
            {
                events.Add(new Dictionary<string, object>
                {
                    ["timing"] = "OnSucceedAttack",
                    ["gen"] = 1L,
                    ["ctx"] = Lpcp.Ref(HandleKind.Unit, unitHandle),
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
                throw new LpcpFault(Lpcp.GetString(error, "code", "internal"),
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
                ["bytes"] = _ => new byte[] { 0, 1, 2, 255 },
                ["big"] = args =>
                {
                    var size = (int)ToLong(args, 0, 1024);
                    return new string('z', size);
                },
                ["boom"] = _ => throw new LpcpFault(ErrorCode.Script, "kaboom"),
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
                    if (!_handles.TryResolve(handle, out _, out var target))
                        throw LpcpFault.StaleHandle("handle 0x" + handle.ToString("x") + " is not live");
                    if (!(target is TaggedObject tagged))
                        throw LpcpFault.Marshal("handle target is not a TaggedObject: " +
                                                (target?.GetType().Name ?? "null"));
                    return (object)tagged.Tag;
                },
                ["handles.pin"] = args => (object)_handles.Pin(ResolveRef(args, 0)),
                ["handles.release"] = args => (object)_handles.Release(ResolveRef(args, 0)),
                ["handles.describe"] = args =>
                {
                    var handle = ResolveRef(args, 0);
                    if (!_handles.TryDescribe(handle, out var info))
                        throw LpcpFault.StaleHandle("handle not live");
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

                // patch table -----------------------------------------------------------------
                ["patch.add"] = args =>
                {
                    var payload = new Dictionary<string, object>
                    {
                        ["owner"] = ToStr(args, 0),
                        ["method"] = ToStr(args, 1),
                        ["kind"] = ToStr(args, 2),
                        ["fn"] = ToStr(args, 3),
                        ["sig"] = args.Length > 4 && args[4] is object[] signature ? signature : new object[0],
                    };
                    var binding = _patches.Apply(PatchSpec.FromWire(payload));
                    return Lpcp.Map(
                        "id", binding.Id,
                        "target", PatchMethodResolver.Describe(binding.Method),
                        "kind", binding.Spec.Kind == PatchKind.Prefix ? "prefix" : "postfix",
                        "fn", binding.Spec.Fn);
                },
                ["patch.del"] = args =>
                {
                    var removed = _patches.Remove(ToStr(args, 0));
                    if (!removed) throw LpcpFault.NotFound("no patch with id '" + ToStr(args, 0) + "'");
                    return true;
                },
                ["patch.list"] = _ =>
                {
                    var bindings = _patches.Bindings;
                    var items = new object[bindings.Count];
                    for (var index = 0; index < bindings.Count; index++)
                    {
                        var binding = bindings[index];
                        items[index] = Lpcp.Map(
                            "id", binding.Id,
                            "target", PatchMethodResolver.Describe(binding.Method),
                            "kind", binding.Spec.Kind == PatchKind.Prefix ? "prefix" : "postfix",
                            "fn", binding.Spec.Fn,
                            "calls", binding.Calls);
                    }
                    return items;
                },
                ["patch.stats"] = _ => _patches.Stats(),

                // reflection layer -----------------------------------------------------------
                ["reflect.new_unit"] = args =>
                {
                    var unit = new TestUnit { Name = ToStr(args, 0), Hp = (int)ToLong(args, 1, 40) };
                    return _marshaller.ToWire(unit);
                },
                ["reflect.new_buff"] = args =>
                {
                    var buff = new TestBuff(ToStr(args, 0), (int)ToLong(args, 1, 1), (int)ToLong(args, 2, 1));
                    return _marshaller.ToWire(buff);
                },
                // 5 个标准 reflect 伪方法统一走 Core 的 ReflectOps：与真内核同一份实现，
                // 这样 conformance 覆盖的就不再是"另一套 harness 私有逻辑"。
                ["reflect.invoke"] = args => ReflectOps.Call(_invoker, "reflect.invoke", args),
                ["reflect.get"] = args => ReflectOps.Call(_invoker, "reflect.get", args),
                ["reflect.set"] = args => ReflectOps.Call(_invoker, "reflect.set", args),
                ["reflect.describe"] = args => ReflectOps.Call(_invoker, "reflect.describe", args),
                ["reflect.types"] = args => ReflectOps.Call(_invoker, "reflect.types",
                    args.Length > 0 ? args : new object[] { "Test" }),

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

        /// <summary>Bridges a patch dispatch into Python. Runs on a worker thread (patch.trigger).</summary>
        private object DispatchToPython(PatchCall call)
        {
            var peer = Peer;
            if (peer == null || peer.IsClosed || call == null) return null;

            var context = Lpcp.Map(
                "patch", call.Binding.Id,
                "kind", call.Binding.Spec.Kind == PatchKind.Prefix ? "prefix" : "postfix",
                "target", PatchMethodResolver.Describe(call.Binding.Method),
                "instance", Wire(call.Instance),
                "args", WireArray(call.Args),
                "result", Wire(call.Result));

            var requestId = peer.RequestStart(MsgType.SyncCall, Lpcp.Map(
                "fn", call.Binding.Spec.Fn,
                "ctx", context,
                "deadline_ms", 5000L));
            var completed = peer.TryWaitResult(requestId, TimeSpan.FromSeconds(8), null, out var reply);
            if (!completed || !Lpcp.GetBool(reply, "ok")) return null;
            return reply.TryGetValue("value", out var value) ? value : null;
        }

        private static Dictionary<string, object> Lcpn(string failure, object value)
            => Lpcp.Map("ok", failure == null, "value", value, "failure", failure);

        private object Wire(object value)
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

        private object[] WireArray(object[] values)
        {
            if (values == null) return Array.Empty<object>();
            var result = new object[values.Length];
            for (var index = 0; index < values.Length; index++) result[index] = Wire(values[index]);
            return result;
        }

        private object Dispatch(string method, object[] args)
        {
            if (!_methods.TryGetValue(method, out var fn))
                throw LpcpFault.NotFound("unknown method: " + method);
            return fn(args);
        }

        private static uint ResolveRef(object[] args, int index)
        {
            var value = args.Length > index ? args[index] : null;
            if (!Lpcp.TryReadRef(value, out _, out var handle, out _))
                throw LpcpFault.Marshal("argument " + index + " is not a handle reference");
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
