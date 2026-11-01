"""Stub generator tests: dump.cs parsing, enum/stub emission, requirement validation."""

from __future__ import annotations

import ast
import json
import os
import sys

import pytest

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
sys.path.insert(0, TOOLS)

import gen_limbuspy_stubs as gen  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures", "dump_sample.cs")


@pytest.fixture(scope="module")
def records():
    parser = gen.DumpParser(FIXTURE)
    return list(parser.parse())


def by_name(records):
    return {record.full_name: record for record in records}


# --------------------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------------------


def test_kept_types(records):
    names = sorted(by_name(records))
    assert "TEST_ENUM" in names
    assert "TestUnit" in names
    assert "TestUnit.Nested" in names
    assert "ProjectMoon.Battle.KeptProjectMoonType" in names
    # engine/BCL namespaces are filtered out
    assert "System.Text.ExcludedByNamespace" not in names
    # compiler generated closures are filtered out
    assert not any("<>" in name for name in names)


def test_class_shape(records):
    unit = by_name(records)["TestUnit"]
    assert unit.kind == "class"
    assert unit.bases == ["BaseUnit", "IHasThing"]
    assert unit.typedef_index == 2

    fields = {field["name"]: field for field in unit.fields}
    assert fields["_hp"]["type"] == "int"
    assert fields["_position"]["type"] == "int"
    assert fields["SCALE"]["static"] is True
    assert fields["SCALE"]["readonly"] is True
    assert fields["_children"]["type"] == "List<TestUnit>"

    properties = {prop["name"]: prop for prop in unit.properties}
    assert properties["Hp"] == {"name": "Hp", "type": "int", "get": True, "set": True, "static": False}
    assert properties["IsAlive"]["set"] is False
    assert properties["Values"]["type"] == "List<int>"

    methods = {method["name"]: method for method in unit.methods}
    assert ".ctor" in methods
    heal = methods["Heal"]
    assert heal["ret"] == "int"
    assert [param["name"] for param in heal["params"]] == ["amount", "overheal"]
    assert [param["type"] for param in heal["params"]] == ["int", "bool"]
    assert methods["Create"]["static"] is True
    assert methods["Snapshot"]["ret"] == "Dictionary<string, int>"
    assert methods["Snapshot"]["virtual"] is True


def test_generic_inst_comment_does_not_leak_members(records):
    unit = by_name(records)["TestUnit"]
    names = [method["name"] for method in unit.methods]
    assert names.count("Heal") == 1, names


def test_enum_members(records):
    enum = by_name(records)["TEST_ENUM"]
    assert enum.kind == "enum"
    assert enum.enum_members == [("Alpha", 0), ("Beta", 1), ("Gamma", -2), ("Delta", 16)]


def test_nested_type_name(records):
    nested = by_name(records)["TestUnit.Nested"]
    assert nested.nested is True
    assert nested.full_name == "TestUnit.Nested"


# --------------------------------------------------------------------------------------
# emission
# --------------------------------------------------------------------------------------


def test_emit_enums_is_valid_python(records, tmp_path):
    path = str(tmp_path / "enums.py")
    written = gen.emit_enums(records, path)
    assert written == 1
    source = open(path, encoding="utf-8").read()
    ast.parse(source)
    assert "class TEST_ENUM(IntEnum):" in source
    assert "Gamma = -2" in source
    assert "Delta = 16" in source


def test_emit_stubs_is_valid_python(records, tmp_path):
    path = str(tmp_path / "types.pyi")
    written = gen.emit_stubs(records, path)
    assert written >= 3
    source = open(path, encoding="utf-8").read()
    ast.parse(source)
    assert "class TestUnit(BaseUnit, IHasThing):" in source
    assert "Hp: int" in source
    assert "def Heal(self, amount: int, overheal: bool) -> int: ..." in source
    assert "def Create(self, name: str) -> " in source


def test_type_mapping():
    assert gen._py_type("void") == "None"
    assert gen._py_type("int") == "int"
    assert gen._py_type("string") == "str"
    assert gen._py_type("TestUnit") == '"TestUnit"'
    assert gen._py_type("List<TestUnit>") == 'List["TestUnit"]'
    assert gen._py_type("Nullable<int>") == "Optional[int]"
    assert gen._py_type("Dictionary<string, List<int>>") == 'Dict[str, List[int]]'
    assert gen._py_type("int[]") == "List[int]"
    assert gen._py_type("") == "Any"


# --------------------------------------------------------------------------------------
# requirements / drift report
# --------------------------------------------------------------------------------------


def test_requirements_all_present(records):
    index = {record.full_name: record.to_index() for record in records}
    report = gen.validate_requirements(index, {
        "types": [
            {"type": "TestUnit", "members": ["Hp", "prop:Hp", "field:_hp", "Heal", "Create"]},
            {"type": "TEST_ENUM", "members": []},
        ]
    })
    assert report["ok"] is True
    assert report["checked"] == 2
    assert sorted(report["present"]) == ["TEST_ENUM", "TestUnit"]


def test_requirements_report_drift(records):
    index = {record.full_name: record.to_index() for record in records}
    report = gen.validate_requirements(index, {
        "types": [
            {"type": "TestUnit", "members": ["Hp", "Rename", "field:_missing"]},
            {"type": "NoSuchType", "members": []},
        ]
    })
    assert report["ok"] is False
    assert report["missing_types"] == ["NoSuchType"]
    assert report["missing_members"] == [{"type": "TestUnit", "members": ["Rename", "field:_missing"]}]


# --------------------------------------------------------------------------------------
# whole CLI on the fixture
# --------------------------------------------------------------------------------------


def test_cli_writes_the_full_artifact_set(tmp_path):
    out = str(tmp_path / "gen")
    code = gen.main(["--dump", FIXTURE, "--out", out, "--build", "testbuild"])
    assert code == 0

    target = os.path.join(out, "testbuild")
    for name in ("manifest.json", "methods.json", "enums.py", "types.pyi"):
        assert os.path.isfile(os.path.join(target, name)), name

    manifest = json.load(open(os.path.join(target, "manifest.json"), encoding="utf-8"))
    assert manifest["build"] == "testbuild"
    assert manifest["counts"]["types"] >= 4
    assert manifest["source"]["size"] > 0

    index = json.load(open(os.path.join(target, "methods.json"), encoding="utf-8"))
    assert "TestUnit" in index["types"]
    assert index["types"]["TestUnit"]["properties"][0]["name"] == "Hp"
