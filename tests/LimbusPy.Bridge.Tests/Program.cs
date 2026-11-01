using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using LimbusPy.Bridge.Core.Bridge;
using LimbusPy.Bridge.Core.Codec;
using LimbusPy.Bridge.Core.Framing;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Transport;

namespace LimbusPy.Bridge.Tests
{
    /// <summary>
    /// Two purposes, both Unity-free:
    ///   * <c>golden</c> - emit the canonical msgpack table so the Python side can compare bytes.
    ///   * <c>serve</c>  - act as the kernel on a real named pipe (or TCP) for the conformance run.
    /// </summary>
    internal static class Program
    {
        private const string KernelVersion = "0.1.0-test";

        private static int Main(string[] args)
        {
            // HarmonyX (0Harmony 2.10.2) references MonoMod.RuntimeDetour 22.3.23.4 while BepInEx
            // 6.0.755 ships 22.7.31.1. In-game BepInEx's own resolution hides that; a standalone
            // harness has to roll the version forward itself, or Harmony's static initialiser
            // throws before any patch is applied.
            AppDomain.CurrentDomain.AssemblyResolve += ResolveFromBaseDirectory;
            System.Runtime.Loader.AssemblyLoadContext.Default.Resolving += (_, name) => LoadSibling(name.Name);

            return MainAsync(args).GetAwaiter().GetResult();
        }

        private static System.Reflection.Assembly ResolveFromBaseDirectory(object sender, ResolveEventArgs args)
            => LoadSibling(new System.Reflection.AssemblyName(args.Name).Name);

        private static System.Reflection.Assembly LoadSibling(string simpleName)
        {
            if (string.IsNullOrEmpty(simpleName)) return null;
            var path = Path.Combine(AppContext.BaseDirectory, simpleName + ".dll");
            if (!File.Exists(path)) return null;
            try
            {
                return System.Reflection.Assembly.LoadFrom(path);
            }
            catch
            {
                return null;
            }
        }

        private static async Task<int> MainAsync(string[] args)
        {
            if (args.Length == 0)
            {
                Console.Error.WriteLine(Usage());
                return 2;
            }

            try
            {
                switch (args[0])
                {
                    case "golden":
                        return RunGolden();
                    case "serve":
                        return await RunServeAsync(ParseOptions(args)).ConfigureAwait(false);
                    case "--help":
                    case "-h":
                    case "help":
                        Console.WriteLine(Usage());
                        return 0;
                    default:
                        Console.Error.WriteLine("unknown mode: " + args[0]);
                        Console.Error.WriteLine(Usage());
                        return 2;
                }
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("FATAL " + ex);
                return 4;
            }
        }

        private static string Usage()
        {
            return "usage:\n" +
                   "  LimbusPy.Bridge.Tests golden\n" +
                   "  LimbusPy.Bridge.Tests serve --pipe <name> [--events N] [--sync FN] [--out FILE] [--max-seconds S] [--verbose]\n" +
                   "  LimbusPy.Bridge.Tests serve --tcp <port> [...]";
        }

        // -- golden ------------------------------------------------------------------------

        private static int RunGolden()
        {
            var hex = new Dictionary<string, object>();
            var canonical = new Dictionary<string, object>();
            foreach (var (label, value, isCanonical) in Golden.Cases)
            {
                var writer = new MsgPackWriter();
                writer.WriteValue(value);
                hex[label] = Convert.ToHexString(writer.ToArray()).ToLowerInvariant();
                canonical[label] = isCanonical;
            }

            var payload = new Dictionary<string, object>
            {
                ["hex"] = hex,
                ["canonical"] = canonical,
            };
            Console.Out.WriteLine("GOLDEN " + Json.Write(payload));
            return 0;
        }

        // -- serve -------------------------------------------------------------------------

        private sealed class Options
        {
            public string Pipe;
            public int TcpPort = -1;
            public int Events;
            public string SyncFn;
            public string OutPath;
            public double MaxSeconds = 60;
            public bool Verbose;
            public bool ExitAfterScript;
        }

        private static Options ParseOptions(string[] args)
        {
            var options = new Options();
            for (var i = 1; i < args.Length; i++)
            {
                switch (args[i])
                {
                    case "--pipe":
                        options.Pipe = args[++i];
                        break;
                    case "--tcp":
                        options.TcpPort = int.Parse(args[++i]);
                        break;
                    case "--events":
                        options.Events = int.Parse(args[++i]);
                        break;
                    case "--sync":
                        options.SyncFn = args[++i];
                        break;
                    case "--out":
                        options.OutPath = args[++i];
                        break;
                    case "--max-seconds":
                        options.MaxSeconds = double.Parse(args[++i]);
                        break;
                    case "--verbose":
                        options.Verbose = true;
                        break;
                    case "--exit-after-script":
                        options.ExitAfterScript = true;
                        break;
                    default:
                        throw new ArgumentException("unknown option: " + args[i]);
                }
            }

            if (string.IsNullOrEmpty(options.Pipe) && options.TcpPort < 0)
                throw new ArgumentException("serve requires --pipe <name> or --tcp <port>");
            return options;
        }

        private static void Log(Options options, string message)
        {
            Console.Error.WriteLine("[harness] " + message);
        }

        private static async Task<int> RunServeAsync(Options options)
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(options.MaxSeconds));

            Stream stream;
            System.Net.Sockets.TcpListener listener = null;
            if (!string.IsNullOrEmpty(options.Pipe))
            {
                Log(options, "listening on pipe pair " + options.Pipe + "{.toPy,.fromPy}");
                // Announce early so the driving script can start connecting.
                Console.Out.WriteLine("PIPE " + options.Pipe);
                Console.Out.Flush();
                stream = await NamedPipeTransport.AcceptPairAsync(options.Pipe, cts.Token).ConfigureAwait(false);
            }
            else
            {
                listener = TcpTransport.CreateListener(options.TcpPort);
                var port = TcpTransport.PortOf(listener);
                Log(options, "listening on tcp 127.0.0.1:" + port);
                Console.Out.WriteLine("PORT " + port);
                Console.Out.Flush();
                stream = await TcpTransport.AcceptAsync(listener, cts.Token).ConfigureAwait(false);
            }

            Log(options, "client connected");
            var kernel = new TestKernel();
            var peer = new PeerConnection(stream, kernel, message => Log(options, message))
            {
                TraceFrames = options.Verbose,
            };
            kernel.Attach(peer);
            peer.SetAwaiting(MsgType.Ready);
            peer.Start();

            var failures = new List<string>();
            var summary = new Dictionary<string, object>
            {
                ["mode"] = "serve",
                ["pipe"] = options.Pipe ?? string.Empty,
            };
            var exitCode = 0;

            try
            {
                await peer.SendAsync(MsgType.Hello, Lpcp.Hello(
                    KernelVersion,
                    "e6312d92",
                    "1.84.0-harness",
                    new[] { Caps.ReflectManaged, Caps.HandleArena, Caps.HandlePinned, Caps.HotReload, Caps.BudgetFrame }),
                    cts.Token).ConfigureAwait(false);
                Log(options, "HELLO sent");

                Frame readyFrame;
                try
                {
                    readyFrame = await peer.WaitForAsync(MsgType.Ready, TimeSpan.FromSeconds(3)).ConfigureAwait(false);
                }
                catch (Exception ex)
                {
                    failures.Add("handshake: no READY (" + ex.Message + ")");
                    exitCode = 3;
                    return exitCode;
                }

                var ready = readyFrame.decode();
                var peerProto = Lpcp.GetString(ready, "proto");
                summary["ready"] = ready;
                if (!ProtocolInfo.IsCompatible(peerProto))
                {
                    failures.Add("handshake: incompatible protocol " + peerProto);
                    await peer.SendAsync(MsgType.Error, Lpcp.Map(
                        "code", ErrorCode.Proto,
                        "msg", "kernel speaks " + ProtocolInfo.Version + ", python speaks " + peerProto,
                        "fatal", true), CancellationToken.None).ConfigureAwait(false);
                    exitCode = 3;
                    return exitCode;
                }

                Log(options, "handshake ok: py=" + Lpcp.GetString(ready, "py") + " mods=" + Lpcp.GetArray(ready, "mods").Length);

                if (options.Events > 0)
                {
                    try
                    {
                        var actions = await kernel.EmitEventsAsync(options.Events, TimeSpan.FromSeconds(5)).ConfigureAwait(false);
                        summary["events"] = new Dictionary<string, object>
                        {
                            ["sent"] = (long)options.Events,
                            ["actions"] = (long)actions.Count,
                            ["first"] = actions.Count > 0 ? actions[0] : null,
                        };
                        Log(options, "event batch round-trip ok: " + actions.Count + " actions");
                    }
                    catch (Exception ex)
                    {
                        failures.Add("events: " + ex.Message);
                    }
                }

                if (!string.IsNullOrEmpty(options.SyncFn))
                {
                    try
                    {
                        var value = await kernel.SyncAsync(options.SyncFn, TimeSpan.FromSeconds(5)).ConfigureAwait(false);
                        summary["sync"] = new Dictionary<string, object>
                        {
                            ["fn"] = options.SyncFn,
                            ["value"] = value,
                        };
                        Log(options, "sync call ok: " + Json.Write(value));
                    }
                    catch (Exception ex)
                    {
                        failures.Add("sync: " + ex.Message);
                    }
                }

                if (options.ExitAfterScript)
                {
                    Log(options, "script complete; closing the connection");
                    await peer.CloseAsync("script complete").ConfigureAwait(false);
                }
                else
                {
                    // Serve until the client goes away (or the watchdog fires).
                    await peer.WaitForCloseAsync().ConfigureAwait(false);
                    Log(options, "peer closed");
                }
            }
            finally
            {
                summary["calls"] = (long)kernel.CallsHandled;
                summary["requests"] = (long)kernel.RequestFrames;
                summary["logs"] = (long)kernel.LogLines.Count;
                summary["counters"] = kernel.Counters.Snapshot();
                summary["ok"] = failures.Count == 0;
                summary["failures"] = failures;
                await WriteSummaryAsync(options, summary).ConfigureAwait(false);
                await peer.DisposeAsync().ConfigureAwait(false);
                listener?.Stop();
            }

            return exitCode != 0 ? exitCode : (failures.Count == 0 ? 0 : 5);
        }

        private static async Task WriteSummaryAsync(Options options, Dictionary<string, object> summary)
        {
            var json = Json.Write(summary);
            Console.Out.WriteLine("SUMMARY " + json);
            Console.Out.Flush();
            if (!string.IsNullOrEmpty(options.OutPath))
            {
                await File.WriteAllTextAsync(options.OutPath, json).ConfigureAwait(false);
            }
        }
    }

    internal static class FrameExtensions
    {
        public static Dictionary<string, object> decode(this Frame frame)
            => frame.HasPayload ? PeerConnection.DecodePayload(frame) : new Dictionary<string, object>();
    }
}

