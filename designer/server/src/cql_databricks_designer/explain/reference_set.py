"""SPEC-EXPLAIN-INPUTS — the resolved reference set a narration is generated against.

WHY THIS IS SERVER-SIDE (`APP-SPEC-MODEL`, option 1). The backend produces the ELM: it calls the
Compiler Service, holds the result, and composes the Specification it serves. So it is the only place
that can state, as a FACT, that ``qicore-condition-problems-health-concerns`` is a profile *this* measure
retrieves — and a generator that has to infer that from CQL text is a generator that will sometimes infer
it wrong. The reference set makes every identifier a narration needs SELECTABLE rather than typeable
(`SPEC-EXPLAIN-VOICE`: "You never write an identifier. You choose one").

WHAT IT IS FOR:

1. **The browser's reads** (`specRetrieves.ts`): served on `/api/translate` as `referenceSets`, one per
   library in the closure.
2. **A section's AI draft and its accounting check** (`DOC-SECTION-EXPLAIN`, `SPEC-EXPLAIN-ACCOUNTING`).
   Coverage needs a denominator, and this is it: every reference site, every read, every terminology
   binding, every metric key, per define. Not built yet; the library-wide generator and checker that
   used it were removed (`DOC-EXPLAIN-REMOVE`).
3. **`sourceSpan` derivation.** Spans come from ELM locators converted to body-relative line ranges, never
   counted by eye — a counted range breaks silently when the registry's CQL is reformatted.

ONE DERIVATION, NOT A SECOND COPY. This is the server-side home of what `specRetrieves.ts` /
`specModel.ts` derive in the browser today; the client is to consume it rather than re-derive it. Two
implementations of "what is a read" is the exact failure `APP-SPEC-MODEL` forbids, so this module
landing means the client's copy is on its way out, not that there are now two.

NOTHING HERE IS INTERPRETED. Every field is read off the ELM or computed from a locator. No prose, no
classification, no judgement — those are the generator's, and keeping them out is what lets this be
regenerated from a compile without review.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: ELM node types that name another statement. `FunctionRef` is included because a fluent call
#: (`ExampleCondition.confirmed()`) is a reference the reader can follow, and the renderer links it.
_REF_TYPES = ("ExpressionRef", "FunctionRef")


@dataclass
class Read:
    """One retrieve, with the profile that discriminates it (`SPEC-READ-REF`).

    ``profile`` is the ELM ``templateId``'s last segment — the value the compiler emitted and the value a
    ``<Read>`` binding must name. PROFILE IS THE DISCRIMINATOR: *problem list* and *encounter diagnosis*
    are two profiles of one resource type, so type alone cannot tell them apart.
    """

    resource_type: str
    profile: str | None
    value_set: str | None
    #: A DIRECT REFERENCE CODE used as the filter, which is NOT the same thing as a value set and must
    #: not be reported as one. Rare, and distinguished because the document says "code in X" versus
    #: "code in <the X value set>" — and because a reader following the reference lands in a different
    #: Appendix table (`SPEC-TERMINOLOGY-REF`). Exactly one of `value_set` / `code` is ever set.
    code: str | None
    code_property: str | None
    line: int | None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"resourceType": self.resource_type}
        if self.profile:
            out["profile"] = self.profile
        if self.value_set:
            out["valueSet"] = self.value_set
        if self.code:
            out["code"] = self.code
        if self.code_property:
            out["codeProperty"] = self.code_property
        if self.line is not None:
            out["line"] = self.line
        return out


@dataclass
class Reference:
    """One site where a define reaches another define or function."""

    name: str
    library: str | None
    line: int | None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.library:
            out["library"] = self.library
        if self.line is not None:
            out["line"] = self.line
        return out


@dataclass
class Define:
    """One statement, and everything a narration of it may cite."""

    name: str
    library: str
    returns: str | None
    locator: str | None
    body: str
    body_lines: int
    references: list[Reference] = field(default_factory=list)
    reads: list[Read] = field(default_factory=list)
    #: LIBRARY-QUALIFIED, both of them. `SPEC-TERMINOLOGY-REF` requires a narrator to SELECT by `library=`
    #: (unqualified names are dropped rather than guessed, because two libraries in one closure may bind
    #: one name to different OIDs), and the document's terminology registry keys on `library::name` for
    #: the same reason.
    value_sets: list[Reference] = field(default_factory=list)
    codes: list[Reference] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)

    @property
    def metric_key(self) -> str:
        """SPEC-METRIC-REF — the key a `<MetricRef>` cites. Library-qualified, always."""
        return f"{self.library}::{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "library": self.library,
            "returns": self.returns,
            "locator": self.locator,
            "bodyLines": self.body_lines,
            "body": self.body,
            # SPEC-METRIC — a metric's FORM is a typed dispatch off the result type, and an Interval or
            # DateTime define has none. Stated here so the generator knows whether direction 4 of
            # `SPEC-EXPLAIN-ACCOUNTING` applies to this element at all.
            "metric": {"key": self.metric_key, "grain": _grain(self.returns)} if _has_metric(self.returns) else None,
            "references": [r.to_dict() for r in self.references],
            "reads": [r.to_dict() for r in self.reads],
            "valueSets": [v.to_dict() for v in self.value_sets],
            "codes": [c.to_dict() for c in self.codes],
            "parameters": self.parameters,
        }


@dataclass
class ReferenceSet:
    """One library's resolved reference set — the whole generation input."""

    library: str
    version: str
    translator_options: str | None
    includes: list[dict[str, str]]
    declared_value_sets: list[dict[str, str]]
    declared_codes: list[dict[str, str]]
    declared_parameters: list[dict[str, Any]]
    defines: list[Define]

    def to_dict(self) -> dict[str, Any]:
        return {
            "library": self.library,
            "version": self.version,
            "translatorOptions": self.translator_options,
            "includes": self.includes,
            "declared": {
                "valueSets": self.declared_value_sets,
                "codes": self.declared_codes,
                "parameters": self.declared_parameters,
            },
            "defines": [d.to_dict() for d in self.defines],
        }


def build_reference_set(elm: dict[str, Any]) -> ReferenceSet:
    """Derive the reference set from one library's ELM JSON.

    Requires the translator's ``EnableLocators`` and ``EnableAnnotations``; ``EnableResultTypes`` is what
    makes ``returns`` (and therefore the metric form) derivable. Their absence is reported rather than
    approximated — `SPEC-METRIC` is explicit that an underivable figure is stated as absent, never guessed.
    """
    lib = elm.get("library") or elm
    ident = lib.get("identifier") or {}
    name = ident.get("id") or "?"
    version = ident.get("version") or "?"

    options = None
    for ann in lib.get("annotation") or []:
        if ann.get("type") == "CqlToElmInfo":
            options = ann.get("translatorOptions")
    if options and "EnableLocators" not in options:
        log.warning("%s@%s compiled without EnableLocators — spans are not derivable", name, version)

    includes = [
        {"localIdentifier": i.get("localIdentifier", ""), "path": i.get("path", ""),
         "version": i.get("version", "")}
        for i in (lib.get("includes") or {}).get("def", [])
    ]
    declared_value_sets = [
        {"name": v.get("name", ""), "id": v.get("id", "")}
        for v in (lib.get("valueSets") or {}).get("def", [])
    ]
    declared_codes = [
        {"name": c.get("name", ""), "code": (c.get("id") or ""),
         "system": ((c.get("codeSystem") or {}).get("name") or "")}
        for c in (lib.get("codes") or {}).get("def", [])
    ]
    declared_parameters = [
        {"name": p.get("name", ""), "type": _type_name(p.get("parameterTypeSpecifier"))}
        for p in (lib.get("parameters") or {}).get("def", [])
    ]

    aliases = _aliases(lib)
    defines: list[Define] = []
    for stmt in (lib.get("statements") or {}).get("def", []):
        # The implicit `Patient` context statement is not a narratable element — it declares the context
        # rather than computing anything, and the document does not render it.
        if stmt.get("name") == "Patient" and (stmt.get("expression") or {}).get("type") == "SingletonFrom":
            continue
        defines.append(_define(stmt, name, aliases))

    return ReferenceSet(
        library=name, version=version, translator_options=options, includes=includes,
        declared_value_sets=declared_value_sets, declared_codes=declared_codes,
        declared_parameters=declared_parameters, defines=defines,
    )


def _owner(node: dict[str, Any], aliases: dict[str, str], own: str) -> str:
    """Which library DECLARES the thing this node names.

    `Helpers."Some Encounter"` carries `libraryName: "Helpers"` — a LOCAL ALIAS, not a library name —
    so it resolves through the ELM's own `includes` table rather than by assuming the two match. An
    unqualified reference is declared by the library doing the referencing.
    """
    alias = node.get("libraryName")
    if not alias:
        return own
    return aliases.get(alias, alias)


def _aliases(lib: dict[str, Any]) -> dict[str, str]:
    """Local include alias -> declared library name, from the ELM's own include table."""
    out: dict[str, str] = {}
    for inc in (lib.get("includes") or {}).get("def", []):
        local, path = inc.get("localIdentifier"), inc.get("path")
        if local and path:
            out[local] = path.rsplit("/", 1)[-1]
    return out


def _define(stmt: dict[str, Any], library: str, aliases: dict[str, str] | None = None) -> Define:
    aliases = aliases or {}
    locator = stmt.get("locator")
    start = _start_line(locator)
    body = _source(stmt)
    # The body is what a `sourceSpan` counts within: everything after the `define "…":` line, with the
    # common indent removed, which is exactly what the renderer slices and shows.
    body_only = _body_only(body)

    refs: list[Reference] = []
    reads: list[Read] = []
    value_sets: list[Reference] = []
    codes: list[Reference] = []
    params: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("type")
        line = _body_line(node.get("locator"), start)
        if kind in _REF_TYPES and node.get("name"):
            refs.append(Reference(name=node["name"], library=node.get("libraryName"), line=line))
        elif kind == "Retrieve":
            value_set, code = _filter_of(node.get("codes"))
            reads.append(Read(
                resource_type=_local(node.get("dataType")),
                profile=_profile(node.get("templateId")),
                value_set=value_set,
                code=code,
                code_property=node.get("codeProperty"),
                line=line,
            ))
        elif kind == "ValueSetRef" and node.get("name"):
            value_sets.append(Reference(name=node["name"], library=_owner(node, aliases, library),
                                        line=line))
        elif kind in ("CodeRef", "ConceptRef") and node.get("name"):
            codes.append(Reference(name=node["name"], library=_owner(node, aliases, library), line=line))
        elif kind == "ParameterRef" and node.get("name"):
            params.append(node["name"])
        for key, value in node.items():
            # `annotation` holds the source text, not logic — walking it yields phantom references.
            if key == "annotation":
                continue
            visit(value)

    visit(stmt.get("expression"))
    # A retrieve's own value set arrives via `codes`; keep the union so a narration can account for
    # terminology whether it filters a read or is tested separately.
    # A retrieve's own filter arrives via `codes` rather than as a bare `ValueSetRef`, so it is unioned
    # in: a narration accounts for terminology whether it filters a read or is tested separately. It is
    # attributed to THIS library, which is where an unqualified filter resolves.
    seen = {(v.library, v.name) for v in value_sets}
    for r in reads:
        if r.value_set and (library, r.value_set) not in seen:
            value_sets.append(Reference(name=r.value_set, library=library, line=r.line))
            seen.add((library, r.value_set))
    seen_codes = {(c.library, c.name) for c in codes}
    for r in reads:
        if r.code and (library, r.code) not in seen_codes:
            codes.append(Reference(name=r.code, library=library, line=r.line))
            seen_codes.add((library, r.code))

    return Define(
        name=stmt.get("name", "?"), library=library, returns=_type_name(
            stmt.get("resultTypeSpecifier") or stmt.get("resultTypeName")),
        locator=locator, body=body_only, body_lines=len(body_only.splitlines()),
        references=_dedupe_refs(refs), reads=_dedupe_reads(reads),
        value_sets=_dedupe_refs(value_sets), codes=_dedupe_refs(codes),
        parameters=sorted(set(params)),
    )


def _source(stmt: dict[str, Any]) -> str:
    """The statement's CQL, reconstructed from its annotation tree.

    The annotation is the translator's own record of the source it parsed, so this is the same text the
    renderer shows — not a re-print of it.
    """
    parts: list[str] = []

    def rec(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for v in node.get("value") or []:
            parts.append(v)
        for child in node.get("s") or []:
            rec(child)

    for ann in stmt.get("annotation") or []:
        if ann.get("type") == "Annotation" and ann.get("s"):
            rec(ann["s"])
    return "".join(parts)


def _body_only(source: str) -> str:
    """Everything after the `define "…":` line, common indent removed — what a span counts within."""
    lines = source.split("\n")
    if len(lines) <= 1:
        return source.strip()
    body = lines[1:]
    pad = [len(l) - len(l.lstrip()) for l in body if l.strip()]
    cut = min(pad) if pad else 0
    return "\n".join(l[cut:] if len(l) >= cut else l for l in body).rstrip()


def _start_line(locator: str | None) -> int | None:
    if not locator or ":" not in locator:
        return None
    try:
        return int(locator.split(":", 1)[0])
    except ValueError:
        return None


def _body_line(locator: str | None, start: int | None) -> int | None:
    """A node's line as a BODY-relative 1-based number, which is what `sourceSpan` uses.

    The body begins on the line after `define "…":`, so a node on absolute line ``start + 1`` is body
    line 1 — hence ``abs - start`` rather than ``abs - start + 1``.
    """
    if start is None:
        return None
    at = _start_line(locator)
    if at is None:
        return None
    line = at - start
    return line if line >= 1 else None


def _local(qname: str | None) -> str:
    """`{http://hl7.org/fhir}Condition` -> `Condition`."""
    if not qname:
        return "?"
    return qname.rsplit("}", 1)[-1]


def _profile(template_id: str | None) -> str | None:
    """The `templateId`'s last segment — the exact string a `<Read profile="…">` must name."""
    if not template_id:
        return None
    return template_id.rstrip("/").rsplit("/", 1)[-1]


def _filter_of(codes: Any) -> tuple[str | None, str | None]:
    """The terminology a ``Retrieve.codes`` binds to, as ``(value_set, code)`` with at most one set.

    THE TWO ARE NOT INTERCHANGEABLE and collapsing them was a real defect: a value set is an EXTERNAL
    list expanded from terminology, a direct reference code is ONE concept stated inline, and they live
    in different Appendix tables. Reporting a code as a value set sent a reader to look up an OID that
    does not exist (`SPEC-TERMINOLOGY-REF`).

    The walk finds the FIRST of either — a retrieve binds one filter — and looks through the wrapper
    nodes the translator puts between `codes` and the reference (`ToList`, `operand`, `element`).
    """
    found: tuple[str | None, str | None] = (None, None)

    def visit(node: Any) -> bool:
        nonlocal found
        if isinstance(node, list):
            return any(visit(n) for n in node)
        if not isinstance(node, dict):
            return False
        kind, name = node.get("type"), node.get("name")
        if kind == "ValueSetRef" and name:
            found = (name, None)
            return True
        if kind == "CodeRef" and name:
            found = (None, name)
            return True
        return any(visit(v) for v in node.values())

    visit(codes)
    if found == (None, None):
        # A `codes` shape this walk does not recognise still names SOMETHING; falling back to the old
        # name-scan keeps those reads filtered rather than silently unfiltered — which would render as
        # "every record" and overstate what the logic reads.
        return (_code_name(codes), None)
    return found


def _code_name(codes: Any) -> str | None:
    if not isinstance(codes, dict):
        return None
    if codes.get("name"):
        return codes["name"]
    for key in ("operand", "element", "codes"):
        inner = codes.get(key)
        found = _code_name(inner if isinstance(inner, dict) else
                           (inner[0] if isinstance(inner, list) and inner else None))
        if found:
            return found
    return None


def _type_name(spec: Any) -> str | None:
    """A result type as something a reader can be shown. `List<Condition>`, `Boolean`, `Interval<Date>`."""
    if spec is None:
        return None
    if isinstance(spec, str):
        return _local(spec)
    kind = spec.get("type")
    if kind == "ListTypeSpecifier":
        return f"List<{_type_name(spec.get('elementType')) or '?'}>"
    if kind == "IntervalTypeSpecifier":
        return f"Interval<{_type_name(spec.get('pointType')) or '?'}>"
    if kind == "NamedTypeSpecifier":
        return _local(spec.get("name"))
    if kind == "ChoiceTypeSpecifier":
        inner = [_type_name(c) or "?" for c in spec.get("choice") or []]
        return f"Choice<{', '.join(inner)}>" if inner else "Choice"
    if kind == "TupleTypeSpecifier":
        return "Tuple"
    return _local(spec.get("name")) if spec.get("name") else None


def _has_metric(returns: str | None) -> bool:
    """SPEC-METRIC — Interval and DateTime defines have no metric, by construction."""
    if not returns:
        return False
    return not (returns.startswith("Interval") or returns in ("DateTime", "Date", "Time"))


def _grain(returns: str | None) -> str:
    """SPEC-METRIC — a list-valued define has both grains; a Boolean says something about the subject only."""
    return "element" if (returns or "").startswith("List<") else "subject"


def _dedupe_refs(refs: list[Reference]) -> list[Reference]:
    """One entry per referenced name, at its FIRST site. Accounting is per thing referenced, not per site
    (`SPEC-EXPLAIN-ACCOUNTING`: "account once, not every time"), and the first site is where a reader
    meets it."""
    seen: dict[tuple[str | None, str], Reference] = {}
    for r in refs:
        key = (r.library, r.name)
        if key not in seen or (r.line or 10**6) < (seen[key].line or 10**6):
            seen[key] = r
    return sorted(seen.values(), key=lambda r: (r.line or 10**6, r.name))


def _dedupe_reads(reads: list[Read]) -> list[Read]:
    seen: dict[tuple[str, str | None, str | None], Read] = {}
    for r in reads:
        key = (r.resource_type, r.profile, r.value_set)
        if key not in seen or (r.line or 10**6) < (seen[key].line or 10**6):
            seen[key] = r
    return sorted(seen.values(), key=lambda r: (r.line or 10**6, r.resource_type))


def main(argv: list[str]) -> int:
    """`python -m cql_databricks_designer.explain.reference_set <elm.json> [-o out.json]`.

    A CLI because the first consumer is a generation SESSION rather than a request: the artifact is
    committed so a narration can be produced, reviewed and re-produced without a live compile.
    """
    if not argv:
        print(__doc__)
        print("usage: python -m cql_databricks_designer.explain.reference_set <elm.json> [-o out.json]")
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        elm = json.load(fh)
    payload = build_reference_set(elm).to_dict()
    text = json.dumps(payload, indent=2)
    if len(argv) >= 3 and argv[1] == "-o":
        with open(argv[2], "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        counts = payload["defines"]
        print(f"{payload['library']}@{payload['version']}: {len(counts)} defines -> {argv[2]}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
