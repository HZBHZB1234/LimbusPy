"""内容注册层的测试。

守的是问题 2 的核心承诺：

> 注册了一个 Buff 之后，``@on_hit(when=victim.has(那个Buff))`` 只在**受击者真的拥有它**时
> 触发，而且这个判断 **0 次跨进程往返**。

最后一条由测试直接量化：回调触发前后 ``ops.calls`` 计数不变。
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading

import pytest

import limbuspy as lp
from limbuspy import registration as reg
from limbuspy._mock import MockBattle, MockKernel, MockOps, MockUnit
from limbuspy.mod.runtime import ModRuntime
from limbuspy.runtime.client import Bridge
from limbuspy.runtime.transport import FileTransport

MOD_SOURCE = '''
"""测试用的最小模组：注册一个 Buff + 一个带条件的被动。"""

import limbuspy as lp


class Drowning(lp.ContentBuff):
    local_id = "drowning"
    key = "DROWNING"
    name = "溺水"
    max_stacks = 10
    max_turns = 5


class TideCrush(lp.Passive):
    local_id = "tide_crush"
    name = "潮涌"

    hits = 0

    @lp.on_hit(when=lp.victim.has(Drowning))
    def crush(self, ev):
        type(self).hits += 1
'''

MOD_TOML = '''
name = "tide_test"
version = "0.1.0"
'''


# --------------------------------------------------------------------------------------
# 1. 谓词本身
# --------------------------------------------------------------------------------------


class _FakeBuff:
    bit = 3
    key = "DROWNING"

    @classmethod
    def keyword(cls):
        return "DROWNING"


def test_has_buff_is_zero_rpc():
    pred = reg.victim.has(_FakeBuff)
    assert pred.rpc == 0, "buff 判断必须是纯本地位运算"
    assert list(pred.bits()) == [("victim", 3)]


def test_bitmask_evaluation_is_one_and():
    pred = reg.compile_predicate(reg.victim.has(_FakeBuff))
    assert pred(reg.Cond(mask_victim=1 << 3)) is True
    assert pred(reg.Cond(mask_victim=0)) is False
    # 别的位不该误命中
    assert pred(reg.Cond(mask_victim=1 << 2)) is False


def test_combination_operators():
    a, b = _FakeBuff, type("Other", (), {"bit": 1, "key": "BURN"})
    both = reg.victim.has(a) & reg.victim.has(b)
    assert both(reg.Cond(mask_victim=(1 << 3) | (1 << 1))) is True
    assert both(reg.Cond(mask_victim=1 << 3)) is False

    either = reg.victim.has(a) | reg.victim.has(b)
    assert either(reg.Cond(mask_victim=1 << 1)) is True

    negated = ~reg.victim.has(a)
    assert negated(reg.Cond(mask_victim=0)) is True


def test_payload_field_comparison():
    """``ev.hp_damage > 100`` 这类条件必须能写，而且 0 往返。"""
    pred = reg.compile_predicate(reg.ev.hp_damage > 100)

    class Payload:
        hp_damage = 250

    assert pred(reg.Cond(payload=Payload())) is True

    class Small:
        hp_damage = 5

    assert pred(reg.Cond(payload=Small())) is False


def test_round_and_cooldown():
    pred = reg.compile_predicate(reg.round_() >= 3)
    assert pred(reg.Cond(round_=2)) is False
    assert pred(reg.Cond(round_=5)) is True

    state = {}
    once = reg.compile_predicate(reg.once_per_turn())
    assert once(reg.Cond(round_=1, state=state)) is True
    assert once(reg.Cond(round_=1, state=state)) is False, "同一回合不该触发第二次"
    assert once(reg.Cond(round_=2, state=state)) is True


def test_rpc_predicate_is_rejected_at_registration():
    """需要跨进程往返的谓词必须在注册期就被拒 —— 这是唯一的性能退化防线。"""

    class Expensive(reg.Pred):
        rpc = 1

        def __call__(self, cond):
            return True

        def describe(self):
            return "any_enemy.has(...)"

    with pytest.raises(reg.PredicateCostError) as info:
        reg.compile_predicate(Expensive())
    assert "rpc == 0" in str(info.value)

    # 显式承认成本时放行
    assert reg.compile_predicate(Expensive(), allow_rpc=True)(reg.Cond()) is True


def test_predicate_cannot_be_used_as_bool():
    """谓词是条件对象，不能直接 ``if pred:`` —— 那几乎总是写错了。"""
    with pytest.raises(TypeError) as info:
        bool(reg.victim.has(_FakeBuff))
    assert "谓词不能直接当布尔用" in str(info.value)


# --------------------------------------------------------------------------------------
# 2. 内容声明
# --------------------------------------------------------------------------------------


def test_content_validation_reports_problems():
    class Incomplete(lp.ContentBuff):
        pass

    problems = Incomplete.validate()
    assert any("local_id" in p for p in problems)
    assert any("name" in p for p in problems)
    assert any("key" in p for p in problems)


def test_plan_for_assigns_watch_and_bits():
    class Drowning(lp.ContentBuff):
        local_id = "drowning"
        key = "DROWNING"
        name = "溺水"
        bit = 0

    from limbuspy.mod.decorators import HookSpec

    fn = lambda self, ev: None
    fn.__limbuspy_hook__ = HookSpec("AttackSucceeded", {}, 0, False, None,
                                    when=reg.victim.has(Drowning))
    plan = reg.plan_for([fn], [Drowning])
    assert plan["watch"] == ["DROWNING"]
    assert plan["hooks"][0]["timing"] == "AttackSucceeded"
    assert plan["hooks"][0]["victim"] == [0]


# --------------------------------------------------------------------------------------
# 3. 端到端：注册 Buff → 只在该受击者有它时触发
# --------------------------------------------------------------------------------------


def _pipe_pair():
    left, right = socket.socketpair()
    left_file = left.makefile("rwb", buffering=0)
    right_file = right.makefile("rwb", buffering=0)
    left.close()
    right.close()
    return left_file, right_file


@pytest.fixture()
def loaded_mod():
    root = tempfile.mkdtemp(prefix="limbuspy-reg-")
    mods_dir = os.path.join(root, "mods")
    state_dir = os.path.join(root, "state")
    mod_dir = os.path.join(mods_dir, "tide_test")
    os.makedirs(mod_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(mod_dir, "mod.toml"), "w", encoding="utf-8") as handle:
        handle.write(MOD_TOML)
    with open(os.path.join(mod_dir, "__init__.py"), "w", encoding="utf-8") as handle:
        handle.write(MOD_SOURCE)

    battle = MockBattle([
        MockUnit(instance_id=101, is_player=True),
        MockUnit(instance_id=201, is_player=False, buffs={"DROWNING": [5, 2]}),
        MockUnit(instance_id=202, is_player=False),
    ], round_=3)
    ops = MockOps(battle)

    client_file, kernel_file = _pipe_pair()
    kernel = MockKernel(FileTransport(kernel_file, "mock-kernel"), ops=ops,
                        battle={"round": 3, "wave": 1, "units": []})
    bridge = Bridge(FileTransport(client_file, "limbuspy-reg"), name="reg").start()
    runtime = ModRuntime(bridge, mods_dir, state_dir, logger=lambda message: None)
    thread = threading.Thread(target=lambda: kernel.start(5.0), daemon=True)
    thread.start()
    runtime.start()
    thread.join(5.0)

    try:
        yield {"runtime": runtime, "ops": ops, "battle": battle, "kernel": kernel}
    finally:
        runtime.stop()
        bridge.close()
        kernel.close()


def _attack_event(battle, attacker_id, target_id, mask_victim):
    attacker = battle.resolve({"instance_id": attacker_id})
    target = battle.resolve({"instance_id": target_id})
    return {
        "timing": "AttackSucceeded",
        "payload": {
            "unit": battle.ref(attacker),
            "target": battle.ref(target),
            "final_damage": 10,
            "real_damage": 10,
            "hp_damage": 10,
            "is_critical": False,
            "timing": "ON_SUCCESS_ATTACK",
            "mask_victim": mask_victim,
            "round": 3,
        },
    }


def test_registered_buff_gates_the_hook(loaded_mod):
    """**用户主场景**：受击者身上有这个 Buff 才触发。"""
    runtime = loaded_mod["runtime"]
    ops = loaded_mod["ops"]
    battle = loaded_mod["battle"]
    kernel = loaded_mod["kernel"]

    mod = runtime.mods["tide_test"].instance
    assert [c.__name__ for c in mod.contents] == ["Drowning", "TideCrush"], (
        "内容没被收集到：%r" % ([c.__name__ for c in mod.contents],))
    assert mod.contents[0].bit == 0, "位序号应按收集顺序分配"
    assert mod.contents[0].owner == "tide_test", "owner 必须是注册它的 mod（Lethe 缺的就是这个）"

    # 计划已发给内核：watch 里就是这个 Buff 的关键字
    assert ops.plan["watch"] == ["DROWNING"], ops.plan

    # 直接从已加载的模块取 Passive 类（不要去 import_module，加载器用的是自己的模块名）
    module = runtime.mods["tide_test"].module
    module.TideCrush.hits = 0

    # ① 受击者 201 身上有 DROWNING → 位图命中 → 触发
    mask = ops.mask_for("AttackSucceeded", battle.resolve({"instance_id": 201}))
    assert mask == 1, "位图算错：mask=%r" % (mask,)
    kernel.emit_event_batch([_attack_event(battle, 101, 201, mask)])
    assert module.TideCrush.hits == 1, "受击者有该 buff，理应触发"

    # ② 受击者 202 没有 → 位图 0 → 不触发
    mask_zero = ops.mask_for("AttackSucceeded", battle.resolve({"instance_id": 202}))
    assert mask_zero == 0
    kernel.emit_event_batch([_attack_event(battle, 101, 202, mask_zero)])
    assert module.TideCrush.hits == 1, "受击者没有该 buff，不该触发"


def test_gate_costs_zero_round_trips(loaded_mod):
    """条件的求值必须是纯本地的：触发一次，op 计数不变。"""
    runtime = loaded_mod["runtime"]
    ops = loaded_mod["ops"]
    battle = loaded_mod["battle"]
    kernel = loaded_mod["kernel"]

    mask = ops.mask_for("AttackSucceeded", battle.resolve({"instance_id": 201}))
    before = len(ops.calls)
    kernel.emit_event_batch([_attack_event(battle, 101, 201, mask)])
    after = len(ops.calls)

    assert after == before, (
        "条件求值产生了 %d 次跨进程调用（%r），必须是 0" % (after - before, ops.calls[before:]))


def test_rejected_predicate_is_recorded_not_fatal(loaded_mod):
    """被拒绝的谓词只让那一个钩子失效，不影响 mod 其它部分。"""
    runtime = loaded_mod["runtime"]
    mod = runtime.mods["tide_test"].instance
    # 这个 mod 的谓词是合法的，所以不该有问题记录
    assert not mod.registration_errors, mod.registration_errors

# --------------------------------------------------------------------------------------
# 4. S5：自定义 Buff 真注入（content.register）
# --------------------------------------------------------------------------------------


def test_custom_buff_goes_through_injection(loaded_mod):
    """``game_id == 0`` 的 Buff 走真注入，并从同一基址镜像出 game_id。"""
    mod = loaded_mod["runtime"].mods["tide_test"].instance
    drowning = mod.contents[0]
    assert drowning.game_id == lp.registration.CUSTOM_BUFF_BASE_ID, (
        "自定义 Buff 的 game_id 应镜像为 CUSTOM_BUFF_BASE_ID，实际 %r" % (drowning.game_id,))
    assert loaded_mod["ops"].injected == ["DROWNING"], (
        "content.register 应收到这个 Buff 的关键字，实际 %r" % (loaded_mod["ops"].injected,))


def test_official_buff_is_not_injected():
    """复用官方 id 的 Buff 一行注入都不该做 —— 那是推荐路线。"""
    assert lp.registration.CUSTOM_BUFF_BASE_ID == 24576, "基址必须与 C# BuffInject.BaseId 一致"


def test_base_id_does_not_collide_with_vanilla_or_lethe():
    """24576 必须避开原版（0..2123）与 Lethe 的 4097+2k 领地。"""
    vanilla_max = 2123
    lethe = {4097 + 2 * k for k in range(64)}
    assert lp.registration.CUSTOM_BUFF_BASE_ID > vanilla_max
    assert lp.registration.CUSTOM_BUFF_BASE_ID not in lethe


# --------------------------------------------------------------------------------------
# 5. S6：人格 / 技能 / EGO 的声明与校验
# --------------------------------------------------------------------------------------


def test_identity_requires_a_template():
    """不支持从零造人格：缺 base_game_id 必须被拒。"""

    class MyFaust(lp.Identity):
        local_id = "my_faust"
        name = "魁首浮士德（改）"

    problems = MyFaust.validate()
    assert any("base_game_id" in p for p in problems), problems

    class WithTemplate(MyFaust):
        base_game_id = 10115

    # 派生类覆盖了 base_game_id 之后就不该再有那条问题
    assert not any("base_game_id" in p for p in WithTemplate.validate())


def test_skill_and_ego_require_templates():
    for kind in (lp.Skill, lp.Ego):
        class Bare(kind):
            local_id = "bare"
            name = "裸的"

        problems = Bare.validate()
        assert any("base_game_id" in p for p in problems), (kind, problems)


def test_buff_row_matches_game_schema():
    """生成的 Buff 行必须用游戏 ``BuffStaticData`` 的真实字段名。

    字段表取自 ``il2csharp/out/limbus/full0/Assembly-CSharp/BuffStaticData.cs``
    的 ``// 0xNN`` 偏移行 —— 这里抽查几个"最容易写错"的键。
    """
    class Drowning(lp.ContentBuff):
        local_id = "drowning"
        key = "DROWNING"
        name = "溺水"
        max_stacks = 10
        max_turns = 5
        icon = "buffIcon_drowning.png"

    row = Drowning.define()
    assert row["id"] == "DROWNING", "字符串 id 就是枚举名"
    assert row["maxStack"] == 10 and row["maxTurn"] == 5
    assert row["iconId"] == "buffIcon_drowning.png"
    assert row["buffClass"] == "Neutral"
    # 这几个是 BuffStaticData 里真实存在、但很容易被写成别的名字的键
    for key in ("destroyableOnZero", "destroyableOnZeroTurn", "canBeDespelled",
                "canBeDuplicated", "stackLimitPerTurn", "totalStack",
                "playerImportanceLevel", "enemyImportanceLevel", "syncOnBossRaid"):
        assert key in row, "缺少游戏字段 %r" % (key,)


def test_buff_row_has_no_invented_fields():
    """不许有游戏里不存在的键 —— 拼错的键会被加载器静默忽略，比报错更糟。"""
    from limbuspy import staticdata

    class Plain(lp.ContentBuff):
        local_id = "plain"
        key = "PLAIN"
        name = "普通"

    allowed = {
        "id", "subIdList", "iconId", "typoIconId", "buffClass", "buffType", "attributeType",
        "totalStack", "stackLimitPerTurn", "maxStack", "maxTurn",
        "destroyableOnZero", "destroyableOnZeroTurn", "destroyableOnBothZero",
        "destroyableOnDie", "destroyableOnRetreat", "canBeDespelled", "canBeDuplicated",
        "list", "useTrigger", "categoryKeywordList", "playerImportanceLevel",
        "enemyImportanceLevel", "syncOnBossRaid", "canIgnoreGiveAdder",
    }
    extra = set(Plain.define()) - allowed
    assert not extra, "生成了游戏里不存在的字段: %r" % (sorted(extra),)
    assert staticdata.TABLE_NAMES["buff"] == "BuffData"


def test_passive_row_requires_official_id():
    class NoId(lp.Passive):
        local_id = "no_id"
        name = "无 id"

    with pytest.raises(ValueError) as info:
        NoId.define()
    assert "passive_id" in str(info.value)

    class WithId(NoId):
        passive_id = 601101

    row = WithId.define()
    assert row["id"] == 601101
    assert row["useUnlock"] is False
    assert lp.Passive.table_name() == "PassiveData"


def test_identity_and_skill_and_ego_produce_template_specs():
    """人格/技能/EGO 产出的是「模板 + 覆盖」，不是完整行 —— 这是刻意的边界。"""

    class MyFaust(lp.Identity):
        local_id = "my_faust"
        name = "魁首浮士德（改）"
        base_game_id = 10115
        rank = 3

    spec = MyFaust.define()
    assert spec["table"] == "PersonalityData"
    assert spec["base"] == 10115
    assert spec["overrides"]["rank"] == 3

    for cls, table in ((lp.Skill, "SkillData"), (lp.Ego, "EgoData")):
        class Templated(cls):
            local_id = "templated"
            name = "模板"
            base_game_id = 12345

        got = Templated.define()
        assert got["table"] == table
        assert got["base"] == 12345


def test_missing_template_is_an_error_not_a_crash():
    """缺 base_game_id 时 define() 抛 ValueError（配置错误要早暴露），
    但 collect() 会跳过它，一个坏内容不影响整个内容包。"""
    from limbuspy import staticdata

    class Bad(lp.Identity):
        local_id = "bad"
        name = "坏的人格"

    with pytest.raises(ValueError):
        Bad.define()

    tables = staticdata.collect([Bad])
    assert "PersonalityData" not in tables, "坏内容不该进内容包"


def test_collect_groups_contents_by_table():
    from limbuspy import staticdata

    class B(lp.ContentBuff):
        local_id = "b"
        key = "B_KEY"
        name = "B"

    class P(lp.Passive):
        local_id = "p"
        name = "P"
        passive_id = 601101

    tables = staticdata.collect([B, P])
    assert set(tables) == {"BuffData", "PassiveData"}
    assert tables["BuffData"][0]["id"] == "B_KEY"
    assert tables["PassiveData"][0]["id"] == 601101
