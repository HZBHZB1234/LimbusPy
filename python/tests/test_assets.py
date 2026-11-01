"""`limbuspy.game.assets` tests: real bundle round-trip when one is available, logic otherwise."""

from __future__ import annotations

import glob
import os

import pytest

from limbuspy.game import assets

WORKSPACE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CANDIDATES = (
    glob.glob(os.path.join(WORKSPACE, "work", "dui_export", "*.bundle"))
    + glob.glob(os.path.join(WORKSPACE, "LetheLauncher-Distribution-7",
                             "LimbusCompany_Data", "StreamingAssets", "aa", "*.bundle"))
)

requires_unitypy = pytest.mark.skipif(not assets.available(), reason="UnityPy not installed")
requires_bundle = pytest.mark.skipif(not CANDIDATES, reason="no small bundle in the workspace")


def test_unity_version_is_pinned():
    assert assets.unity_version().startswith("6000.")


def test_available_reports_unitypy():
    assert assets.available() is True


def test_missing_unitypy_raises_actionable_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "UnityPy":
            raise ModuleNotFoundError("No module named 'UnityPy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ModuleNotFoundError) as info:
        assets._unitypy()
    assert "pip install UnityPy" in str(info.value)


def test_open_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        assets.open(str(tmp_path / "nope.bundle"))


def test_patch_object_tracks_entries():
    patch = assets.Patch().replace("TextAsset", "A", b"1").replace_image("Texture2D", "B", "x.png")
    assert len(patch) == 2
    assert patch.entries[0]["op"] == "text"
    assert patch.entries[1]["op"] == "image"


@requires_unitypy
@requires_bundle
def test_bundle_inspection_and_text_roundtrip(tmp_path):
    source = CANDIDATES[0]
    bundle = assets.open(source)
    assert len(bundle) > 0
    assert bundle.objects, "bundle exposes no objects"
    assert bundle.types() == sorted(bundle.types())

    text_assets = bundle.find(type="TextAsset")
    every = bundle.find()
    assert len(every) == len(bundle)
    assert len(bundle.find(type="textasset")) == len(text_assets)  # type match is case-insensitive

    if not text_assets:
        pytest.skip("this bundle has no TextAsset to round-trip")

    asset = text_assets[0]
    original = bundle.export(asset)
    assert isinstance(original, bytes)

    marker = b'{"limbuspy_probe": true}'
    patch = assets.Patch().replace("TextAsset", asset.name, marker)
    result = patch.apply(bundle)
    assert result["applied"] == 1 and result["missing"] == 0

    target = str(tmp_path / "patched.bundle")
    bundle.save(target)
    assert os.path.isfile(target) and os.path.getsize(target) > 0

    reopened = assets.open(target)
    again = reopened.find(type="TextAsset", name=asset.name)
    assert again, "the patched asset survived the rebuild"
    assert reopened.export(again[0]) == marker


@requires_unitypy
@requires_bundle
def test_patch_reports_missing_assets():
    bundle = assets.open(CANDIDATES[0])
    patch = assets.Patch().replace("TextAsset", "definitely-not-here", b"x")
    assert patch.apply(bundle) == {"applied": 0, "missing": 1}
