"""devkit REPL + statics table tests."""

from __future__ import annotations

import json
import os
import threading

import pytest

from conftest import make_transport_pair
from limbuspy._mock import MockKernel
from limbuspy.devkit import Repl
from limbuspy.game import statics
from limbuspy.runtime.client import Bridge

# --------------------------------------------------------------------------------------
# statics: a directory of tables is enough (extraction is a separate, optional step)
# --------------------------------------------------------------------------------------


@pytest.fixture()
def tables(tmp_path):
    root = tmp_path / "statics"
    root.mkdir()
    (root / "SkillData.json").write_text(json.dumps([
        {"id": 601101, "name": "Slash", "tier": 1},
        {"id": 601102, "name": "Pierce", "tier": 2},
    ]), encoding="utf-8")
    # Unity text assets are often wrapped in a single-key object
    (root / "PassiveData.json").write_text(json.dumps({"list": [
        {"id": 9001, "name": "Aggro", "cost": 2},
    ]}), encoding="utf-8")
    (root / "NotJson.txt").write_text("ignored", encoding="utf-8")
    return str(root)


def test_open_and_list_tables(tables):
    db = statics.open_tables(tables)
    assert db.tables == ["PassiveData", "SkillData"]
    assert len(db) == 2
    assert db.table("skilldata") is not None  # case-insensitive
    assert db.table("nope") is None


def test_table_rows_and_lookup(tables):
    db = statics.open_tables(tables)
    skills = db.require("SkillData")
    assert len(skills) == 2
    assert skills.keys() == [601101, 601102]
    assert skills.get(601101)["name"] == "Slash"
    assert skills.get("601102")["name"] == "Pierce"
    assert skills.get(1) is None
    assert [row["id"] for row in skills.rows(tier=2)] == [601102]
    assert [row["id"] for row in skills.rows(tier=[1, 2])] == [601101, 601102]
    assert len(skills.rows()) == 2


def test_wrapped_payload_is_unwrapped(tables):
    db = statics.open_tables(tables)
    passives = db.require("PassiveData")
    assert len(passives) == 1
    assert passives.get(9001)["name"] == "Aggro"


def test_patch_apply_and_roundtrip(tables, tmp_path):
    db = statics.open_tables(tables)
    patch = db.patch("SkillData", 601101, {"tier": 3})
    patch = db.patch("SkillData", 601102, {"tier": 4}, patch=patch)
    patch = db.patch("PassiveData", 9001, {"cost": 0}, patch=patch)
    assert len(patch) == 3

    applied = patch.apply_to(db)
    assert applied == 3
    assert db.get("SkillData", 601101)["tier"] == 3
    assert db.get("PassiveData", 9001)["cost"] == 0

    path = str(tmp_path / "patches" / "my_mod.json")
    patch.save(path)
    loaded = statics.Patch.load(path)
    assert loaded.to_dict() == patch.to_dict()
    assert len(loaded) == 3


def test_patch_without_table_is_skipped(tables):
    db = statics.open_tables(tables)
    patch = statics.Patch().add("NoSuchTable", 1, {"x": 1}).add("SkillData", 999, {"x": 1})
    assert patch.apply_to(db) == 0


def test_missing_directory_is_empty_not_fatal(tmp_path):
    db = statics.open_tables(str(tmp_path / "nope"))
    assert db.tables == []
    with pytest.raises(KeyError):
        db.require("SkillData")


# --------------------------------------------------------------------------------------
# repl
# --------------------------------------------------------------------------------------


def test_repl_offline_namespace_has_the_tools():
    repl = Repl(None)
    out = repl.run_script(
        "print(members.UNIT.type_name)\n"
        "print(session is None)\n"
        "print(hasattr(gb, 'current'))\n"
        "print(hasattr(Modular, 'run'))\n"
    )
    assert "BattleUnitModel" in out
    assert "True" in out
    assert out.count("True") >= 3


def test_repl_help_prints_usage():
    repl = Repl(None)
    out = repl.run_script("help()\n")
    assert "bridge" in out and "session" in out and "Modular" in out


def test_repl_against_a_kernel():
    client, kernel = make_transport_pair()
    mock = MockKernel(kernel, handlers={}, battle={
        "round": 7, "wave": 2, "units": [
            {"ref": {"t": "unit", "h": 4242}, "instance_id": 1, "hp": 10, "max_hp": 20,
             "is_dead": False, "is_player": True},
        ]})
    bridge = Bridge(client, name="repl-test").start()
    thread = threading.Thread(target=lambda: mock.start(5.0), daemon=True)
    thread.start()
    bridge.handshake(mods=[], stubs_build="")
    try:
        repl = Repl(bridge)
        out = repl.run_script("b = gb.current(session)\nprint(b.round, len(b.units), b.units[0].hp)\n")
        assert "7 1 10" in out
    finally:
        bridge.close()
        mock.close()
        thread.join(5)


def test_repl_run_file(tmp_path):
    path = tmp_path / "script.py"
    path.write_text("print('from file', 1 + 1)\n", encoding="utf-8")
    assert "from file 2" in Repl(None).run_file(str(path))
