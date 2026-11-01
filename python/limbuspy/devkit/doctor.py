"""Self-diagnosis: is this LimbusPy installation actually able to run?

``limbuspy doctor`` answers three questions and prints a report an author can act on:

1. **stubs**   - is an IL2CPP index generated, for which build, and do the promised APIs exist?
2. **python**  - which interpreter, which package version, are the mods discoverable?
3. **kernel**  - can we reach the bridge, and what does the kernel say about itself (caps,
   thread id, handles, dispatcher counters, connected generation)?

Everything is explicit about what is *unknown* vs. *broken*: a missing index is "unknown", a
missing required member is "broken".
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import __version__
from ..il2cpp import GEN_ROOT, available_builds, current_build, default_index


def gen_root() -> str:
    return GEN_ROOT
from ..runtime import protocol
from ..runtime.client import Bridge
from ..runtime.errors import LimbusPyError
from ..runtime.protocol import MsgType

__all__ = ["Check", "Report", "diagnose", "check_stubs", "check_python", "check_kernel", "check_mods"]


@dataclass
class Check:
    name: str
    status: str          # "ok" | "warn" | "broken" | "unknown" | "skipped"
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        # "unknown" (e.g. no generated index on a fresh install) and "skipped" are not failures;
        # only a broken part makes the install unhealthy.
        return self.status in ("ok", "skipped", "unknown", "warn")

    def to_dict(self) -> dict:
        payload = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.data:
            payload["data"] = self.data
        return payload


@dataclass
class Report:
    checks: List[Check] = field(default_factory=list)

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        return check

    @property
    def ok(self) -> bool:
        return not any(check.status == "broken" for check in self.checks)

    def section(self, prefix: str) -> List[Check]:
        return [check for check in self.checks if check.name.startswith(prefix)]

    def to_dict(self) -> dict:
        return {
            "limbuspy": __version__,
            "protocol": protocol.PROTOCOL_VERSION,
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_text(self) -> str:
        width = max([len(check.name) for check in self.checks] + [8])
        lines = []
        for check in self.checks:
            marker = {"ok": "ok  ", "warn": "WARN", "broken": "FAIL",
                      "unknown": "?   ", "skipped": "--  "}.get(check.status, "??  ")
            lines.append("%s %-*s %s" % (marker, width, check.name, check.detail))
        lines.append("")
        lines.append("result: " + ("healthy" if self.ok else "attention needed"))
        return "\n".join(lines)


# --------------------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------------------


def check_python(mods_dir: Optional[str] = None) -> Check:
    detail = "%s %d.%d.%d (%s)" % (
        platform.python_implementation(), sys.version_info[0], sys.version_info[1],
        sys.version_info[2], sys.executable,
    )
    if sys.version_info < (3, 9):
        return Check("python", "broken", detail + " - Python 3.9+ required")
    return Check("python", "ok", detail,
                 {"limbuspy": __version__, "protocol": protocol.PROTOCOL_VERSION})


def check_stubs(build: Optional[str] = None) -> Check:
    builds = available_builds()
    build = build or current_build()
    if not build:
        return Check(
            "stubs", "unknown",
            "no generated index - run: python tools/gen_limbuspy_stubs.py --dump <dump.cs> --out python/limbuspy/_gen",
            {"builds": builds},
        )

    index = default_index(build)
    if index is None:
        if os.path.isfile(os.path.join(gen_root(), str(build), "enums.py")):
            return Check(
                "stubs", "warn",
                "%s: enums shipped without methods.json (index queries unavailable; "
                "run tools/gen_limbuspy_stubs.py to add it)" % build,
                {"builds": builds})
        return Check("stubs", "broken", "index directory %r is incomplete (no methods.json)" % build,
                     {"builds": builds})

    manifest = index.manifest
    counts = manifest.get("counts") or {}
    caps = index.caps or {}
    detail = "%s: %d types, %d enums (generated %s)" % (
        build, len(index), counts.get("enums_written", 0), manifest.get("generated_at", "?"),
    )

    if caps and not caps.get("ok", True):
        missing_types = caps.get("missing_types") or []
        missing_members = caps.get("missing_members") or []
        detail += " - requirements FAILED (%d types, %d member groups missing)" % (
            len(missing_types), len(missing_members))
        return Check("stubs", "broken", detail,
                     {"builds": builds, "caps": caps, "manifest": manifest})

    if caps:
        detail += " - %d required type(s) verified" % caps.get("checked", 0)
    else:
        detail += " - requirements not validated (no caps.json)"
    return Check("stubs", "ok", detail, {"builds": builds, "manifest": manifest, "caps_ok": caps.get("ok")})


def check_mods(mods_dir: Optional[str]) -> Check:
    if not mods_dir:
        return Check("mods", "skipped", "no mods directory given")
    if not os.path.isdir(mods_dir):
        return Check("mods", "warn", "mods directory does not exist: %s" % mods_dir)

    from ..mod.loader import ManifestError, prepare, resolve_order, unload_module

    try:
        prepared = prepare(mods_dir)
    except ManifestError as exc:
        return Check("mods", "broken", "discovery failed: %s" % exc)

    try:
        ordered = resolve_order([manifest for manifest, _module, _instance in prepared.imported])
        names = sorted(manifest.name for manifest in ordered)
        if prepared.errors:
            detail = "%d ok, %d failed to import: %s" % (
                len(names), len(prepared.errors), "; ".join(
                    "%s (%s)" % (name, error) for name, error in sorted(prepared.errors.items())))
            return Check("mods", "broken", detail,
                         {"mods": names, "import_errors": dict(prepared.errors)})
        detail = "%d mod(s): %s" % (len(names), ", ".join(names) if names else "(none)")
        return Check("mods", "ok", detail, {"mods": names})
    except ManifestError as exc:
        names = sorted(manifest.name for manifest, _module, _instance in prepared.imported)
        return Check("mods", "broken",
                     "%d mod(s) but dependencies are broken: %s" % (len(names), exc),
                     {"mods": names})
    finally:
        # Doctor must not leave the mods imported in sys.modules.
        for manifest in prepared.manifests:
            unload_module(manifest)


def check_kernel(bridge: Optional[Bridge], timeout: float = 5.0) -> Check:
    if bridge is None or bridge.closed:
        return Check("kernel", "skipped", "not connected (pass --pipe/--tcp to inspect a running kernel)")

    try:
        result = bridge.request(MsgType.DOCTOR, {}, timeout=timeout)
    except LimbusPyError as exc:
        return Check("kernel", "broken", "DOCTOR request failed: %s" % exc)

    doctor = result.get("value") if isinstance(result, dict) else None
    if not isinstance(doctor, dict):
        return Check("kernel", "broken", "kernel returned an unexpected DOCTOR payload",
                     {"payload": result})

    caps = doctor.get("caps") or []
    handles = doctor.get("handles") or {}
    dispatcher = doctor.get("dispatcher") or {}
    detail = "connected (generation %s, %d handle(s) live, %d cap(s))" % (
        doctor.get("generation", "?"), handles.get("live", 0), len(caps),
    )

    problems = []
    if not protocol.is_compatible(doctor.get("proto")):
        problems.append("protocol mismatch (%s)" % doctor.get("proto"))
    required_caps = {protocol.Caps.REFLECT_MANAGED, protocol.Caps.HANDLE_ARENA}
    missing = required_caps.difference(caps)
    if missing:
        problems.append("missing capability: %s" % ", ".join(sorted(missing)))

    status = "broken" if problems else "ok"
    if problems:
        detail = "; ".join(problems)
    return Check("kernel", status, detail, {"doctor": doctor})


# --------------------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------------------


def diagnose(bridge: Optional[Bridge] = None, build: Optional[str] = None,
             mods_dir: Optional[str] = None, kernel_timeout: float = 5.0) -> Report:
    report = Report()
    report.add(check_python(mods_dir))
    report.add(check_stubs(build))
    report.add(check_mods(mods_dir))
    report.add(check_kernel(bridge, timeout=kernel_timeout))
    return report
