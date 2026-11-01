using System;
using System.Collections.Generic;
using System.Text;

namespace LimbusPy.Bridge.Core.Codec
{
    /// <summary>
    /// Minimal MessagePack reader for the LPCP subset (docs/01-protocol.md §4).
    /// Decoded values use a JSON-like model:
    ///   null, bool, long, ulong, double, string, byte[], object[], Dictionary&lt;string,object&gt;
    /// Non-string map keys are decoded with ToString() so that diagnostics never throw.
    /// </summary>
    public sealed class MsgPackReader
    {
        private readonly byte[] _buffer;
        private readonly int _offset;
        private int _position;
        private readonly int _end;

        public MsgPackReader(byte[] buffer) : this(buffer, 0, buffer?.Length ?? 0) { }

        public MsgPackReader(byte[] buffer, int offset, int count)
        {
            if (buffer == null) throw new ArgumentNullException(nameof(buffer));
            if (offset < 0 || count < 0 || offset + count > buffer.Length)
                throw new MsgPackException("invalid reader window");
            _buffer = buffer;
            _offset = offset;
            _position = offset;
            _end = offset + count;
        }

        public int Remaining => _end - _position;

        public bool AtEnd => _position >= _end;

        public object ReadValue()
        {
            var marker = ReadByte();
            switch (marker)
            {
                case 0xC0: return null;
                case 0xC2: return false;
                case 0xC3: return true;

                case 0xCC: return (long)ReadBigEndian(1);
                case 0xCD: return (long)ReadBigEndian(2);
                case 0xCE: return (long)ReadBigEndian(4);
                case 0xCF:
                {
                    var unsigned = ReadBigEndian(8);
                    return unsigned <= long.MaxValue ? (object)(long)unsigned : unsigned;
                }
                case 0xD0: return (long)(sbyte)ReadByte();
                case 0xD1: return (long)(short)ReadBigEndian(2);
                case 0xD2: return (long)(int)ReadBigEndian(4);
                case 0xD3: return unchecked((long)ReadBigEndian(8));

                case 0xCA: return (double)BitConverter.Int32BitsToSingle(unchecked((int)ReadBigEndian(4)));
                case 0xCB: return BitConverter.Int64BitsToDouble(unchecked((long)ReadBigEndian(8)));

                case 0xC4: return ReadBytes((int)ReadBigEndian(1));
                case 0xC5: return ReadBytes((int)ReadBigEndian(2));

                case 0xD9: return ReadString((int)ReadBigEndian(1));
                case 0xDA: return ReadString((int)ReadBigEndian(2));

                case 0xDC: return ReadArray((int)ReadBigEndian(2));
                case 0xDE: return ReadMap((int)ReadBigEndian(2));

                case 0xD4:
                case 0xD5:
                case 0xD6:
                case 0xD7:
                case 0xD8:
                case 0xC7:
                case 0xC8:
                case 0xC9:
                    throw new MsgPackException("msgpack ext types are not supported by LPCP");

                case 0xDB:
                case 0xC6:
                case 0xDD:
                case 0xDF:
                    throw new MsgPackException("msgpack 32-bit length headers are not supported by LPCP");

                default:
                    if (marker <= 0x7F) return (long)marker;                    // positive fixint
                    if (marker >= 0xE0) return (long)(sbyte)marker;             // negative fixint
                    if ((marker & 0xF0) == 0x80) return ReadMap(marker & 0x0F); // fixmap
                    if ((marker & 0xF0) == 0x90) return ReadArray(marker & 0x0F); // fixarray
                    if ((marker & 0xE0) == 0xA0) return ReadString(marker & 0x1F); // fixstr
                    throw new MsgPackException("unknown msgpack marker 0x" + marker.ToString("X2"));
            }
        }

        /// <summary>Reads the whole payload and asserts that it is a string-keyed map.</summary>
        public Dictionary<string, object> ReadMapPayload()
        {
            var value = ReadValue();
            if (value is Dictionary<string, object> map) return map;
            throw new MsgPackException("LPCP payload must be a map, got " + Describe(value));
        }

        private object[] ReadArray(int count)
        {
            var items = new object[count];
            for (var i = 0; i < count; i++) items[i] = ReadValue();
            return items;
        }

        private Dictionary<string, object> ReadMap(int count)
        {
            var map = new Dictionary<string, object>(count, StringComparer.Ordinal);
            for (var i = 0; i < count; i++)
            {
                var keyValue = ReadValue();
                var key = keyValue as string ?? (keyValue?.ToString() ?? "null");
                map[key] = ReadValue();
            }
            return map;
        }

        private string ReadString(int length)
        {
            var bytes = ReadBytes(length);
            return Encoding.UTF8.GetString(bytes);
        }

        private byte[] ReadBytes(int length)
        {
            if (length < 0 || _position + length > _end) throw new MsgPackException("truncated msgpack payload");
            var bytes = new byte[length];
            Buffer.BlockCopy(_buffer, _position, bytes, 0, length);
            _position += length;
            return bytes;
        }

        private byte ReadByte()
        {
            if (_position >= _end) throw new MsgPackException("truncated msgpack payload");
            return _buffer[_position++];
        }

        private ulong ReadBigEndian(int bytes)
        {
            if (_position + bytes > _end) throw new MsgPackException("truncated msgpack payload");
            ulong value = 0;
            for (var i = 0; i < bytes; i++) value = (value << 8) | _buffer[_position++];
            return value;
        }

        internal static string Describe(object value)
        {
            if (value == null) return "null";
            if (value is string s) return "str(" + s.Length + ")";
            if (value is byte[] b) return "bin(" + b.Length + ")";
            if (value is object[] a) return "array(" + a.Length + ")";
            if (value is Dictionary<string, object> m) return "map(" + m.Count + ")";
            return value.GetType().Name + "(" + value + ")";
        }
    }
}
