// Minimal Il2CppDumper dump.cs excerpt used by tests/test_stubgen.py
// Image 0: Assembly-CSharp.dll - 0

// Namespace:
public enum TEST_ENUM // TypeDefIndex: 1
{
	// Fields
	public int value__; // 0x0
	public const TEST_ENUM Alpha = 0;
	public const TEST_ENUM Beta = 1;
	public const TEST_ENUM Gamma = -2;
	public const TEST_ENUM Delta = 0x10;
}

// Namespace:
[SomeAttribute("x")]
public class TestUnit : BaseUnit, IHasThing // TypeDefIndex: 2
{
	// Fields
	protected int _hp; // 0x10
	[UnitStatusDataAsJson("")]
	protected int _position; // 0x14
	public static readonly float SCALE; // 0x0
	private readonly List<TestUnit> _children; // 0x18

	// Properties
	public int Hp { get; set; }
	public bool IsAlive { get; }
	public List<int> Values { get; }

	// Methods
	// RVA: -1 Offset: -1
	public void .ctor() { }
	// RVA: -1 Offset: -1
	public int Heal(int amount, bool overheal = false) { }
	/* GenericInstMethod :
	|
	|-RVA: 0x1 Offset: 0x1 VA: 0x1
	|-TestUnit.Heal<int>
	*/

	// RVA: 0x2
	public static TestUnit Create(string name) { }
	// RVA: 0x3
	public virtual Dictionary<string, int> Snapshot() { }
}

// Namespace: System.Text
public class ExcludedByNamespace // TypeDefIndex: 3
{
	// Methods
	public void Foo() { }
}

// Namespace:
public class TestUnit.Nested // TypeDefIndex: 4
{
	// Fields
	public int Inner; // 0x0
}

// Namespace:
public sealed class TestUnit.<>c__DisplayClass0_0 // TypeDefIndex: 5
{
}

// Namespace: ProjectMoon.Battle
public class KeptProjectMoonType // TypeDefIndex: 6
{
	// Fields
	public int Value; // 0x0
}
