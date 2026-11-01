using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace LimbusPy.Bridge.Core.Transport
{
    /// <summary>
    /// A duplex stream built from two one-way streams.
    ///
    /// Why this exists: on Windows a named-pipe handle opened by the Python client through
    /// <c>open(path, "r+b")</c> is a *synchronous* handle, and a pending blocking ReadFile makes a
    /// concurrent WriteFile on the same handle fail with ERROR_INVALID_PARAMETER (reproduced with
    /// plain Python, no bridge involved). Using one pipe per direction removes the shared handle
    /// entirely, keeps the "no TCP port, current-user ACL, no firewall prompt" properties of named
    /// pipes, and also removes the read/write lock contention.
    /// </summary>
    public sealed class SplitDuplexStream : Stream
    {
        private readonly Stream _rx;
        private readonly Stream _tx;
        private readonly Action _onDispose;
        private int _disposed;

        public SplitDuplexStream(Stream rx, Stream tx, Action onDispose = null)
        {
            _rx = rx ?? throw new ArgumentNullException(nameof(rx));
            _tx = tx ?? throw new ArgumentNullException(nameof(tx));
            _onDispose = onDispose;
        }

        public override bool CanRead => _disposed == 0 && _rx.CanRead;
        public override bool CanWrite => _disposed == 0 && _tx.CanWrite;
        public override bool CanSeek => false;
        public override bool CanTimeout => false;
        public override long Length => throw new NotSupportedException();

        public override long Position
        {
            get => throw new NotSupportedException();
            set => throw new NotSupportedException();
        }

        public override int Read(byte[] buffer, int offset, int count) => _rx.Read(buffer, offset, count);

        public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
            => _rx.ReadAsync(buffer, offset, count, cancellationToken);

        public override void Write(byte[] buffer, int offset, int count) => _tx.Write(buffer, offset, count);

        public override Task WriteAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
            => _tx.WriteAsync(buffer, offset, count, cancellationToken);

        public override void Flush() => _tx.Flush();

        public override Task FlushAsync(CancellationToken cancellationToken) => _tx.FlushAsync(cancellationToken);

        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

        public override void SetLength(long value) => throw new NotSupportedException();

        protected override void Dispose(bool disposing)
        {
            if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
            try { if (disposing) _rx.Dispose(); } catch { /* best effort */ }
            try { if (disposing) _tx.Dispose(); } catch { /* best effort */ }
            try { _onDispose?.Invoke(); } catch { /* best effort */ }
            base.Dispose(disposing);
        }
    }
}
