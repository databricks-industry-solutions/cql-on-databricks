"""The loaded library closure — multi-library ELM environment (SYNTH-PDS-IR-CONSUMER).

A measure is a CLOSURE of libraries (root + includes). ``Env`` indexes every library's
defines, functions, value sets, direct-reference codes and parameters, and resolves the
include-alias namespace (``ExpressionRef.libraryName`` is the caller's LOCAL alias, not
the library's own name). FHIRHelpers is deliberately NOT loaded: its coercions are
modeled natively by the evaluator — interpreting its ELM (a wall of choice dispatch)
would be slower and no more correct.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import SynthesizerError
from .valuesets import Coding

FHIR_HELPERS = "FHIRHelpers"


@dataclass(frozen=True)
class Code:
    system: str
    code: str
    display: str | None = None


@dataclass
class Lib:
    """One library's indexed ELM."""
    name: str
    version: str
    elm: dict
    defines: dict[str, dict] = field(default_factory=dict)
    functions: dict[str, list[dict]] = field(default_factory=dict)
    valuesets: dict[str, str] = field(default_factory=dict)           # name -> canonical url
    codes: dict[str, Code] = field(default_factory=dict)              # name -> Code
    parameters: dict[str, dict] = field(default_factory=dict)
    includes: dict[str, str] = field(default_factory=dict)            # local alias -> library name


class Env:
    """The library closure, keyed by library NAME; ``root`` is the measure library."""

    def __init__(self, libraries: list[dict], root: str | None = None):
        self.libs: dict[str, Lib] = {}
        for elm in libraries:
            lib = _index(elm)
            if lib.name == FHIR_HELPERS:
                continue
            self.libs[lib.name] = lib
        if not self.libs:
            raise SynthesizerError("no libraries loaded")
        if root is None:
            # The root is the one library nothing else includes.
            included = {name for lib in self.libs.values() for name in lib.includes.values()}
            candidates = [name for name in self.libs if name not in included]
            if len(candidates) != 1:
                raise SynthesizerError(
                    f"cannot infer the root library (candidates: {sorted(candidates)}); pass root=")
            root = candidates[0]
        if root not in self.libs:
            raise SynthesizerError(f"root library {root!r} not in closure {sorted(self.libs)}")
        self.root = root

    @classmethod
    def from_paths(cls, paths: list[str | Path], root: str | None = None) -> Env:
        """Load a closure from ELM JSON file paths, or a single directory of them."""
        if len(paths) == 1 and Path(paths[0]).is_dir():
            paths = sorted(Path(paths[0]).glob("*.elm.json"))
        return cls([json.loads(Path(p).read_text()) for p in paths], root=root)

    def lib(self, name: str) -> Lib:
        lib = self.libs.get(name)
        if lib is None:
            raise SynthesizerError(f"library {name!r} not in the loaded closure {sorted(self.libs)}")
        return lib

    def resolve(self, current: Lib, library_alias: str | None) -> Lib:
        """The target library of a reference: the current one, or the include alias's."""
        if not library_alias:
            return current
        if library_alias == FHIR_HELPERS or current.includes.get(library_alias) == FHIR_HELPERS:
            raise SynthesizerError("FHIRHelpers references are handled natively, not resolved")
        target = current.includes.get(library_alias, library_alias)
        return self.lib(target)


def _index(elm: dict) -> Lib:
    library = elm["library"]
    ident = library["identifier"]
    lib = Lib(name=ident["id"], version=ident.get("version", ""), elm=library)
    for inc in library.get("includes", {}).get("def", []):
        lib.includes[inc.get("localIdentifier") or inc["path"]] = inc["path"]
    for v in library.get("valueSets", {}).get("def", []):
        lib.valuesets[v["name"]] = v["id"]
    systems = {s["name"]: s["id"] for s in library.get("codeSystems", {}).get("def", [])}
    for c in library.get("codes", {}).get("def", []):
        system_ref = c.get("codeSystem", {}).get("name")
        lib.codes[c["name"]] = Code(
            system=systems.get(system_ref, system_ref or ""), code=c["id"],
            display=c.get("display"))
    for p in library.get("parameters", {}).get("def", []):
        lib.parameters[p["name"]] = p
    for d in library.get("statements", {}).get("def", []):
        if d.get("type") == "FunctionDef":
            lib.functions.setdefault(d["name"], []).append(d)
        else:
            lib.defines[d["name"]] = d
    return lib


def coding_of(code: Code) -> Coding:
    return Coding(system=code.system, code=code.code, display=code.display)
