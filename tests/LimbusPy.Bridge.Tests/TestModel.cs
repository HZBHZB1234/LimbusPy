using System;
using System.Collections.Generic;
using System.Linq;

namespace LimbusPy.Bridge.Tests.Fixtures
{
    /// <summary>
    /// Plain managed model used to exercise the reflection layer end-to-end through the wire.
    /// It deliberately mirrors the shapes the real API needs: value properties, fields, enums,
    /// overloads, reference results and a method that throws.
    /// </summary>
    public enum TestFaction
    {
        NONE = 0,
        PLAYER = 1,
        ENEMY = 2,
    }

    public sealed class TestBuff
    {
        public TestBuff()
        {
            Keyword = string.Empty;
        }

        public TestBuff(string keyword, int stack, int turn)
        {
            Keyword = keyword;
            Stack = stack;
            Turn = turn;
        }

        public string Keyword;
        public int Stack;
        public int Turn;

        public override string ToString() => Keyword + ":" + Stack + "/" + Turn;
    }

    public sealed class TestUnit
    {
        public readonly List<TestBuff> Buffs = new List<TestBuff>();

        public string Name = string.Empty;
        public int MaxHp = 40;
        public double Speed = 5.0;
        public TestFaction Faction = TestFaction.NONE;

        public int Hp { get; set; }
        public string Nickname { get; set; } = string.Empty;

        public bool IsAlive => Hp > 0;

        public int Heal(int amount)
        {
            Hp += amount;
            return Hp;
        }

        public int Heal(int amount, bool overheal)
        {
            Hp += amount;
            if (!overheal && Hp > MaxHp) Hp = MaxHp;
            return Hp;
        }

        public TestBuff AddBuff(string keyword, int stack, int turn)
        {
            var buff = new TestBuff(keyword, stack, turn);
            Buffs.Add(buff);
            return buff;
        }

        public void AddBuff(TestBuff buff)
        {
            Buffs.Add(buff);
        }

        public int BuffCount => Buffs.Count;

        public int CountByKeyword(string keyword)
            => Buffs.Count(b => string.Equals(b.Keyword, keyword, StringComparison.OrdinalIgnoreCase));

        public string Overloaded(int value) => "int:" + value;

        public string Overloaded(string value) => "str:" + value;

        public string Describe() => Name + " hp=" + Hp + "/" + MaxHp + " buffs=" + Buffs.Count + " faction=" + Faction;

        public void Explode() => throw new InvalidOperationException("unit exploded on purpose");

        public int[] HpSeries(int count)
        {
            var result = new int[Math.Max(0, count)];
            for (var i = 0; i < result.Length; i++) result[i] = Hp + i;
            return result;
        }

        public Dictionary<string, object> Snapshot()
        {
            return new Dictionary<string, object>
            {
                ["name"] = Name,
                ["hp"] = Hp,
                ["maxHp"] = MaxHp,
                ["speed"] = Speed,
                ["faction"] = Faction,
                ["buffs"] = Buffs.Count,
            };
        }
    }

    public static class TestStatics
    {
        public const string Version = "harness-1.0";

        public static string Label = "static-label";

        public static int Add(int a, int b) => a + b;
        public static double Half(double value) => value / 2.0;
        public static string Echo(string text) => text;
        public static string Concat(string left, string right) => left + right;
        public static TestUnit CreateUnit(string name, int hp) => new TestUnit { Name = name, Hp = hp };
        public static TestFaction ParseFaction(string name) => (TestFaction)Enum.Parse(typeof(TestFaction), name, true);
        public static int Bump() => ++_bumps;

        private static int _bumps;
    }
}
