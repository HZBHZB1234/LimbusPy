using System;
using System.IO;
using System.IO.Pipes;
using System.Threading;
using System.Threading.Tasks;

namespace LimbusPy.Bridge.Core.Transport
{
    /// <summary>
    /// Named-pipe transport (primary).
    ///
    /// Two one-way pipes are used - <c>&lt;prefix&gt;.toPy</c> (kernel writes) and
    /// <c>&lt;prefix&gt;.fromPy</c> (kernel reads) - because a single duplex handle cannot be used
    /// for a pending blocking read and a write at the same time from separate threads on the
    /// Python side (see SplitDuplexStream for the details).
    ///
    /// PipeOptions.CurrentUserOnly restricts connections to the current Windows account without
    /// pulling in System.IO.Pipes.AccessControl.
    /// </summary>
    public static class NamedPipeTransport
    {
        public const string Prefix = "limbuspy.";
        public const string ToPySuffix = ".toPy";
        public const string FromPySuffix = ".fromPy";

        /// <summary>Base name for a game process: <c>limbuspy.&lt;pid&gt;</c>.</summary>
        public static string NameForProcess(int processId) => Prefix + processId;

        public static string ToPyName(string prefix) => prefix + ToPySuffix;

        public static string FromPyName(string prefix) => prefix + FromPySuffix;

        public static NamedPipeServerStream CreateServer(string pipeName, PipeDirection direction)
        {
            return new NamedPipeServerStream(
                pipeName,
                direction,
                1,
                PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
        }

        /// <summary>
        /// Creates both pipe instances, waits for the client to connect to both, and returns a
        /// duplex stream. Both instances are created before either wait so the client can connect
        /// in any order.
        /// </summary>
        public static async Task<Stream> AcceptPairAsync(string prefix, CancellationToken cancellationToken)
        {
            var toPy = CreateServer(ToPyName(prefix), PipeDirection.Out);
            var fromPy = CreateServer(FromPyName(prefix), PipeDirection.In);
            try
            {
                // Both instances exist before either wait, so the client may connect in any order.
                await Task.WhenAll(
                    toPy.WaitForConnectionAsync(cancellationToken),
                    fromPy.WaitForConnectionAsync(cancellationToken)).ConfigureAwait(false);
            }
            catch
            {
                toPy.Dispose();
                fromPy.Dispose();
                throw;
            }

            // Ownership moves to the duplex wrapper: it disposes both pipes when the connection ends.
            return new SplitDuplexStream(fromPy, toPy);
        }

        /// <summary>Single-pipe accept, kept for diagnostics and for future use.</summary>
        public static async Task<Stream> AcceptAsync(string pipeName, CancellationToken cancellationToken)
        {
            var server = CreateServer(pipeName, PipeDirection.InOut);
            try
            {
                await server.WaitForConnectionAsync(cancellationToken).ConfigureAwait(false);
                return server;
            }
            catch
            {
                server.Dispose();
                throw;
            }
        }

        /// <summary>Client side (harness/tests only).</summary>
        public static Stream Connect(string pipeName, int timeoutMs = 5000)
        {
            var deadline = Environment.TickCount64 + timeoutMs;
            Exception last = null;
            while (Environment.TickCount64 < deadline)
            {
                var client = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
                try
                {
                    client.Connect(250);
                    return client;
                }
                catch (Exception ex)
                {
                    last = ex;
                    client.Dispose();
                    Thread.Sleep(25);
                }
            }

            throw new IOException("could not connect to pipe '" + pipeName + "' within " + timeoutMs + " ms", last);
        }
    }

    /// <summary>TCP fallback transport, loopback only.</summary>
    public static class TcpTransport
    {
        public static System.Net.Sockets.TcpListener CreateListener(int port = 0)
        {
            var listener = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, port);
            listener.Start();
            return listener;
        }

        public static async Task<Stream> AcceptAsync(System.Net.Sockets.TcpListener listener, CancellationToken cancellationToken)
        {
            var client = await listener.AcceptTcpClientAsync(cancellationToken).ConfigureAwait(false);
            client.NoDelay = true;
            return client.GetStream();
        }

        public static int PortOf(System.Net.Sockets.TcpListener listener)
            => ((System.Net.IPEndPoint)listener.LocalEndpoint).Port;
    }
}
