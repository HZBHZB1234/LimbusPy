using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace LimbusPy.Bridge.Core.Codec
{
    /// <summary>
    /// Minimal MessagePack writer covering the LPCP subset (docs/01-protocol.md §4).
    /// No ext / str32 / bin32 / array32 / map32 / timestamp: those are not needed and
    /// keeping the subset tiny keeps both implementations byte-identical and auditable.
    /// </summary>
    public sealed class MsgPackWriter
    {
        private readonly MemoryStream _buffer;

        public MsgPackWriter(int capacity = 256)
        {
            _buffer = new MemoryStream(capacity);
        }

        public int Length => (int)_buffer.Length;

        public byte[] ToArray() => _buffer.ToArray();

        public void WriteNil() => _buffer.WriteByte(0xC0);

        public void WriteBool(bool value) => _buffer.WriteByte(value ? (byte)0xC3 : (byte)0xC2);

        /// <summary>Writes a signed integer using the canonical smallest representation.</summary>
        public void WriteInt(long value)
        {
            if (value >= 0)
            {
                WriteUInt((ulong)value);
                return;
            }

            if (value >= -32)
            {
                _buffer.WriteByte(unchecked((byte)value));
                return;
            }

            if (value >= sbyte.MinValue)
            {
                _buffer.WriteByte(0xD0);
                _buffer.WriteByte(unchecked((byte)value));
                return;
            }

            if (value >= short.MinValue)
            {
                _buffer.WriteByte(0xD1);
                WriteBigEndian((ulong)(ushort)unchecked((short)value), 2);
                return;
            }

            if (value >= int.MinValue)
            {
                _buffer.WriteByte(0xD2);
                WriteBigEndian((ulong)(uint)unchecked((int)value), 4);
                return;
            }

            _buffer.WriteByte(0xD3);
            WriteBigEndian(unchecked((ulong)value), 8);
        }

        public void WriteUInt(ulong value)
        {
            if (value <= 0x7F)
            {
                _buffer.WriteByte(unchecked((byte)value));
                return;
            }

            if (value <= byte.MaxValue)
            {
                _buffer.WriteByte(0xCC);
                _buffer.WriteByte((byte)value);
                return;
            }

            if (value <= ushort.MaxValue)
            {
                _buffer.WriteByte(0xCD);
                WriteBigEndian(value, 2);
                return;
            }

            if (value <= uint.MaxValue)
            {
                _buffer.WriteByte(0xCE);
                WriteBigEndian(value, 4);
                return;
            }

            _buffer.WriteByte(0xCF);
            WriteBigEndian(value, 8);
        }

        /// <summary>Always emits float64 (0xCB); the decoder accepts float32 too.</summary>
        public void WriteDouble(double value)
        {
            _buffer.WriteByte(0xCB);
            WriteBigEndian(unchecked((ulong)BitConverter.DoubleToInt64Bits(value)), 8);
        }

        public void WriteString(string value)
        {
            if (value == null)
            {
                WriteNil();
                return;
            }

            var byteCount = Encoding.UTF8.GetByteCount(value);
            if (byteCount <= 31)
            {
                _buffer.WriteByte(unchecked((byte)(0xA0 | byteCount)));
            }
            else if (byteCount <= byte.MaxValue)
            {
                _buffer.WriteByte(0xD9);
                _buffer.WriteByte((byte)byteCount);
            }
            else if (byteCount <= ushort.MaxValue)
            {
                _buffer.WriteByte(0xDA);
                WriteBigEndian((ulong)byteCount, 2);
            }
            else
            {
                throw new MsgPackException("string too large for the LPCP subset (" + byteCount + " bytes, max 65535)");
            }

            var tmp = Encoding.UTF8.GetBytes(value);
            _buffer.Write(tmp, 0, tmp.Length);
        }

        public void WriteBinary(byte[] value)
        {
            if (value == null)
            {
                WriteNil();
                return;
            }

            if (value.Length <= byte.MaxValue)
            {
                _buffer.WriteByte(0xC4);
                _buffer.WriteByte((byte)value.Length);
            }
            else if (value.Length <= ushort.MaxValue)
            {
                _buffer.WriteByte(0xC5);
                WriteBigEndian((ulong)value.Length, 2);
            }
            else
            {
                throw new MsgPackException("binary too large for the LPCP subset (" + value.Length + " bytes, max 65535)");
            }

            _buffer.Write(value, 0, value.Length);
        }

        public void WriteArrayHeader(int count)
        {
            if (count < 0) throw new MsgPackException("negative array length");
            if (count <= 15)
            {
                _buffer.WriteByte(unchecked((byte)(0x90 | count)));
            }
            else if (count <= ushort.MaxValue)
            {
                _buffer.WriteByte(0xDC);
                WriteBigEndian((ulong)count, 2);
            }
            else
            {
                throw new MsgPackException("array too large for the LPCP subset (" + count + ")");
            }
        }

        public void WriteMapHeader(int count)
        {
            if (count < 0) throw new MsgPackException("negative map length");
            if (count <= 15)
            {
                _buffer.WriteByte(unchecked((byte)(0x80 | count)));
            }
            else if (count <= ushort.MaxValue)
            {
                _buffer.WriteByte(0xDE);
                WriteBigEndian((ulong)count, 2);
            }
            else
            {
                throw new MsgPackException("map too large for the LPCP subset (" + count + ")");
            }
        }

        /// <summary>
        /// Writes a value from the JSON-like object model used across the bridge
        /// (null, bool, long, ulong, double, string, byte[], object[], IDictionary&lt;string,object&gt;).
        /// </summary>
        public void WriteValue(object value)
        {
            switch (value)
            {
                case null:
                    WriteNil();
                    return;
                case bool b:
                    WriteBool(b);
                    return;
                case byte by:
                    WriteUInt(by);
                    return;
                case sbyte sb:
                    WriteInt(sb);
                    return;
                case short s:
                    WriteInt(s);
                    return;
                case ushort us:
                    WriteUInt(us);
                    return;
                case int i:
                    WriteInt(i);
                    return;
                case uint ui:
                    WriteUInt(ui);
                    return;
                case long l:
                    WriteInt(l);
                    return;
                case ulong ul:
                    WriteUInt(ul);
                    return;
                case float f:
                    WriteDouble(f);
                    return;
                case double d:
                    WriteDouble(d);
                    return;
                case string str:
                    WriteString(str);
                    return;
                case byte[] bin:
                    WriteBinary(bin);
                    return;
                case object[] array:
                    WriteArrayHeader(array.Length);
                    for (var idx = 0; idx < array.Length; idx++) WriteValue(array[idx]);
                    return;
                case IDictionary<string, object> map:
                    WriteMapHeader(map.Count);
                    foreach (var pair in map)
                    {
                        WriteString(pair.Key);
                        WriteValue(pair.Value);
                    }
                    return;
                default:
                    throw new MsgPackException("value type not encodable over LPCP: " + value.GetType().FullName);
            }
        }

        private void WriteBigEndian(ulong value, int bytes)
        {
            for (var shift = (bytes - 1) * 8; shift >= 0; shift -= 8)
            {
                _buffer.WriteByte(unchecked((byte)(value >> shift)));
            }
        }
    }

    public sealed class MsgPackException : Exception
    {
        public MsgPackException(string message) : base(message) { }
    }
}
