"""`limbuspy.game` façade tests, driven by the mock kernel (no game, no C#)."""

from __future__ import annotations

import os
import sys
import threading

import pytest

from conftest import make_transport_pair
from limbuspy._mock import MockKernel
from limbuspy.game import Session, battle as game_battle
from limbuspy.game.members import ACTION, BUFF, COIN, MANAGER, SKILL, UNIT, validate_against_index
from limbuspy.game.objects import Buff, Coin, Skill, Unit
from limbuspy.runtime.client import Bridge

# --------------------------------------------------------------------------------------
# a tiny in-memory model the mock kernel resolves by handle
# --------------------------------------------------------------------------------------

MODEL = {}
NEXT_HANDLE = [1000]


def add(kind, members):
    handle = NEXT_HANDLE[0]
    NEXT_HANDLE[0] += 1
    MODEL[handle] = {"kind": kind, "members": dict(members)}
    return {"t": kind, "h": handle}


def build_model():
    MODEL.clear()
    sinkng = add("buff", {"GetMainKeyword": "SINKING", "GetCurrentStack": 3, "GetCurrentTurn": 2,
                          "GetMaxStack": 99, "GetMaxTurn": 99, "IsKeyword": lambda kw: kw.upper() == "SINKING",
                          "BuffType": "Negative"})
    charge = add("buff", {"GetMainKeyword": "CHARGE", "GetCurrentStack": 5, "GetCurrentTurn": 1,
                          "GetMaxStack": 99, "GetMaxTurn": 99,
                          "IsKeyword": lambda kw: kw.upper() == "CHARGE", "BuffType": "Positive"})
    coin_head = add("coin", {"IsHead": lambda: True, "IsTail": lambda: False, "GetScale": 5,
                             "GetProb": 0.5, "IsDestroyed": False, "IsSuccessAttack": True})
    coin_tail = add("coin", {"IsHead": lambda: False, "IsTail": lambda: True, "GetScale": 4,
                             "GetProb": 0.5, "IsDestroyed": False, "IsSuccessAttack": True})
    coins = [coin_head, coin_tail]
    skill = add("skill", {"GetID": 601101, "GetSkillName": "Slash", "GetCoinNum": 2,
                          "CoinList": coins, "SkillAbilityList": [],
                          "GetCoinByIndex": lambda index: coins[index]})
    hero = add("unit", {"Hp": 40, "MaxHp": 40, "Mp": 3, "Level": 30, "Position": 1,
                        "InstanceID": 101, "GetOriginUnitID": 1001, "IsDead": False,
                        "GetActivatedBuffModels": [sinkng, charge], "GetOpponentFaction": "ENEMY",
                        "Skills": [skill], "HasUnitKeyword": lambda kw: False})
    enemy = add("unit", {"Hp": 300, "MaxHp": 300, "Mp": 0, "Level": 30, "Position": 4,
                         "InstanceID": 201, "GetOriginUnitID": 2001, "IsDead": False,
                         "GetActivatedBuffModels": [], "GetOpponentFaction": "PLAYER",
                         "Skills": [], "HasUnitKeyword": lambda kw: False})
    return {
        "round": 3,
        "wave": 1,
        "units": [
            {"ref": hero, "instance_id": 101, "origin_id": 1001, "hp": 40, "max_hp": 40, "mp": 3,
             "level": 30, "position": 1, "is_dead": False, "is_player": True, "faction": "ENEMY"},
            {"ref": enemy, "instance_id": 201, "origin_id": 2001, "hp": 300, "max_hp": 300, "mp": 0,
             "level": 30, "position": 4, "is_dead": False, "is_player": False, "faction": "PLAYER"},
        ],
    }


def resolve(target, member):
    if isinstance(target, dict):
        entry = MODEL.get(int(target.get("h") or 0))
        if entry is None:
            raise KeyError("stale ref %r" % (target,))
        if member not in entry["members"]:
            raise KeyError("%s has no member %r" % (entry["kind"], member))
        value = entry["members"][member]
        if callable(value):
            try:
                return value()
            except TypeError:
                return value  # needs arguments; only reachable through invoke()
        return value
    raise KeyError("unsupported target %r" % (target,))


def invoke(target, member, args):
    entry = MODEL.get(int(target.get("h") or 0)) if isinstance(target, dict) else None
    if entry is None:
        raise KeyError("stale ref")
    if member not in entry["members"]:
        raise KeyError("no member %s" % member)
    value = entry["members"][member]
    return value(*args) if callable(value) else value


class CountingBridge:
    """Counts round trips so 'snapshot values are free' can be asserted."""

    def __init__(self, bridge):
        self._bridge = bridge
        self.calls = 0

    def call(self, target, method, *args, **kwargs):
        self.calls += 1
        return self._bridge.call(target, method, *args, **kwargs)

    def request(self, msg_type, payload=None, timeout=5.0):
        self.calls += 1
        return self._bridge.request(msg_type, payload, timeout=timeout)

    def __getattr__(self, name):
        return getattr(self._bridge, name)


class Env:
    def __init__(self):
        self.battle = build_model()
        client, kernel = make_transport_pair()
        self.kernel = MockKernel(kernel, handlers={
            "reflect.get": lambda target, member: resolve(target, member),
            "reflect.invoke": lambda target, member, args: invoke(target, member, args),
        }, battle=self.battle)
        self.bridge = Bridge(client, name="game-test").start()
        self._thread = threading.Thread(target=lambda: self.kernel.start(5.0), daemon=True)

    def start(self) -> Session:
        self._thread.start()
        self.bridge.handshake(mods=[], stubs_build="")
        self.session = Session(self.bridge)
        return self.session

    def close(self):
        self.bridge.close()
        self.kernel.close()


@pytest.fixture()
def session():
    env = Env()
    try:
        yield env.start()
    finally:
        env.close()


# --------------------------------------------------------------------------------------
# façade behaviour
# --------------------------------------------------------------------------------------


def test_snapshot_lists_units(session):
    current = game_battle.current(session)
    assert current.round == 3
    assert current.wave == 1
    assert len(current.units) == 2
    assert [unit.instance_id for unit in current.player_units] == [101]
    assert [unit.instance_id for unit in current.enemy_units] == [201]
    assert len(current.alive_units) == 2
    assert current.unit(101) is not None
    assert current.unit(999) is None
    assert [unit.instance_id for unit in current.units_by_origin(2001)] == [201]


def test_snapshot_values_do_not_round_trip(session):
    counting = CountingBridge(session.bridge)
    session.bridge = counting
    current = game_battle.current(session)
    counting.calls = 0

    hero = current.unit(101)
    assert hero.hp == 40
    assert hero.max_hp == 40
    assert hero.hp_ratio == 1.0
    assert hero.missing_hp == 0
    assert counting.calls == 0, "snapshot-provided fields must not hit the bridge"


def test_unwrapped_members_round_trip_once(session):
    counting = CountingBridge(session.bridge)
    session.bridge = counting
    current = game_battle.current(session)
    hero = current.unit(101)
    counting.calls = 0

    assert hero.buffs  # one call to GetActivatedBuffModels
    assert counting.calls == 1
    assert len(hero.buffs) == 2
    assert counting.calls == 1, "session cache reused"


def test_buff_access(session):
    hero = game_battle.current(session).unit(101)
    sinking = hero.buff("SINKING")
    assert isinstance(sinking, Buff)
    assert sinking.stack == 3
    assert sinking.turn == 2
    assert sinking.keyword == "SINKING"
    assert sinking.buff_type == "Negative"
    assert hero.buff_stack("CHARGE") == 5
    assert hero.buff("nope") is None
    assert hero.buff_stack("nope") == 0
    assert {buff.keyword for buff in hero.buffs} == {"SINKING", "CHARGE"}


def test_skill_and_coins(session):
    hero = game_battle.current(session).unit(101)
    skills = session.coerce(hero.get("Skills"), Skill)
    assert len(skills) == 1
    skill = skills[0]
    assert skill.skill_id == 601101
    assert skill.name == "Slash"
    assert skill.coin_count == 2
    coins = skill.coins
    assert [coin.is_head() for coin in coins] == [True, False]
    assert [coin.scale for coin in coins] == [5, 4]
    assert isinstance(skill.coin(0), Coin)
    assert skill.coin(0).scale == 5


def test_coerce_relabels_generic_refs(session):
    """A ref the kernel could not classify arrives as kind 'obj'; the façade re-labels it."""
    hero = game_battle.current(session).unit(101)
    raw = hero.get("GetActivatedBuffModels")
    generic = [{"t": "obj", "h": buff.handle} for buff in raw]

    typed = session.coerce(generic, Buff)
    assert all(isinstance(item, Buff) for item in typed)
    assert typed[0].stack == 3
    # Re-labelling an already typed object is a no-op, not a re-wrap.
    assert session.coerce(typed[0], Buff) is typed[0]


def test_ref_identity_and_repr(session):
    current = game_battle.current(session)
    hero = current.unit(101)
    same = current.unit(101)
    assert hero == same
    assert hash(hero) == hash(same)
    assert "Unit" in repr(hero)
    assert hero.wire.get("h") == hero.handle


def test_raw_escape_hatch(session):
    hero = game_battle.current(session).unit(101)
    assert hero.get("Hp") == 40
    assert hero.call("GetOriginUnitID") == 1001
    assert hero.call("HasUnitKeyword", "TEST") is False


def test_member_tables_match_a_real_index():
    """Every member name the façade uses must exist in a generated dump index (skipped if none)."""
    from limbuspy import il2cpp

    index = il2cpp.default_index()
    if index is None:
        pytest.skip("no generated IL2CPP index in this checkout")

    problems = validate_against_index(index)
    assert problems == {}, problems


def test_member_tables_reject_unknown_alias():
    with pytest.raises(AttributeError):
        UNIT.no_such_alias
    assert UNIT.name_for("buffs") == "GetActivatedBuffModels"
    assert BUFF.is_call("stack") is True
    assert SKILL.is_call("coins") is False
    assert COIN.is_call("scale") is True
    assert ACTION.is_call("skill") is False
    assert MANAGER.type_name == "BattleObjectManager"
