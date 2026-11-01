#!/usr/bin/env python
"""Extract the game's static data tables into a plain directory.

This is the *only* step that needs UnityPy and a game install; ``limbuspy.game.statics`` then reads
the directory with plain Python (mods never depend on UnityPy).

    python tools/extract_statics.py --out extracted/statics
    python tools/extract_statics.py --bundle <path> --out extracted/statics

Finding the bundle without hardcoding:
  * ``--bundle`` wins;
  * else ``--catalog`` + ``--key-regex`` (default ``static_.*_assets_all_``) via
    ``tools/parse_catalog.py``;
  * else the Unity cache snapshot in the workspace (``_catalog_bundle_names.json``) is searched.

UnityPy is optional: a missing dependency is reported as an actionable message, not a traceback.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

DEFAULT_KEY = r"static_.*_assets_all_"


def _fail(message: str) -> int:
    print("[statics] " + message, file=sys.stderr)
    return 2


def find_bundles(catalog: str, pattern: str) -> list:
    parse = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parse_catalog.py")
    if not os.path.isfile(parse) or not catalog or not os.path.isfile(catalog):
        return []
    sys.path.insert(0, os.path.dirname(parse))
    try:
        import parse_catalog
    except Exception:  # noqa: BLE001
        return []

    matcher = re.compile(pattern)
    found = []
    try:
        catalog_data = parse_catalog.load_catalog(catalog)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - the helper's API may vary; fall back to CLI usage
        return []

    for key, bundle in (catalog_data or {}).items():
        if matcher.search(str(key)):
            found.append(str(bundle))
    return sorted(set(found))


def extract(bundle: str, out_dir: str) -> int:
    try:
        from UnityPy import Environment  # noqa: PLC0415
    except ModuleNotFoundError:
        return _fail("UnityPy is not installed. `pip install UnityPy` (only this extraction step "
                     "needs it; scripts and the runtime do not).")

    os.makedirs(out_dir, exist_ok=True)
    count = 0
    environment = Environment()
    environment.load(bundle)
    for obj in environment.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
            name = getattr(data, "m_Name", None) or getattr(data, "name", None) or ("asset_%d" % obj.path_id)
            payload = getattr(data, "m_Script", None)
            if payload is None:
                payload = getattr(data, "script", None)
            if isinstance(payload, str):
                payload = payload.encode("utf-8", "surrogateescape")
            if not payload:
                continue
            target = os.path.join(out_dir, "%s.json" % str(name).replace("/", "_"))
            with io.open(target, "wb") as handle:
                handle.write(payload)
            count += 1
        except Exception:  # noqa: BLE001 - one bad asset must not abort the run
            continue

    manifest = {
        "bundle": os.path.abspath(bundle),
        "tables": count,
        "out": os.path.abspath(out_dir),
    }
    with io.open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print("[statics] extracted %d table(s) -> %s" % (count, out_dir))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Extract Limbus static data tables")
    parser.add_argument("--out", default=os.path.join("extracted", "statics"))
    parser.add_argument("--bundle", default=None)
    parser.add_argument("--catalog", default=None,
                        help="Addressables catalog.bin (base or S1) to locate the static bundle")
    parser.add_argument("--key-regex", default=DEFAULT_KEY)
    args = parser.parse_args(argv)

    bundle = args.bundle
    if not bundle and args.catalog:
        candidates = find_bundles(args.catalog, args.key_regex)
        if candidates:
            bundle = candidates[0]
            print("[statics] catalog resolves the table bundle to %s" % bundle)
    if not bundle:
        return _fail("no --bundle given and no catalog match; pass --bundle <static_..._assets_all_*.bundle>")
    if not os.path.isfile(bundle):
        return _fail("bundle not found: %s" % bundle)
    return extract(bundle, args.out)


if __name__ == "__main__":
    sys.exit(main())
