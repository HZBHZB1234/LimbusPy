#!/usr/bin/env python
"""Generate LimbusPy's IL2CPP index from an Il2CppDumper ``dump.cs``.

What it produces (into ``python/limbuspy/_gen/<build>/``):

    manifest.json   provenance: source hash, counts, filters, generator version
    methods.json    the runtime index the Python API uses (types -> fields/properties/methods)
    enums.py        IntEnum classes for the game enums (so scripts stop hardcoding magic numbers)
    types.pyi       type hints for IDEs (the Python equivalent of Modular's MTCSLuaDef.lua)
    caps.json       result of validating a requirements file (which promised APIs still exist)

Usage:

    python tools/gen_limbuspy_stubs.py --dump <dump.cs> --out python/limbuspy/_gen
    python tools/gen_limbuspy_stubs.py --dump ... --only-namespaces ""   # quick run
    python tools/gen_limbuspy_stubs.py --dump ... --requirements tools/requirements/limbuspy.json

Design notes:
  * streaming, single pass, no third-party deps (the tool must run in the shipped "system python"
    build too);
  * the index is deliberately *lossy*: it records signatures, not bodies. Anything the runtime
    needs is resolved by reflection at call time; the index exists for autocomplete, capability
    probes and version-drift reports.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

GENERATOR_VERSION = "0.1.0"
MANIFEST_NAME = "manifest.json"

# --------------------------------------------------------------------------------------
# filters
# --------------------------------------------------------------------------------------

#: Namespaces/types we never index: engines, BCL, third-party libraries. Everything else
#: (global namespace included - that is where most of Limbus' own types live) is a candidate.
EXCLUDED_NAMESPACE_PREFIXES = (
    "System", "Microsoft", "Unity", "UnityEngine", "Il2Cpp", "Mono", "Newtonsoft",
    "JetBrains", "TMPro", "Spine", "DG.", "Coffee.", "Sirenix", "Febucci", "Steamworks",
    "Firebase", "PlayFab", "SocketIO", "SimpleJSON", "Cysharp", "ACTk", "Google",
    "Amazon", "SharpZipLib", "SharpCompress", "NAudio", "FMOD", "DOTween", "Demigiant",
    "InControl", "Rewired", "BestHTTP", "LitJson", "MessagePack", "ProtoBuf", "MessagePack",
    "Antlr4", "Lua", "MonoMod", "Router", "UniTask", "VContainer", "Zenject", "ObjectPool",
)

#: Compiler-generated / generic artifacts.
EXCLUDED_TYPE_PATTERNS = (
    re.compile(r"<.*>"),            # generic definitions and compiler closures
    re.compile(r"^<>"),             # display classes, anonymous types
    re.compile(r"`\d+$"),           # arity suffix coming from metadata
    re.compile(r"^(?:_?PrivateImplementationDetails_|__StaticArrayInitTypeSize=)"),
)

DECLARATION = re.compile(
    r"^(?P<mods>(?:(?:public|internal|private|protected|sealed|abstract|static|readonly|unsafe|partial)\s+)*)"
    r"(?P<kind>class|struct|enum|interface)\s+"
    r"(?P<name>[A-Za-z_<][\w.<>]*)"
    r"(?:\s*:\s*(?P<bases>[^{]+))?"
    r"\s*//\s*TypeDefIndex:\s*(?P<index>\d+)"
)

NAMESPACE = re.compile(r"^//\s*Namespace:\s*(?P<ns>.*?)\s*$")
IMAGE = re.compile(r"^//\s*Image\s+\d+:\s*(?P<name>.*?)\s*-\s*(?P<first>\d+)\s*$")

#: Images (assemblies) that actually contain Limbus' own code. Unity/BCL/third-party images are
#: skipped even when they put types into the global namespace (e.g. URP render features).
KEPT_IMAGE_PREFIXES = (
    "Assembly-CSharp", "CommonAssembly", "ServiceAssembly", "ProjectMoon",
    "InterfacesLibrary", "RpgSystemCore", "SkillViewMarker", "LimbusCompany",
)

FIELD = re.compile(
    r"^(?P<mods>(?:(?:public|internal|private|protected|static|readonly|const|volatile|new)\s+)*)"
    r"(?P<type>[\w.<>\[\],\s?]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)"
    r"\s*(?:=\s*[^;]+)?;\s*(?://\s*(?P<offset>0x[0-9A-Fa-f]+))?"
)

ENUM_MEMBER = re.compile(
    r"^\s*public\s+const\s+(?P<enum>[\w.<>]+)\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<value>-?\d+|0x[0-9A-Fa-f]+)\s*;"
)

PROPERTY = re.compile(
    r"^(?P<mods>(?:(?:public|internal|private|protected|static|virtual|abstract|override|sealed|new)\s+)*)"
    r"(?P<type>[\w.<>\[\],\s?]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\{\s*(?P<accessors>[^}]*)\}"
)

METHOD = re.compile(
    r"^(?P<mods>(?:(?:public|internal|private|protected|static|virtual|abstract|override|sealed|new|extern|unsafe)\s+)*)"
    r"(?P<ret>[\w.<>\[\],\s?]+?)\s+"
    r"(?P<name>\.?[A-Za-z_~][\w]*|<[\w]+>)\s*"
    r"\((?P<params>[^)]*)\)"
)

CS_KEYWORDS = frozenset(
    """abstract as base bool break byte case catch char checked class const continue decimal default
    delegate do double else enum event explicit extern false finally fixed float for foreach goto if
    implicit in int interface internal is lock long namespace new null object operator out override
    params private protected public readonly ref return sbyte sealed short sizeof stackalloc static
    string struct switch this throw true try typeof uint ulong unchecked unsafe ushort using virtual
    void volatile while""".split()
)

PY_KEYWORDS = frozenset(
    """False None True and as assert async await break class continue def del elif else except finally
    for from global if import in is lambda nonlocal not or pass raise return try while with yield""".split()
)


# --------------------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------------------


class TypeRecord:
    __slots__ = ("namespace", "kind", "name", "bases", "typedef_index", "fields", "properties",
                 "methods", "enum_members", "nested", "image")

    def __init__(self, namespace: str, kind: str, name: str, bases: List[str], index: int) -> None:
        self.namespace = namespace
        self.kind = kind
        self.name = name
        self.bases = bases
        self.typedef_index = index
        self.fields: List[dict] = []
        self.properties: List[dict] = []
        self.methods: List[dict] = []
        self.enum_members: List[Tuple[str, int]] = []
        self.nested = "." in name
        self.image = ""

    @property
    def full_name(self) -> str:
        return "%s.%s" % (self.namespace, self.name) if self.namespace else self.name

    def base_name(self) -> Optional[str]:
        for base in self.bases:
            return base
        return None

    def to_index(self) -> dict:
        record = {
            "kind": self.kind,
            "namespace": self.namespace,
            "image": self.image,
            "name": self.name,
            "index": self.typedef_index,
        }
        if self.bases:
            record["bases"] = self.bases
        if self.fields:
            record["fields"] = self.fields
        if self.properties:
            record["properties"] = self.properties
        if self.methods:
            record["methods"] = self.methods
        if self.enum_members:
            record["members"] = [name for name, _value in self.enum_members]
        return record


def _split_params(text: str) -> List[str]:
    if not text.strip():
        return []
    parts: List[str] = []
    depth = 0
    current = []
    for char in text:
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part]


def _parse_params(text: str) -> List[dict]:
    params = []
    for raw in _split_params(text):
        raw = raw.strip()
        if not raw:
            continue
        if "=" in raw:  # default value
            raw = raw.split("=", 1)[0].strip()
        tokens = raw.split()
        if len(tokens) < 2:
            continue
        name = tokens[-1].lstrip("@")
        type_text = " ".join(tokens[:-1])
        type_text = re.sub(r"\b(in|out|ref|params|this)\b\s*", "", type_text).strip()
        params.append({"name": name, "type": type_text})
    return params


def _clean_type(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("?", "?")).strip()


def _is_excluded_type(name: str) -> bool:
    if not name:
        return True
    for pattern in EXCLUDED_TYPE_PATTERNS:
        if pattern.search(name):
            return True
    return False


def _is_excluded_namespace(namespace: str) -> bool:
    if not namespace:
        return False  # global namespace = Limbus' own types
    lowered = namespace
    for prefix in EXCLUDED_NAMESPACE_PREFIXES:
        if lowered == prefix or lowered.startswith(prefix + "."):
            return True
    return False


class DumpParser:
    """Single-pass, tolerant parser for Il2CppDumper's dump.cs."""

    def __init__(self, path: str, only_namespaces: Optional[List[str]] = None,
                 skip_namespaces: Optional[List[str]] = None,
                 keep_all_images: bool = False,
                 kept_image_prefixes=KEPT_IMAGE_PREFIXES) -> None:
        self.path = path
        self.only_namespaces = only_namespaces
        self.skip_namespaces = skip_namespaces or []
        self.keep_all_images = keep_all_images
        self.kept_image_prefixes = tuple(kept_image_prefixes)
        self.images: List[Tuple[int, str]] = []      # (first_typedef_index, image name), sorted
        self.image_counts: Dict[str, int] = {}

    def _image_of(self, typedef_index: int) -> str:
        name = "<unknown>"
        for first, image_name in self.images:
            if typedef_index >= first:
                name = image_name
            else:
                break
        return name

    def _image_kept(self, image: str) -> bool:
        if self.keep_all_images:
            return True
        return any(image.startswith(prefix) for prefix in self.kept_image_prefixes)

    # -- filtering ---------------------------------------------------------------------

    def _keep(self, record: TypeRecord) -> bool:
        if _is_excluded_type(record.name):
            return False
        if self.only_namespaces is not None:
            if record.namespace not in self.only_namespaces:
                return False
        if self.skip_namespaces and any(
            record.namespace == prefix or record.namespace.startswith(prefix + ".")
            for prefix in self.skip_namespaces
        ):
            return False
        return not _is_excluded_namespace(record.namespace)

    # -- main loop ---------------------------------------------------------------------

    def parse(self):
        """Yields :class:`TypeRecord` for every type that passes the filters."""
        namespace = ""
        pending_attributes: List[str] = []
        lines_read = 0

        with io.open(self.path, encoding="utf-8", errors="replace") as handle:
            iterator = iter(handle)
            for line in iterator:
                lines_read += 1
                stripped = line.strip()

                if stripped.startswith("// Image"):
                    header = IMAGE.match(stripped)
                    if header:
                        self.images.append((int(header.group("first")), header.group("name")))
                    continue

                if stripped.startswith("// Namespace:"):
                    namespace = NAMESPACE.match(stripped).group("ns") if NAMESPACE.match(stripped) else ""
                    pending_attributes.clear()
                    continue

                if not stripped:
                    continue
                if stripped.startswith("//"):
                    continue
                if stripped.startswith("["):  # attribute line
                    pending_attributes.append(stripped)
                    continue

                match = DECLARATION.match(stripped)
                if not match:
                    continue

                bases = [
                    part.strip() for part in (match.group("bases") or "").split(",") if part.strip()
                ]
                record = TypeRecord(
                    namespace=namespace,
                    kind=match.group("kind"),
                    name=match.group("name"),
                    bases=bases,
                    index=int(match.group("index")),
                )
                pending_attributes.clear()

                image = self._image_of(record.typedef_index)
                record.image = image
                self.image_counts[image] = self.image_counts.get(image, 0) + 1
                if not self._image_kept(image) or not self._keep(record):
                    # Still consume the body so the braces stay balanced.
                    for line in iterator:
                        if line.rstrip() == "}":
                            break
                    continue

                section = "fields"
                in_comment = False
                for line in iterator:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if stripped.startswith("/*"):
                        in_comment = True
                        continue
                    if in_comment:
                        if "*/" in stripped:
                            in_comment = False
                        continue
                    if stripped.startswith("//"):
                        if "Properties" in stripped:
                            section = "properties"
                        elif "Methods" in stripped:
                            section = "methods"
                        elif "Fields" in stripped:
                            section = "fields"
                        continue
                    if stripped.startswith("["):
                        continue
                    if stripped == "}":
                        break
                    if stripped == "{":
                        continue

                    if record.kind == "enum":
                        member = ENUM_MEMBER.match(stripped)
                        if member:
                            value_text = member.group("value")
                            value = int(value_text, 16) if value_text.startswith("0x") else int(value_text)
                            record.enum_members.append((member.group("name"), value))
                        continue

                    if section == "methods":
                        method = METHOD.match(stripped)
                        if method:
                            record.methods.append({
                                "name": method.group("name"),
                                "ret": _clean_type(method.group("ret")),
                                "params": _parse_params(method.group("params")),
                                "static": "static" in method.group("mods").split(),
                                "virtual": "virtual" in method.group("mods").split()
                                           or "abstract" in method.group("mods").split()
                                           or "override" in method.group("mods").split(),
                            })
                        continue

                    if section == "properties":
                        prop = PROPERTY.match(stripped)
                        if prop:
                            accessors = prop.group("accessors")
                            record.properties.append({
                                "name": prop.group("name"),
                                "type": _clean_type(prop.group("type")),
                                "get": "get" in accessors,
                                "set": "set" in accessors,
                                "static": "static" in prop.group("mods").split(),
                            })
                        continue

                    field = FIELD.match(stripped)
                    if field:
                        record.fields.append({
                            "name": field.group("name"),
                            "type": _clean_type(field.group("type")),
                            "static": "static" in field.group("mods").split(),
                            "readonly": "readonly" in field.group("mods").split()
                                        or "const" in field.group("mods").split(),
                        })

                yield record


# --------------------------------------------------------------------------------------
# emitting
# --------------------------------------------------------------------------------------


def _py_type(cs_type: str) -> str:
    """Maps a C# type expression onto a Python annotation (best effort, never raises)."""
    text = cs_type.strip()
    if not text:
        return "Any"

    # Arrays
    if text.endswith("[]"):
        return "List[%s]" % _py_type(text[:-2])

    generics = {
        "List": "List", "IList": "List", "IReadOnlyList": "List", "IEnumerable": "List",
        "ICollection": "List", "HashSet": "Set", "Dictionary": "Dict", "IDictionary": "Dict",
        "IReadOnlyDictionary": "Dict",
    }
    match = re.match(r"^([\w.]+)\s*<(.+)>$", text)
    if match:
        name, inner = match.group(1), match.group(2)
        args = _split_params(inner)
        if name == "Nullable":
            return "Optional[%s]" % _py_type(args[0] if args else "object")
        if name in generics:
            head = generics[name]
            if head == "Dict":
                left = _py_type(args[0]) if len(args) > 0 else "Any"
                right = _py_type(args[1]) if len(args) > 1 else "Any"
                return "Dict[%s, %s]" % (left, right)
            return "%s[%s]" % (head, ", ".join(_py_type(arg) for arg in args))
        return "%s[%s]" % (name.split(".")[-1], ", ".join(_py_type(arg) for arg in args))

    primitives = {
        "void": "None", "bool": "bool", "string": "str", "char": "str",
        "byte": "int", "sbyte": "int", "short": "int", "ushort": "int", "int": "int",
        "uint": "int", "long": "int", "ulong": "int", "float": "float", "double": "float",
        "decimal": "float", "object": "Any",
    }
    if text in primitives:
        return primitives[text]

    simple = text.split(".")[-1]
    if simple in primitives:
        return primitives[simple]
    return '"%s"' % simple


def _py_identifier(name: str) -> Optional[str]:
    if not name:
        return None
    candidate = name.lstrip("@")
    if candidate in PY_KEYWORDS:
        return None
    if not re.match(r"^[A-Za-z_]\w*$", candidate):
        return None
    return candidate


def emit_enums(records: List[TypeRecord], path: str) -> int:
    written = 0
    lines = [
        '"""Generated by tools/gen_limbuspy_stubs.py - do not edit.',
        "",
        "Game enums so scripts stop hardcoding magic numbers. Import them directly:",
        "",
        "    from limbuspy._gen.<build>.enums import BUFF_UNIQUE_KEYWORD",
        '"""',
        "",
        "from enum import IntEnum",
        "",
    ]
    used = set()
    for record in records:
        if record.kind != "enum" or not record.enum_members:
            continue
        if len(record.enum_members) > 400:
            continue
        name = record.name.replace(".", "_")
        if name in used or name in PY_KEYWORDS:
            continue
        used.add(name)
        lines.append("")
        lines.append("class %s(IntEnum):" % name)
        for member_name, value in record.enum_members:
            ident = _py_identifier(member_name)
            if ident is None:
                lines.append("    # %s = %s" % (member_name, value))
                continue
            lines.append("    %s = %d" % (ident, value))
        written += 1
    if not written:
        lines.append("__all__ = []")
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    return written


def load_curated_types(path: str) -> List[str]:
    """Reads tools/stub_types.txt (one full type name per line, '#' comments)."""
    names: List[str] = []
    if not path or not os.path.isfile(path):
        return names
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(line)
    return names


def select_curated(records: List[TypeRecord], curated: List[str]) -> List[TypeRecord]:
    """Curated set = listed types + their base classes + their nested types."""
    by_name = {record.full_name: record for record in records}
    wanted = set()
    for name in curated:
        record = by_name.get(name)
        if record is None:
            continue
        wanted.add(name)
        for base in record.bases:
            if base in by_name:
                wanted.add(base)
        prefix = name + "."
        for other in records:
            if other.full_name.startswith(prefix):
                wanted.add(other.full_name)
    return [record for record in records if record.full_name in wanted]


def emit_stubs(records: List[TypeRecord], path: str, limit: int = 0) -> int:
    written = 0
    out = [
        '"""Generated by tools/gen_limbuspy_stubs.py - do not edit.',
        "",
        "Type hints for the IL2CPP surface LimbusPy can reach. Signatures only: the actual",
        "objects are looked up through the bridge at call time.",
        '"""',
        "",
        "from typing import Any, Dict, List, Optional, Set",
        "",
    ]
    for record in records:
        if record.kind not in ("class", "struct", "interface"):
            continue
        if limit and written >= limit:
            break
        class_name = record.name.split(".")[-1]
        if not re.match(r"^[A-Za-z_]\w*$", class_name) or class_name in PY_KEYWORDS:
            continue
        bases = [base.split("<")[0].split(".")[-1] for base in record.bases if re.match(r"^[A-Za-z_]\w*$", base.split("<")[0].split(".")[-1])]
        header = "class %s%s:" % (class_name, "(%s)" % ", ".join(bases) if bases else "")
        body: List[str] = []
        for field in record.fields:
            name = _py_identifier(field["name"])
            if name is None:
                continue
            body.append("    %s: %s" % (name, _py_type(field["type"])))
        for prop in record.properties:
            name = _py_identifier(prop["name"])
            if name is None:
                continue
            readonly = "  # read-only" if not prop["set"] else ""
            body.append("    %s: %s%s" % (name, _py_type(prop["type"]), readonly))
        for method in record.methods:
            name = _py_identifier(method["name"].lstrip("."))
            if method["name"].startswith("."):
                name = None
            if name is None:
                continue
            params = ["self"]
            for param in method["params"]:
                ident = _py_identifier(param["name"])
                if ident is None:
                    ident = "arg"
                params.append("%s: %s" % (ident, _py_type(param["type"])))
            body.append("    def %s(%s) -> %s: ..." % (name, ", ".join(params), _py_type(method["ret"])))
        if not body:
            body.append("    ...")
        out.append(header)
        out.extend(body)
        out.append("")
        written += 1
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
    return written


def validate_requirements(index: Dict[str, dict], requirements: dict) -> dict:
    """Matches a requirements file against the index and reports drift."""
    report = {"ok": True, "checked": 0, "missing_types": [], "missing_members": [], "present": []}
    for entry in requirements.get("types") or []:
        type_name = entry["type"]
        report["checked"] += 1
        record = index.get(type_name)
        if record is None:
            report["missing_types"].append(type_name)
            report["ok"] = False
            continue

        missing = []
        for member in entry.get("members") or []:
            if member.startswith("prop:"):
                kind, name = "property", member.split(":", 1)[1]
                found = any(prop["name"] == name for prop in record.get("properties") or [])
            elif member.startswith("field:"):
                kind, name = "field", member.split(":", 1)[1]
                found = any(field["name"] == name for field in record.get("fields") or [])
            elif member.startswith("method:"):
                kind, name = "method", member.split(":", 1)[1]
                found = any(method["name"] == name for method in record.get("methods") or [])
            else:
                # bare name: present under any kind
                kind, name = "any", member
                found = (any(prop["name"] == name for prop in record.get("properties") or [])
                         or any(field["name"] == name for field in record.get("fields") or [])
                         or any(method["name"] == name for method in record.get("methods") or []))
            if not found:
                missing.append(member if kind == "any" else "%s:%s" % (kind, name))

        if missing:
            report["missing_members"].append({"type": type_name, "members": missing})
            report["ok"] = False
        else:
            report["present"].append(type_name)
    return report


# --------------------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------------------


def _sha256_of(path: str, limit: int = 32) -> str:
    digest = hashlib.sha256()
    with io.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:limit]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate the LimbusPy IL2CPP index from dump.cs")
    parser.add_argument("--dump", required=True, help="path to Il2CppDumper's dump.cs")
    parser.add_argument("--out", required=True, help="output root (e.g. python/limbuspy/_gen)")
    parser.add_argument("--build", default=None, help="build id; defaults to the dump's hash prefix")
    parser.add_argument("--only-namespaces", default=None,
                        help="comma separated namespace allowlist (empty string = global namespace)")
    parser.add_argument("--skip-namespaces", default=None, help="comma separated namespace denylist")
    parser.add_argument("--requirements", default=None, help="requirements json to validate")
    parser.add_argument("--stubs", choices=("curated", "all", "off"), default="curated",
                        help="types.pyi scope: curated (tools/stub_types.txt), all, or off")
    parser.add_argument("--stub-types", default=None,
                        help="path to the curated type list (default: <repo>/tools/stub_types.txt)")
    parser.add_argument("--all-images", action="store_true",
                        help="ignore the assembly filter (index Unity/BCL types too)")
    parser.add_argument("--max-types", type=int, default=0, help="stop after N kept types (debug)")
    parser.add_argument("--dry-run", action="store_true", help="parse and report, do not write")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.dump):
        raise SystemExit("dump.cs not found: %s" % args.dump)

    only = None
    if args.only_namespaces is not None:
        only = [part.strip() for part in args.only_namespaces.split(",")]
    skip = [part.strip() for part in args.skip_namespaces.split(",")] if args.skip_namespaces else None

    started = time.time()
    parser_ = DumpParser(args.dump, only, skip, keep_all_images=args.all_images)
    records: List[TypeRecord] = []
    for record in parser_.parse():
        records.append(record)
        if args.max_types and len(records) >= args.max_types:
            break
    elapsed = time.time() - started

    index: Dict[str, dict] = {}
    for record in records:
        index[record.full_name] = record.to_index()

    print("parsed %d types in %.1fs (%d with methods, %d enums)"
          % (len(records),
             elapsed,
             sum(1 for record in records if record.methods),
             sum(1 for record in records if record.kind == "enum")))

    if args.dry_run:
        for name in list(index)[:20]:
            print("  sample:", name)
        return 0

    build = args.build or _sha256_of(args.dump, 12)
    target = os.path.join(args.out, build)
    os.makedirs(target, exist_ok=True)

    methods_path = os.path.join(target, "methods.json")
    io.open(methods_path, "w", encoding="utf-8", newline="\n").write(
        json.dumps({"build": build, "types": index}, ensure_ascii=False, separators=(",", ":"))
    )

    enums_written = emit_enums(records, os.path.join(target, "enums.py"))

    stubs_written = 0
    curated_names: List[str] = []
    if args.stubs != "off":
        stub_records = records
        if args.stubs == "curated":
            curated_path = args.stub_types or os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "stub_types.txt")
            curated_names = load_curated_types(curated_path)
            stub_records = select_curated(records, curated_names)
            missing = [name for name in curated_names
                       if not any(record.full_name == name for record in records)]
            if missing:
                print("curated types missing from this build: %s" % ", ".join(missing))
        stubs_written = emit_stubs(stub_records, os.path.join(target, "types.pyi"))

    caps = None
    if args.requirements and os.path.isfile(args.requirements):
        with io.open(args.requirements, encoding="utf-8") as handle:
            requirements = json.load(handle)
        caps = validate_requirements(index, requirements)
        io.open(os.path.join(target, "caps.json"), "w", encoding="utf-8", newline="\n").write(
            json.dumps(caps, ensure_ascii=False, indent=2)
        )
        if not caps["ok"]:
            print("requirements: %d missing type(s), %d type(s) with missing members"
                  % (len(caps["missing_types"]), len(caps["missing_members"])))

    manifest = {
        "generator": "gen_limbuspy_stubs.py",
        "generator_version": GENERATOR_VERSION,
        "build": build,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "path": os.path.abspath(args.dump),
            "size": os.path.getsize(args.dump),
            "sha256_32": _sha256_of(args.dump),
        },
        "filters": {
            "only_namespaces": only,
            "skip_namespaces": skip,
            "excluded_namespace_prefixes": list(EXCLUDED_NAMESPACE_PREFIXES),
        },
        "counts": {
            "types": len(records),
            "classes": sum(1 for record in records if record.kind == "class"),
            "structs": sum(1 for record in records if record.kind == "struct"),
            "interfaces": sum(1 for record in records if record.kind == "interface"),
            "enums": sum(1 for record in records if record.kind == "enum"),
            "enums_written": enums_written,
            "stubs_written": stubs_written,
            "stubs_scope": args.stubs,
            "curated_types": curated_names,
            "members": sum(len(r.fields) + len(r.properties) + len(r.methods) for r in records),
        },
        "caps_ok": None if caps is None else caps["ok"],
    }
    io.open(os.path.join(target, MANIFEST_NAME), "w", encoding="utf-8", newline="\n").write(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )

    print("wrote %s (methods.json %.1f MB, types.pyi %d classes, enums.py %d enums)"
          % (target,
             os.path.getsize(methods_path) / (1024 * 1024),
             stubs_written,
             enums_written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
