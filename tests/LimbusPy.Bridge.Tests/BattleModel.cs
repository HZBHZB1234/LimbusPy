using System.Collections.Generic;

namespace LimbusPy.Bridge.Tests.Fixtures
{
    /// <summary>
    /// Game-shaped stand-ins for the battle model.
    ///
    /// They exist so the Python façade (``limbuspy.game``) can be verified end-to-end over the real
    /// wire: the member names here are the ones verified against a real ``dump.cs``
    /// (``limbuspy/game/members.py``), so a rename in the façade breaks this test instead of a
    /// player's battle.
    /// </summary>
    public enum TestUnitFaction
    {
        NONE = 0,
        PLAYER = 1,
        ENEMY = 2,
    }

    public sealed class TestBattleBuff
    {
        private readonly string _keyword;
        private readonly int _stack;
        private readonly int _turn;

        public TestBattleBuff(string keyword, int stack, int turn)
        {
            _keyword = keyword;
            _stack = stack;
            _turn = turn;
        }

        public string GetMainKeyword() => _keyword;
        public int GetCurrentStack() => _stack;
        public int GetMaxStack() => 99;
        public int GetCurrentTurn() => _turn;
        public int GetMaxTurn() => 99;
        public string GetName() => _keyword;
        public int GetTotalStack() => _stack;
        public List<string> GetKeywordList() => new List<string> { _keyword };
        public bool IsKeyword(string keyword) => string.Equals(keyword, _keyword, System.StringComparison.OrdinalIgnoreCase);
        public string BuffType => "Negative";
        public int VanilaMaxStack => 99;
    }

    public sealed class TestBattleCoin
    {
        private readonly bool _head;
        private readonly int _scale;

        public TestBattleCoin(bool head, int scale)
        {
            _head = head;
            _scale = scale;
        }

        public bool IsHead() => _head;
        public bool IsTail() => !_head;
        public int GetScale() => _scale;
        public float GetProb() => 0.5f;
        public string GetCoinResult() => _head ? "HEAD" : "TAIL";
        public bool IsDestroyed => false;
        public bool IsSuccessAttack => true;
        public List<object> CoinAbilityList = new List<object>();
    }

    public sealed class TestBattleSkill
    {
        private readonly int _id;
        private readonly string _name;

        public TestBattleSkill(int id, string name)
        {
            _id = id;
            _name = name;
        }

        public int GetID() => _id;
        public string GetSkillName() => _name;
        public bool IsAttack() => true;
        public bool IsDefense() => false;
        public int GetCoinNum() => CoinList.Count;
        public TestBattleCoin GetCoinByIndex(int index) => index >= 0 && index < CoinList.Count ? CoinList[index] : null;
        public List<TestBattleCoin> GetAliveCoins() => new List<TestBattleCoin>(CoinList);
        public List<TestBattleCoin> CoinList = new List<TestBattleCoin>();
        public List<object> SkillAbilityList = new List<object>();
    }

    public sealed class TestBattleUnit
    {
        public int InstanceID { get; set; }
        public int Hp { get; set; }
        public int MaxHp { get; set; }
        public int Mp { get; set; }
        public int Level { get; set; }
        public int Position { get; set; }

        public TestUnitFaction Faction { get; set; } = TestUnitFaction.PLAYER;

        public readonly List<TestBattleBuff> Buffs = new List<TestBattleBuff>();
        public readonly List<TestBattleSkill> Skills = new List<TestBattleSkill>();

        private readonly int _originId;

        public TestBattleUnit(int instanceId, int originId, int hp)
        {
            InstanceID = instanceId;
            _originId = originId;
            Hp = hp;
            MaxHp = hp;
            Mp = 3;
            Level = 30;
            Position = 1;
        }

        public int GetOriginUnitID() => _originId;

        /// <summary>Mirrors the game: a unit reports the *opponent's* faction.</summary>
        public TestUnitFaction GetOpponentFaction()
            => Faction == TestUnitFaction.PLAYER ? TestUnitFaction.ENEMY : TestUnitFaction.PLAYER;

        public bool IsDead() => Hp <= 0;
        public bool IsBreak() => false;
        public bool IsActionable() => Hp > 0;
        public bool IsAlly(TestUnitFaction faction) => Faction == faction;
        public bool IsOpponent(TestUnitFaction faction) => Faction != faction;
        public bool HasUnitKeyword(string keyword) => false;

        public List<TestBattleBuff> GetActivatedBuffModels() => Buffs;

        public int GetActivatedBuffStack(string keyword)
        {
            foreach (var buff in Buffs)
            {
                if (string.Equals(buff.GetMainKeyword(), keyword, System.StringComparison.OrdinalIgnoreCase))
                    return buff.GetCurrentStack();
            }

            return 0;
        }

        public int ChangeHp(int amount)
        {
            Hp += amount;
            return Hp;
        }
    }

    /// <summary>Mirrors ``BattleObjectManager.BattleUnit`` (a wrapper around the model).</summary>
    public sealed class TestBattleUnitEntry
    {
        public TestBattleUnitEntry(TestBattleUnit model) => Model = model;

        public TestBattleUnit Model { get; }
    }

    public sealed class TestBattleObjectManager
    {
        public readonly Dictionary<int, TestBattleUnitEntry> _allUnitDictionary =
            new Dictionary<int, TestBattleUnitEntry>();

        public void Add(TestBattleUnit unit) => _allUnitDictionary[unit.InstanceID] = new TestBattleUnitEntry(unit);

        public TestBattleUnit GetModel(int instanceId)
            => _allUnitDictionary.TryGetValue(instanceId, out var entry) ? entry.Model : null;
    }
}
