using System;

// Il2CppDumper-generated reference assemblies (Il2Cppmscorlib / Assembly-CSharp) ship a stub
// System.Runtime.CompilerServices.NullableAttribute without the constructors Roslyn needs, which
// makes it impossible to emit any nullable annotation metadata. Defining the attributes locally
// makes the compiler use these instead (CS0436 warnings are expected and harmless here).
namespace System.Runtime.CompilerServices
{
    [AttributeUsage(AttributeTargets.All, AllowMultiple = true)]
    internal sealed class NullableAttribute : Attribute
    {
        public NullableAttribute(byte value) => Values = new[] { value };

        public NullableAttribute(byte[] values) => Values = values;

        public byte[] Values { get; }
    }

    [AttributeUsage(AttributeTargets.All, AllowMultiple = false)]
    internal sealed class NullableContextAttribute : Attribute
    {
        public NullableContextAttribute(byte value) => Flag = value;

        public byte Flag { get; }
    }
}
