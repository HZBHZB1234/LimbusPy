using System;
using System.IO;
using BepInEx;
using BepInEx.Logging;
using BepInEx.Unity.IL2CPP;
using Il2CppInterop.Runtime.Injection;
using LimbusPy.Bridge.Core.Bridge;
using LimbusPy.Bridge.Core.Protocol;
using LimbusPy.Bridge.Core.Reflect;
using LimbusPy.Bridge.Core.Snapshot;
using LimbusPy.Bridge.Patching;
using LimbusPy.Bridge.Runtime;
using UnityEngine;

namespace LimbusPy.Bridge
{
    /// <summary>
    /// The LimbusPy kernel.
    ///
    /// Responsibilities (and nothing else - see docs/03-architecture.md):
    ///   * accept the LPCP connection and route messages;
    ///   * run bridge work on the game's main thread;
    ///   * store IL2CPP objects behind handles;
    ///   * own the Python process lifecycle.
    ///
    /// All protocol/codec/handle/reflection logic lives in LimbusPy.Bridge.Core, which is compiled
    /// and tested without Unity (tools/conformance.py).
    /// </summary>
    [BepInPlugin(Guid, Name, Version)]
    [BepInDependency("Lethe", BepInDependency.DependencyFlags.SoftDependency)]
    [BepInDependency("GlitchGames.ModularSkillScripts", BepInDependency.DependencyFlags.SoftDependency)]
    public sealed class Plugin : BasePlugin
    {
        public const string Guid = "LimbusPy.Bridge";
        public const string Name = "LimbusPy.Bridge";
        public const string Version = "0.1.0";

        internal static Plugin Instance { get; private set; }
        internal static BridgeSettings Settings { get; private set; }
        internal static new ManualLogSource Log { get; private set; }
        internal static MainThreadDispatcher Dispatcher { get; private set; }
        internal static KernelHost Host { get; private set; }
        internal static PythonSupervisor Supervisor { get; private set; }
        internal static Il2CppRefStore Store { get; private set; }
        internal static CallMarshaller Marshaller { get; private set; }
        internal static ReflectionInvoker Invoker { get; private set; }
        internal static InteropTypeIndex Types { get; private set; }
        internal static HarmonyPatchHub Patches { get; private set; }
        internal static GlobalRegistry Globals { get; private set; }

        public override void Load()
        {
            Instance = this;
            Log = new ManualLogSource("LimbusPy");
            BepInEx.Logging.Logger.Sources.Add(Log);

            var pluginDirectory = ResolvePluginDirectory();
            Settings = new BridgeSettings(Config, pluginDirectory);
            Settings.EnsureDirectories();

            void LogLine(string message)
            {
                if (Settings.Verbose.Value) Log.LogInfo(message);
                else Log.LogDebug(message);
            }

            Log.LogInfo("LimbusPy kernel " + Version + " (proto " + ProtocolInfo.Version + ")");
            Log.LogInfo("plugin dir: " + pluginDirectory);

            // 1. Main-thread dispatcher (drained by PumpBehaviour.Update).
            Dispatcher = new MainThreadDispatcher
            {
                NestedBudgetMs = Math.Max(1, Settings.SyncCallTimeoutMs.Value),
                MaxNestedDepth = Math.Max(0, Settings.MaxNestedDepth.Value),
            };

            // 2. The Unity pump - the only Unity object the kernel owns.
            ClassInjector.RegisterTypeInIl2Cpp<PumpBehaviour>();
            PumpBehaviour.Ensure();
            PumpBehaviour.Dispatcher = Dispatcher;
            PumpBehaviour.DrainBatch = Math.Max(1, Settings.DrainBatch.Value);
            PumpBehaviour.DrainBudget = TimeSpan.FromMilliseconds(Math.Max(0.1, Settings.FrameBudgetMs.Value));

            // 3. Handles, marshalling, reflection.
            var table = new HandleTable(Il2CppRefStore.IsAlive);
            Store = new Il2CppRefStore(table);
            Marshaller = new CallMarshaller(Store);
            Types = new InteropTypeIndex(Settings.StubsBuild.Value, Path.Combine(Paths.BepInExRootPath, "interop"));
            Globals = new GlobalRegistry();
            Invoker = new ReflectionInvoker(Types, Globals, Marshaller, EnsureMainThread);

            // 4. Patch hub (table-driven Harmony patching driven by Python).
            Patches = new HarmonyPatchHub("LimbusPy.PatchHub", Types, Marshaller, EnsureMainThread)
            {
                TimeoutMs = Math.Max(1, Settings.SyncCallTimeoutMs.Value),
            };

            // 5. Battle snapshot service + bridge host.
            var snapshot = new SnapshotService(Globals, Marshaller, Store);
            Host = new KernelHost(Settings, LogLine, Dispatcher, Marshaller, Invoker, Store, Types, Globals,
                Patches, snapshot);
            Host.SetGameInfo(SafeApplicationVersion(), SafeUnityVersion());
            Host.Start();

            // 6. Sweep dead/expired handles periodically (every ~2 s at 60 fps).
            var frame = 0;
            PumpBehaviour.OnTick = () =>
            {
                frame++;
                if (frame % 120 != 0) return;
                try
                {
                    var removed = Store.Sweep();
                    if (removed > 0 && Settings.Verbose.Value) Log.LogDebug("swept " + removed + " handle(s)");
                }
                catch (Exception ex)
                {
                    Log.LogDebug("handle sweep failed: " + ex.Message);
                }
            };

            // 7. Python process.
            Supervisor = new PythonSupervisor(Settings, LogLine, Host.PipePrefix);
            Supervisor.Start();

            Log.LogInfo("kernel ready; waiting for python on " + (Host.PipePrefix ?? "<tcp>"));
        }

        public override bool Unload()
        {
            Shutdown("plugin unloaded");
            return true;
        }

        internal static void Shutdown(string reason)
        {
            try
            {
                Log?.LogInfo("shutting down: " + reason);
                Supervisor?.Stop();
                Host?.Stop();
                Patches?.UnpatchAll();
                PumpBehaviour.OnTick = null;
            }
            catch (Exception ex)
            {
                Log?.LogWarning("shutdown failed: " + ex.Message);
            }
        }

        /// <summary>Refuses engine access from any thread but the pump's owner thread.</summary>
        internal static void EnsureMainThread()
        {
            if (Dispatcher == null || Dispatcher.IsOwnerThread) return;
            throw LpcpFault.WrongThread(
                "engine access from thread " + Environment.CurrentManagedThreadId +
                " but the main thread is " + Dispatcher.OwnerThreadId);
        }

        private string ResolvePluginDirectory()
        {
            try
            {
                var location = typeof(Plugin).Assembly.Location;
                if (!string.IsNullOrEmpty(location))
                {
                    var directory = Path.GetDirectoryName(location);
                    if (!string.IsNullOrEmpty(directory)) return directory;
                }
            }
            catch
            {
                // fall through to the conventional location
            }

            return Path.Combine(Paths.PluginPath, "LimbusPy");
        }

        internal static void LogError(string message)
        {
            try
            {
                Log?.LogError(message);
            }
            catch
            {
                // logging must never throw
            }
        }

        internal static void LogWarn(string message)
        {
            try
            {
                Log?.LogWarning(message);
            }
            catch
            {
                // logging must never throw
            }
        }

        private static string SafeApplicationVersion()
        {
            try
            {
                return Application.version ?? string.Empty;
            }
            catch
            {
                return string.Empty;
            }
        }

        private static string SafeUnityVersion()
        {
            try
            {
                return Application.unityVersion ?? string.Empty;
            }
            catch
            {
                return string.Empty;
            }
        }
    }
}
