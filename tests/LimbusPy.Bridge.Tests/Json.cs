using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace LimbusPy.Bridge.Tests
{
    /// <summary>
    /// Deterministic JSON writer for harness output. Hand-rolled so that the exact byte layout
    /// of the summary/golden files is stable and does not depend on serializer settings.
    /// </summary>
    internal static class Json
    {
        public static string Write(object value)
        {
            var sb = new StringBuilder(1024);
            WriteValue(sb, value);
            return sb.ToString();
        }

        private static void WriteValue(StringBuilder sb, object value)
        {
            switch (value)
            {
                case null:
                    sb.Append("null");
                    return;
                case bool b:
                    sb.Append(b ? "true" : "false");
                    return;
                case byte[] bytes:
                    sb.Append('"').Append(Convert.ToHexString(bytes).ToLowerInvariant()).Append('"');
                    return;
                case string s:
                    WriteString(sb, s);
                    return;
                case double d:
                    if (double.IsNaN(d) || double.IsInfinity(d)) throw new InvalidOperationException("non-finite double in JSON");
                    sb.Append(d.ToString("R", CultureInfo.InvariantCulture));
                    return;
                case float f:
                    sb.Append(f.ToString("R", CultureInfo.InvariantCulture));
                    return;
                case ulong ul:
                    sb.Append(ul.ToString(CultureInfo.InvariantCulture));
                    return;
                case long l:
                    sb.Append(l.ToString(CultureInfo.InvariantCulture));
                    return;
                case int i:
                    sb.Append(i.ToString(CultureInfo.InvariantCulture));
                    return;
                case IDictionary<string, object> map:
                {
                    sb.Append('{');
                    var first = true;
                    foreach (var pair in map)
                    {
                        if (!first) sb.Append(',');
                        first = false;
                        WriteString(sb, pair.Key);
                        sb.Append(':');
                        WriteValue(sb, pair.Value);
                    }
                    sb.Append('}');
                    return;
                }
                case IEnumerable<string> strings:
                {
                    sb.Append('[');
                    var first = true;
                    foreach (var item in strings)
                    {
                        if (!first) sb.Append(',');
                        first = false;
                        WriteString(sb, item);
                    }
                    sb.Append(']');
                    return;
                }
                case System.Collections.IEnumerable items:
                {
                    sb.Append('[');
                    var first = true;
                    foreach (var item in items)
                    {
                        if (!first) sb.Append(',');
                        first = false;
                        WriteValue(sb, item);
                    }
                    sb.Append(']');
                    return;
                }
                default:
                    throw new InvalidOperationException("cannot serialise " + value.GetType().FullName + " to harness JSON");
            }
        }

        private static void WriteString(StringBuilder sb, string value)
        {
            sb.Append('"');
            foreach (var ch in value)
            {
                switch (ch)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (ch < 0x20) sb.Append("\\u").Append(((int)ch).ToString("x4", CultureInfo.InvariantCulture));
                        else sb.Append(ch);
                        break;
                }
            }
            sb.Append('"');
        }
    }
}
