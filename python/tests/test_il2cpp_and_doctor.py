"""IL2CPP index access + doctor tests.

The index is generated from a fixture dump.cs, so these tests exercise the real loading path
without needing the game or the 87 MB dump.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
FIXTURE = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures", "dump_sample.cs")
sys.path.insert(0, TOOLS)

from limbuspy import il2cpp  # noqa: E402
from limbuspy.devkit import diagnose  # noqa: E402


@pytest.fixture()
def generated(tmp_path, monkeypatch):
    """Generates an index from the fixture dump and points the loader at it."""
    import gen_limbuspy_stubs as gen

    out = str(tmp_path / "_gen")
    requirements = str(tmp_path / "req.json")
    with open(requirements, "w", encoding="utf-8") as handle:
        json.dump({"types": [
            {"type": "TestUnit", "members": ["Hp", "field:_hp", "Heal"]},
        ]}, handle)

    code = gen.main(["--dump", FIXTURE, "--out", out, "--build", "fixturebuild",
                     "--requirements", requirements, "--stubs", "all"])
    assert code == 0

    il2cpp.reset_cache()
    monkeypatch.setattr(il2cpp, "GEN_ROOT", out)
    monkeypatch.setenv("LIMBUSPY_BUILD", "fixturebuild")
    yield out
    il2cpp.reset_cache()


def test_available_builds_and_current(generated):
    assert il2cpp.available_builds() == ["fixturebuild"]
    assert il2cpp.current_build() == "fixturebuild"


def test_index_queries(generated):
    index = il2cpp.default_index()
    assert index is not None
    assert len(index) >= 4

    assert index.has_type("TestUnit")
    assert not index.has_type("NoSuchType")

    assert index.has_member("TestUnit", "Hp")
    assert index.has_member("TestUnit", "_hp")
    assert index.has_member("TestUnit", "Heal")
    assert not index.has_member("TestUnit", "Rename")

    described = index.describe("TestUnit")
    assert described["kind"] == "class"
    assert described["bases"] == ["BaseUnit", "IHasThing"]
    assert "Hp" in described["properties"]
    assert "Heal" in described["methods"]

    member = index.member("TestUnit", "Heal")
    assert member["kind"] == "method"
    assert [param["name"] for param in member["params"]] == ["amount", "overheal"]


def test_index_search(generated):
    index = il2cpp.default_index()
    assert index.type_names("Test") == ["TestUnit", "TestUnit.Nested"]
    hits = index.find_members("^Hp$")
    assert any(hit["type"] == "TestUnit" and hit["name"] == "Hp" for hit in hits)


def test_enums_module(generated):
    enums = il2cpp.enums_module()
    assert int(enums.TEST_ENUM.Gamma) == -2
    assert int(enums.TEST_ENUM.Delta) == 16


def test_missing_index_returns_none(monkeypatch, tmp_path):
    il2cpp.reset_cache()
    monkeypatch.setattr(il2cpp, "GEN_ROOT", str(tmp_path / "nope"))
    monkeypatch.delenv("LIMBUSPY_BUILD", raising=False)
    assert il2cpp.default_index() is None
    assert il2cpp.available_builds() == []
    with pytest.raises(ImportError):
        il2cpp.enums_module()


# --------------------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------------------


def test_doctor_without_kernel(generated, tmp_path):
    report = diagnose(mods_dir=str(tmp_path / "mods"))
    by_name = {check.name: check for check in report.checks}

    assert by_name["python"].status == "ok"
    assert by_name["stubs"].status == "ok", by_name["stubs"].detail
    assert "fixturebuild" in by_name["stubs"].detail
    assert by_name["mods"].status == "warn"  # directory does not exist
    assert by_name["kernel"].status == "skipped"

    text = report.to_text()
    assert "LimbusPy" not in text  # plain report body
    assert "result:" in text


def test_doctor_flags_missing_requirement(tmp_path, monkeypatch):
    import gen_limbuspy_stubs as gen

    out = str(tmp_path / "_gen")
    requirements = str(tmp_path / "req_bad.json")
    with open(requirements, "w", encoding="utf-8") as handle:
        json.dump({"types": [{"type": "TestUnit", "members": ["DoesNotExist"]}]}, handle)

    gen.main(["--dump", FIXTURE, "--out", out, "--build", "badbuild",
              "--requirements", requirements, "--stubs", "off"])
    il2cpp.reset_cache()
    monkeypatch.setattr(il2cpp, "GEN_ROOT", out)
    monkeypatch.setenv("LIMBUSPY_BUILD", "badbuild")

    report = diagnose()
    stubs_check = [check for check in report.checks if check.name == "stubs"][0]
    assert stubs_check.status == "broken"
    assert "requirements FAILED" in stubs_check.detail
    assert report.ok is False
    il2cpp.reset_cache()


def test_doctor_mods_ok(tmp_path):
    from limbuspy.devkit import check_mods

    mods = tmp_path / "mods" / "hello"
    mods.mkdir(parents=True)
    (mods / "__init__.py").write_text(
        "from limbuspy.mod import Mod\nclass Hello(Mod):\n    pass\n", encoding="utf-8"
    )

    check = check_mods(str(tmp_path / "mods"))
    assert check.status == "ok"
    assert "hello" in check.detail


def test_doctor_mods_broken_dependency(tmp_path):
    from limbuspy.devkit import check_mods

    mods = tmp_path / "mods" / "needy"
    mods.mkdir(parents=True)
    (mods / "__init__.py").write_text(
        "from limbuspy.mod import Mod\nclass Needy(Mod):\n    requires = ['missing']\n",
        encoding="utf-8",
    )

    check = check_mods(str(tmp_path / "mods"))
    assert check.status == "broken"
    assert "missing" in check.detail


# --------------------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------------------


def test_cli_version(capsys):
    from limbuspy import cli

    assert cli.main(["version"]) == 0
    out = capsys.readouterr().out
    assert "limbuspy" in out and "proto" in out


def test_cli_index_and_stubs(generated, capsys):
    from limbuspy import cli

    assert cli.main(["index", "Test"]) == 0
    assert "TestUnit" in capsys.readouterr().out

    assert cli.main(["index"]) == 0
    assert "fixturebuild" in capsys.readouterr().out


def test_cli_doctor_json(generated, capsys):
    from limbuspy import cli

    assert cli.main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert any(check["name"] == "stubs" for check in payload["checks"])
