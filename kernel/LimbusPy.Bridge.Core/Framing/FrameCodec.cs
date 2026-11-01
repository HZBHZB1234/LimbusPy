using System;
using System.Buffers.Binary;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace LimbusPy.Bridge.Core.Framing
{
    /// <summary>LPCP message types. Values are frozen by docs/01-protocol.md §3.</summary>
    public enum MsgType : byte
    {
        Hello = 0x01,
        Ready = 0x02,
        Call = 0x03,
        CallResult = 0x04,
        EventBatch = 0x05,
        Act = 0x06,
        SyncCall = 0x07,
        SyncResult = 0x08,
        PatchAdd = 0x09,
        PatchDel = 0x0A,
        RegistryAdd = 0x0B,
        RegistryDel = 0x0C,
        Reload = 0x0D,
        State = 0x0E,
        Log = 0x0F,
        Stats = 0x10,
        Ping = 0x11,
        Pong = 0x12,
        Error = 0x13,
        Doctor = 0x14,
        Bye = 0x15,
    }

    public readonly struct Frame
    {
        public Frame(MsgType type, byte[] payload)
        {
            Type = type;
            Payload = payload ?? Array.Empty<byte>();
        }

        public MsgType Type { get; }
        public byte[] Payload { get; }
        public bool HasPayload => Payload.Length > 0;

        public override string ToString() => Type + "(" + Payload.Length + "B)";
    }

    public sealed class FrameProtocolException : Exception
    {
        public FrameProtocolException(string message) : base(message) { }
    }

    /// <summary>
    /// [u32 LE length][u8 type][payload], length = 1 + payload.Length  (docs/01-protocol.md §2)
    /// </summary>
    public static class FrameCodec
    {
        public const int HeaderBytes = 5;
        public const int MaxPayloadBytes = 8 * 1024 * 1024;
        private const int MaxFrameLength = 1 + MaxPayloadBytes;

        public static byte[] Encode(MsgType type, byte[] payload)
        {
            payload = payload ?? Array.Empty<byte>();
            var length = 1 + payload.Length;
            if (length > MaxFrameLength)
                throw new FrameProtocolException("frame too large: " + length + " bytes");

            var frame = new byte[4 + length];
            BinaryPrimitives.WriteUInt32LittleEndian(frame.AsSpan(0, 4), (uint)length);
            frame[4] = (byte)type;
            if (payload.Length > 0) Buffer.BlockCopy(payload, 0, frame, 5, payload.Length);
            return frame;
        }

        public static byte[] Encode(MsgType type) => Encode(type, Array.Empty<byte>());

        /// <summary>Reads one frame. Returns null on clean EOF (no bytes read at all).</summary>
        public static async Task<Frame?> ReadAsync(Stream stream, CancellationToken cancellationToken = default)
        {
            var header = new byte[4];
            var read = await ReadAtLeastAsync(stream, header, 0, 4, cancellationToken).ConfigureAwait(false);
            if (read == 0) return null;
            if (read < 4) throw new FrameProtocolException("truncated frame header (" + read + "/4 bytes)");

            var length = BinaryPrimitives.ReadUInt32LittleEndian(header);
            if (length == 0) throw new FrameProtocolException("frame length must be >= 1");
            if (length > MaxFrameLength) throw new FrameProtocolException("frame too large: " + length + " bytes");

            var body = new byte[length];
            var bodyRead = await ReadAtLeastAsync(stream, body, 0, (int)length, cancellationToken).ConfigureAwait(false);
            if (bodyRead < length) throw new FrameProtocolException("truncated frame body (" + bodyRead + "/" + length + " bytes)");

            var type = (MsgType)body[0];
            var payload = new byte[length - 1];
            if (payload.Length > 0) Buffer.BlockCopy(body, 1, payload, 0, payload.Length);
            return new Frame(type, payload);
        }

        public static async Task WriteAsync(Stream stream, MsgType type, byte[] payload, CancellationToken cancellationToken = default)
        {
            var frame = Encode(type, payload);
            await stream.WriteAsync(frame, 0, frame.Length, cancellationToken).ConfigureAwait(false);
            await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
        }

        public static Task WriteAsync(Stream stream, MsgType type, CancellationToken cancellationToken = default)
            => WriteAsync(stream, type, Array.Empty<byte>(), cancellationToken);

        /// <summary>
        /// Fills the buffer as far as possible; returns the number of bytes read (== count unless EOF).
        /// </summary>
        private static async Task<int> ReadAtLeastAsync(Stream stream, byte[] buffer, int offset, int count, CancellationToken cancellationToken)
        {
            var total = 0;
            while (total < count)
            {
                var read = await stream.ReadAsync(buffer, offset + total, count - total, cancellationToken).ConfigureAwait(false);
                if (read <= 0) break;
                total += read;
            }
            return total;
        }
    }
}
