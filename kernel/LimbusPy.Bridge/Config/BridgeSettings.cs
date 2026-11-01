using System;
using System.IO;
using BepInEx.Configuration;
using BepInEx.Unity.IL2CPP;
using LimbusPy.Bridge.Runtime;

namespace LimbusPy.Bridge
{
    /// <summary>Kernel settings, persisted by BepInEx at <c>BepInEx/config/LimbusPy.Bridge.cfg</c>.</summary>
    internal sealed class BridgeSettings
    {
        public BridgeSettings(ConfigFile config, string pluginDir)
        {
            PluginDirectory = pluginDir;

            PythonMode = config.Bind(
                "python", "Mode", "spawn",
                "spawn = the kernel starts Python itself; attach = connect to an already running Python (limbuspy.bootstrap).");

            PythonExecutable = config.Bind(
                "python", "Executable", "",
                "Path to python.exe. Empty = auto-detect: bundled runtime first, then system Python on PATH.");

            PythonArguments = config.Bind(
                "python", "Arguments", "-m limbuspy.bootstrap --attach-kernel",
                "Arguments passed to Python. --attach-kernel makes the bootstrap connect back to this process.");

            Transport = config.Bind(
                "transport", "Kind", "pipe",
                "pipe (recommended) or tcp (debugging only, loopback).");

            TcpPort = config.Bind(
                "transport", "TcpPort", 0,
                "TCP port for Kind=tcp. 0 = pick a free port.");

            HandshakeTimeoutMs = config.Bind(
                "transport", "HandshakeTimeoutMs", 5000,
                "How long the kernel waits for READY before giving up on a connection.");

            FrameBudgetMs = config.Bind(
                "runtime", "FrameBudgetMs", 2.0,
                "Main-thread budget spent draining bridge work per frame. Exceeding it defers work to the next frame.");

            DrainBatch = config.Bind(
                "runtime", "DrainBatch", 128,
                "Maximum queue items processed per frame regardless of budget.");

            SyncCallTimeoutMs = config.Bind(
                "runtime", "SyncCallTimeoutMs", 20,
                "Default deadline for a kernel->Python synchronous call (nested pump).");

            MaxNestedDepth = config.Bind(
                "runtime", "MaxNestedDepth", 1,
                "Nested-pump depth. 1 refuses re-entry, which turns Python->C#->Python cycles into an error instead of a hang.");

            RestartOnExit = config.Bind(
                "supervisor", "RestartOnExit", true,
                "Restart Python when it exits unexpectedly.");

            RestartBackoffMs = config.Bind(
                "supervisor", "RestartBackoffMs", 2000,
                "Delay before restarting Python; doubles up to MaxRestartBackoffMs.");

            MaxRestartBackoffMs = config.Bind(
                "supervisor", "MaxRestartBackoffMs", 30000,
                "Upper bound of the restart backoff.");

            PassThroughOnPythonDeath = config.Bind(
                "supervisor", "PassThroughOnPythonDeath", true,
                "Keep the game running with scripting disabled instead of blocking gameplay.");

            Verbose = config.Bind(
                "logging", "Verbose", false,
                "Log every bridge frame (very chatty; for debugging the bridge itself).");

            LogPythonOutput = config.Bind(
                "logging", "LogPythonOutput", true,
                "Forward Python stdout/stderr into the BepInEx log.");

            StubsBuild = config.Bind(
                "python", "StubsBuild", "",
                "Game build hash the Python stubs were generated for. Empty = unknown (kernel logs a hint).");
        }

        public string PluginDirectory { get; }
        public ConfigEntry<string> PythonMode { get; }
        public ConfigEntry<string> PythonExecutable { get; }
        public ConfigEntry<string> PythonArguments { get; }
        public ConfigEntry<string> Transport { get; }
        public ConfigEntry<int> TcpPort { get; }
        public ConfigEntry<int> HandshakeTimeoutMs { get; }
        public ConfigEntry<double> FrameBudgetMs { get; }
        public ConfigEntry<int> DrainBatch { get; }
        public ConfigEntry<int> SyncCallTimeoutMs { get; }
        public ConfigEntry<int> MaxNestedDepth { get; }
        public ConfigEntry<bool> RestartOnExit { get; }
        public ConfigEntry<int> RestartBackoffMs { get; }
        public ConfigEntry<int> MaxRestartBackoffMs { get; }
        public ConfigEntry<bool> PassThroughOnPythonDeath { get; }
        public ConfigEntry<bool> Verbose { get; }
        public ConfigEntry<bool> LogPythonOutput { get; }
        public ConfigEntry<string> StubsBuild { get; }

        public string ModsDirectory => Path.Combine(PluginDirectory, "mods");
        public string PythonDirectory => Path.Combine(PluginDirectory, "python");
        public string StateDirectory => Path.Combine(PluginDirectory, ".state");
        public string BundledPython => Path.Combine(PythonDirectory, "python.exe");

        public TimeSpan SyncTimeout => TimeSpan.FromMilliseconds(Math.Max(1, SyncCallTimeoutMs.Value));

        public void EnsureDirectories()
        {
            Directory.CreateDirectory(ModsDirectory);
            Directory.CreateDirectory(StateDirectory);
        }
    }
}
