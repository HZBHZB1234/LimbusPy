using System;
using System.Collections.Generic;

namespace LimbusPy.Bridge.Core.Protocol
{
    /// <summary>
    /// A structured, wire-safe failure. Every LpcpFault maps 1:1 onto an LPCP error code
    /// (docs/01-protocol.md §7) and is what handlers should throw instead of raw exceptions.
    /// </summary>
    public sealed class LpcpFault : Exception
    {
        public LpcpFault(string code, string message, Dictionary<string, object> detail = null) : base(message)
        {
            Code = string.IsNullOrEmpty(code) ? ErrorCode.Internal : code;
            Detail = detail;
        }

        public string Code { get; }
        public Dictionary<string, object> Detail { get; }

        public Dictionary<string, object> ToError() => Lpcp.Error(Code, Message, Detail);

        public static LpcpFault NotFound(string message, Dictionary<string, object> detail = null)
            => new LpcpFault(ErrorCode.NotFound, message, detail);

        public static LpcpFault Ambiguous(string message, Dictionary<string, object> detail = null)
            => new LpcpFault(ErrorCode.Ambiguous, message, detail);

        public static LpcpFault Marshal(string message, Dictionary<string, object> detail = null)
            => new LpcpFault(ErrorCode.Marshal, message, detail);

        public static LpcpFault StaleHandle(string message, Dictionary<string, object> detail = null)
            => new LpcpFault(ErrorCode.StaleHandle, message, detail);

        public static LpcpFault Unsupported(string message, Dictionary<string, object> detail = null)
            => new LpcpFault(ErrorCode.Unsupported, message, detail);

        public static LpcpFault WrongThread(string message)
            => new LpcpFault(ErrorCode.WrongThread, message);
    }
}
