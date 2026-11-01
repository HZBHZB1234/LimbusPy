using System;
using System.Diagnostics;
using System.IO;
using System.Text;

namespace LimbusPy.Bridge.Runtime
{
    /// <summary>
    /// Starts and supervises the Python process.
    ///
    /// Two deployment modes (see BridgeSettings): the kernel spawns Python, or the user starts it
    /// out-of-band (attach). Supervising only covers the spawn mode; attach mode simply waits for
    /// a connection.
    ///
    /// Crash policy: restart with exponential backoff, and if Python keeps dying leave the game
    /// running (pass-through) rather than blocking gameplay.
    /// </summary>
    internal sealed class PythonSupervisor
    {
        private readonly BridgeSettings _settings;
        private readonly Action<string> _log;
        private readonly string _pipePrefix;
        private readonly object _lock = new object();

        private Process _process;
        private int _attempt;
        private volatile bool _stopping;

        public PythonSupervisor(BridgeSettings settings, Action<string> log, string pipePrefix)
        {
            _settings = settings;
            _log = log;
            _pipePrefix = pipePrefix;
        }

        public bool SpawnMode =>
            string.Equals(_settings.PythonMode.Value, "spawn", StringComparison.OrdinalIgnoreCase);

        public bool Running
        {
            get
            {
                lock (_lock)
                {
                    try
                    {
                        return _process != null && !_process.HasExited;
                    }
                    catch
                    {
                        return false;
                    }
                }
            }
        }

        public int? ProcessId
        {
            get
            {
                lock (_lock)
                {
                    try
                    {
                        return _process != null && !_process.HasExited ? _process.Id : (int?)null;
                    }
                    catch
                    {
                        return null;
                    }
                }
            }
        }

        public void Start()
        {
            if (!SpawnMode)
            {
                _log("python mode = attach; waiting for an external limbuspy.bootstrap");
                return;
            }

            Launch();
        }

        public void Stop()
        {
            _stopping = true;
            Process process;
            lock (_lock) process = _process;
            if (process == null) return;

            try
            {
                if (!process.HasExited)
                {
                    _log("stopping python (pid " + process.Id + ")");
                    process.Kill(true);
                }
            }
            catch (Exception ex)
            {
                _log("python stop failed: " + ex.Message);
            }
        }

        // -- launch ------------------------------------------------------------------------

        private void Launch()
        {
            var executable = ResolvePython();
            if (executable == null)
            {
                _log("ERROR: no python interpreter found. Put the bundled runtime at '" +
                     _settings.BundledPython + "', set python.Executable in the config, or install Python on PATH.");
                return;
            }

            var arguments = BuildArguments(executable);
            var startInfo = new ProcessStartInfo
            {
                FileName = executable,
                Arguments = arguments,
                WorkingDirectory = _settings.PluginDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };

            var pythonPath = _settings.PythonDirectory;
            if (Directory.Exists(pythonPath))
            {
                var existing = Environment.GetEnvironmentVariable("PYTHONPATH");
                startInfo.Environment["PYTHONPATH"] = string.IsNullOrEmpty(existing)
                    ? pythonPath
                    : pythonPath + Path.PathSeparator + existing;
            }

            startInfo.Environment["PYTHONUNBUFFERED"] = "1";
            startInfo.Environment["PYTHONUTF8"] = "1";
            startInfo.Environment["LIMBUSPY_KERNEL_PID"] = Environment.ProcessId.ToString();
            startInfo.Environment["LIMBUSPY_PIPE_PREFIX"] = _pipePrefix;
            startInfo.Environment["LIMBUSPY_PLUGIN_DIR"] = _settings.PluginDirectory;
            startInfo.Environment["LIMBUSPY_MODS_DIR"] = _settings.ModsDirectory;
            startInfo.Environment["LIMBUSPY_STATE_DIR"] = _settings.StateDirectory;
            startInfo.Environment["LIMBUSPY_PROTOCOL"] = "1.0";

            try
            {
                var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
                process.OutputDataReceived += (_, args) =>
                {
                    if (args.Data != null && _settings.LogPythonOutput.Value) _log("[py-out] " + args.Data);
                };
                process.ErrorDataReceived += (_, args) =>
                {
                    if (args.Data != null && _settings.LogPythonOutput.Value) _log("[py-err] " + args.Data);
                };
                process.Exited += (_, __) => OnExited(process);

                lock (_lock)
                {
                    _process = process;
                }

                process.Start();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                _attempt = 0;
                _log("python started (pid " + process.Id + ", " + executable + ")");
            }
            catch (Exception ex)
            {
                _log("ERROR: could not start python: " + ex.Message);
                ScheduleRestart();
            }
        }

        private string BuildArguments(string executable)
        {
            var userArguments = _settings.PythonArguments.Value ?? string.Empty;
            var builder = new StringBuilder();
            builder.Append(userArguments);

            if (userArguments.IndexOf("--pipe", StringComparison.OrdinalIgnoreCase) < 0)
            {
                builder.Append(" --pipe ").Append(_pipePrefix);
            }

            if (userArguments.IndexOf("--mods", StringComparison.OrdinalIgnoreCase) < 0)
            {
                builder.Append(" --mods \"").Append(_settings.ModsDirectory).Append('"');
            }

            if (userArguments.IndexOf("--state", StringComparison.OrdinalIgnoreCase) < 0)
            {
                builder.Append(" --state \"").Append(_settings.StateDirectory).Append('"');
            }

            return builder.ToString();
        }

        private string ResolvePython()
        {
            var configured = _settings.PythonExecutable.Value;
            if (!string.IsNullOrWhiteSpace(configured))
            {
                return File.Exists(configured) ? configured : configured; // let the OS report the failure
            }

            if (File.Exists(_settings.BundledPython)) return _settings.BundledPython;

            // "system python" build of LimbusPy: no bundled runtime, rely on PATH.
            var pathVariable = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
            foreach (var directory in pathVariable.Split(Path.PathSeparator))
            {
                if (string.IsNullOrWhiteSpace(directory)) continue;
                foreach (var candidate in new[] { "python.exe", "python3.exe" })
                {
                    try
                    {
                        var full = Path.Combine(directory.Trim(), candidate);
                        if (File.Exists(full)) return full;
                    }
                    catch
                    {
                        // malformed PATH entry
                    }
                }
            }

            return null;
        }

        // -- restart policy ----------------------------------------------------------------

        private void OnExited(Process process)
        {
            int exitCode;
            try
            {
                exitCode = process.ExitCode;
            }
            catch
            {
                exitCode = -1;
            }

            _log("python exited (code " + exitCode + ")");

            if (_stopping || !_settings.RestartOnExit.Value) return;
            if (_settings.PassThroughOnPythonDeath.Value)
            {
                _log("scripting disabled; the game keeps running (pass-through)");
            }

            ScheduleRestart();
        }

        private void ScheduleRestart()
        {
            if (_stopping || !_settings.RestartOnExit.Value) return;

            _attempt++;
            var backoff = _settings.RestartBackoffMs.Value * (long)Math.Pow(2, Math.Min(6, _attempt - 1));
            backoff = Math.Min(backoff, _settings.MaxRestartBackoffMs.Value);
            _log("restarting python in " + backoff + " ms (attempt " + _attempt + ")");

            var ignored = System.Threading.Tasks.Task.Run(async () =>
            {
                await System.Threading.Tasks.Task.Delay((int)backoff).ConfigureAwait(false);
                if (_stopping) return;
                Launch();
            });
            GC.KeepAlive(ignored);
        }
    }
}
