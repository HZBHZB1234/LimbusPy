"""Distribution packaging tests: both layouts, and a doctor run from the packed tree."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOLS = os.path.join(ROOT, "tools")
KERNEL_DLL = os.path.join(ROOT, "kernel", "LimbusPy.Bridge", "bin", "Debug", "net6.0", "LimbusPy.Bridge.dll")

sys.path.insert(0, TOOLS)

import pack as packer  # noqa: E402


pytestmark = pytest.mark.skipif(not os.path.isfile(KERNEL_DLL),
                                reason="kernel assemblies not built (dotnet build first)")


def _pack(out: str, variant: str, embed=None) -> str:
    code = packer.main(["--out", out, "--variant", variant, "--no-build"] +
                       (["--python-embed", embed] if embed else []))
    assert code == 0
    return os.path.join(out, variant)


def _doctor(layout: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(layout, "python")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-m", "limbuspy", "doctor", "--mods", "mods"],
                          cwd=layout, env=env, capture_output=True, text=True, timeout=120)


def test_system_layout_is_complete_and_healthy(tmp_path):
    layout = _pack(str(tmp_path / "dist"), "system")

    for name in ("LimbusPy.Bridge.dll", "LimbusPy.Bridge.Core.dll", "LimbusPy.Bridge.Harmony.dll",
                 "manifest.json", "README.md"):
        assert os.path.isfile(os.path.join(layout, name)), name

    assert os.path.isfile(os.path.join(layout, "python", "limbuspy", "__init__.py"))
    assert os.path.isdir(os.path.join(layout, "python", "limbuspy", "game"))
    assert os.path.isfile(os.path.join(layout, "mods", "_example", "__init__.py"))

    manifest = json.load(open(os.path.join(layout, "manifest.json"), encoding="utf-8"))
    assert manifest["variant"] == "system"
    assert manifest["complete"] is True
    assert set(manifest["assemblies"]) == {"LimbusPy.Bridge.dll", "LimbusPy.Bridge.Core.dll",
                                          "LimbusPy.Bridge.Harmony.dll"}

    # The layout must never ship Harmony/MonoMod: BepInEx/core owns those assemblies.
    for name in packer.EXTERNAL_ASSEMBLIES:
        assert not os.path.isfile(os.path.join(layout, name)), name

    result = _doctor(layout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "result: healthy" in result.stdout
    assert "limbuspy" in result.stdout


def test_bundled_without_embeddable_is_marked_incomplete(tmp_path):
    layout = _pack(str(tmp_path / "dist"), "bundled")
    manifest = json.load(open(os.path.join(layout, "manifest.json"), encoding="utf-8"))
    assert manifest["variant"] == "bundled"
    assert manifest["complete"] is False
    assert not os.path.isfile(os.path.join(layout, "python", "python.exe"))
    assert "incomplete" in open(os.path.join(layout, "README.md"), encoding="utf-8").read()


def test_bundled_with_embeddable_ships_the_interpreter(tmp_path):
    embed = tmp_path / "embed"
    embed.mkdir()
    (embed / "python.exe").write_bytes(b"")           # stand-in for a real embeddable
    (embed / "python312.zip").write_bytes(b"")
    shutil.copytree(os.path.join(ROOT, "python", "limbuspy"), embed / "limbuspy", dirs_exist_ok=True)

    layout = _pack(str(tmp_path / "dist"), "bundled", embed=str(embed))
    manifest = json.load(open(os.path.join(layout, "manifest.json"), encoding="utf-8"))
    assert manifest["complete"] is True
    assert os.path.isfile(os.path.join(layout, "python", "python.exe"))
    # the package is still installed by the packer, not only by the embeddable
    assert os.path.isfile(os.path.join(layout, "python", "limbuspy", "__init__.py"))


def test_pack_rejects_a_bogus_embeddable(tmp_path):
    bad = tmp_path / "notpython"
    bad.mkdir()
    (bad / "readme.txt").write_text("no interpreter here", encoding="utf-8")
    with pytest.raises(SystemExit):
        packer.main(["--out", str(tmp_path / "dist"), "--variant", "bundled",
                     "--python-embed", str(bad), "--no-build"])


def test_shipped_index_omits_the_big_methods_json(tmp_path):
    layout = _pack(str(tmp_path / "dist"), "system")
    gen = os.path.join(layout, "python", "limbuspy", "_gen")
    if not os.path.isdir(gen):
        pytest.skip("no generated index in this checkout")
    for build in os.listdir(gen):
        directory = os.path.join(gen, build)
        if not os.path.isdir(directory):
            continue
        assert not os.path.isfile(os.path.join(directory, "methods.json")), \
            "methods.json is 34 MB and is generated per build, not shipped"
