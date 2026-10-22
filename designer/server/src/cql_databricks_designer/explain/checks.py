"""APP-NARRATIVE-OVERLAY, SPEC-EXPLAIN-ACCOUNTING — the checks a generated narration passes before it is shown.

One function, :func:`check`, returns every problem with an instance. An empty list is the only pass: a
narration with any problem is not shown and not cached (no partial narration, no warning-only mode).

THREE KINDS OF PROBLEM, all blocking:

- ``invalid`` — not XML, not valid against ``schema/specification.xsd`` (`SPEC-XSD`), the wrong library,
  prose that claims to be authored, or an element a narration must never carry (`SPEC-DERIVED-APPENDIX`,
  `SPEC-LICENSE`).
- ``identifier`` — an identifier the reference set does not contain: a profile, a define, a library
  (`SPEC-EXPLAIN-INPUTS`: "You never write an identifier. You choose one").
- ``coverage`` — the accounting directions of `SPEC-EXPLAIN-ACCOUNTING`: every define narrated, every
  measurable element citing its figure, every read bound, every claim anchored to lines that exist.

THE REFERENCE SET IS THE DENOMINATOR (`explain/reference_set.py`). Only defines the narrated library
declares are judged; a cross-library reference is checked for its library, not its define, because the
reference set carries one library's defines.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

NS = "https://cql-databricks.databricks.com/spec/1.0"
_Q = "{" + NS + "}"

#: SPEC-XSD — the schema ships in the package, beside the code that validates against it.
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "specification.xsd"

#: Elements a narration never carries. The Appendix, value sets, codes, parameters, metrics and badges are
#: derived from the ELM at render time (`SPEC-DERIVED-APPENDIX`); the copyright and disclaimer are the
#: steward's own words and are only ever quoted (`SPEC-LICENSE`).
FORBIDDEN = ("Appendix", "ValueSet", "Parameter", "Code", "Metric", "Badges", "Copyright", "Disclaimer")

#: Prose fields on a criterion, Concept or Rule. Each carries a `sourceSpan` (forward traceability).
_SPANNED = {_Q + t for t in ("Narrative", "Text", "Rationale", "Guidance")}
#: Inline references. Their `define` names a TARGET, never the element that owns them.
_INLINE = {_Q + t for t in ("ConceptRef", "RuleRef", "MetricRef", "Read")}


@dataclass(frozen=True)
class Problem:
    """One reason a narration is not shown. ``kind`` is ``invalid``, ``identifier`` or ``coverage``."""

    kind: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "message": self.message}


@lru_cache(maxsize=1)
def _schema():
    import xmlschema
    return xmlschema.XMLSchema(str(SCHEMA_PATH))


def check(text: str, reference_set: dict[str, Any]) -> list[Problem]:
    """Every problem with the narration ``text`` of the library ``reference_set`` describes."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as e:
        return [Problem("invalid", f"not well-formed XML: {e}")]
    problems = [Problem("invalid", f"schema: {e.reason} at {e.path}") for e in _schema().iter_errors(text)]
    problems += _identity(root, reference_set)
    problems += _provenance(root)
    problems += [Problem("invalid", f"<{tag}> is derived from the ELM or quoted from the steward; a "
                                    "narration never carries it")
                 for tag in FORBIDDEN if root.find(f".//{_Q}{tag}") is not None]
    problems += _identifiers(root, reference_set)
    problems += _coverage(root, reference_set)
    return problems


def _identity(root: ElementTree.Element, reference_set: dict[str, Any]) -> list[Problem]:
    identity = root.find(_Q + "Identity")
    if identity is None:
        return []  # the schema already reports a missing Identity
    want = (reference_set["library"], reference_set["version"])
    got = (identity.get("library"), identity.get("version"))
    if got != want:
        return [Problem("invalid", f"Identity is {got[0]}@{got[1]}; the narration is for {want[0]}@{want[1]}")]
    return []


def _provenance(root: ElementTree.Element) -> list[Problem]:
    """Generated prose is narrated, AI-drafted and tagged. Structure stays `derived`."""
    bad: list[str] = []
    for element in root.iter():
        provenance = element.get("provenance")
        if provenance is None or provenance == "derived":
            continue
        name = element.tag.replace(_Q, "")
        if provenance != "narrated" or element.get("method") != "ai-drafted":
            bad.append(f"<{name}> is provenance={provenance!r} method={element.get('method')!r}")
        elif not element.get("sourceTag"):
            bad.append(f"<{name}> has no sourceTag")
    return [Problem("invalid", f"generated prose must be provenance='narrated' method='ai-drafted' with a "
                               f"sourceTag: {m}") for m in bad]


def _own(reference_set: dict[str, Any]) -> list[dict[str, Any]]:
    """The defines this library declares — the only ones its own narration answers for."""
    return [d for d in reference_set["defines"] if d["library"] == reference_set["library"]]


def _libraries(reference_set: dict[str, Any]) -> set[str]:
    """This library and every library it includes, by declared name."""
    included = {i["path"].rsplit("/", 1)[-1] for i in reference_set.get("includes", []) if i.get("path")}
    return {reference_set["library"], *included}


def _identifiers(root: ElementTree.Element, reference_set: dict[str, Any]) -> list[Problem]:
    library = reference_set["library"]
    known = {d["name"] for d in _own(reference_set)}
    libraries = _libraries(reference_set)
    profiles = {r["profile"] for d in reference_set["defines"] for r in d["reads"] if r.get("profile")}
    out: list[Problem] = []
    for read in root.iter(_Q + "Read"):
        profile = read.get("profile")
        if profile is None:
            out.append(Problem("identifier", "a <Read> with no profile cannot resolve to anything"))
        elif profile not in profiles:
            out.append(Problem("identifier", f"<Read profile={profile!r}> is not a profile the compiled ELM reads"))
    for tag in ("ConceptRef", "RuleRef", "MetricRef"):
        for ref in root.iter(_Q + tag):
            lib, define = ref.get("library"), ref.get("define")
            if not lib or not define:
                out.append(Problem("identifier", f"<{tag}> needs both library and define"))
            elif lib not in libraries:
                out.append(Problem("identifier", f"<{tag} library={lib!r}> is neither {library} nor a "
                                                 "library it includes"))
            elif lib == library and define not in known:
                out.append(Problem("identifier", f"<{tag} define={define!r}> is not declared by {library}"))
    for tag in ("ConceptRef", "RuleRef"):
        for ref in root.iter(_Q + tag):
            phrase = " ".join((ref.text or "").split())
            if not phrase:
                out.append(Problem("identifier", f"<{tag}> to {ref.get('define')!r} has no phrase"))
            elif phrase == ref.get("define"):
                out.append(Problem("identifier", f"<{tag}> to {phrase!r} repeats the declared name instead of "
                                                 "carrying prose"))
    return out


def _coverage(root: ElementTree.Element, reference_set: dict[str, Any]) -> list[Problem]:
    library = reference_set["library"]
    own = _own(reference_set)
    out: list[Problem] = []

    # Direction 1 — every define is narrated.
    narrated = {e.get("define") for e in root.iter() if e.get("define")}
    missing = sorted(d["name"] for d in own if d["name"] not in narrated)
    if missing:
        out.append(Problem("coverage", f"defines with no narration: {missing}"))

    # Direction 4 — every measurable element cites its figure, and none cites one it does not have.
    cited = {m.get("define") for m in root.iter(_Q + "MetricRef") if m.get("library") == library}
    admits = {d["name"] for d in own if d["metric"]}
    if uncited := sorted(admits - cited):
        out.append(Problem("coverage", f"elements with a metric that cite none: {uncited}"))
    if bogus := sorted((cited & {d["name"] for d in own}) - admits):
        out.append(Problem("coverage", f"a figure is cited for elements that have none: {bogus}"))

    # Direction 3 — every profile the logic reads is bound by some phrase.
    read = {r["profile"] for d in own for r in d["reads"] if r.get("profile")}
    bound = {r.get("profile") for r in root.iter(_Q + "Read")}
    if unbound := sorted(read - bound):
        out.append(Problem("coverage", f"profiles the logic reads but no phrase binds: {unbound}"))

    out += _forward(root)
    out += _spans(root, own)
    out += _anchors(root, own)
    out += _bullets(root)
    return out


def _forward(root: ElementTree.Element) -> list[Problem]:
    """Direction 2 — a prose field on a criterion, Concept or Rule carries a span. Overview prose, the
    measure's or a library's, is about the measure rather than about lines and carries none."""
    out: list[Problem] = []

    def walk(element: ElementTree.Element, owner: str | None, overview: bool) -> None:
        for child in element:
            name = child.tag.replace(_Q, "")
            in_overview = overview or child.tag == _Q + "Overview"
            span = child.get("sourceSpan")
            if in_overview and child.get("provenance") is not None and span is not None:
                out.append(Problem("coverage", f"Overview <{name}> carries sourceSpan {span}; Overview prose "
                                               "is about the measure, not about lines"))
            elif not in_overview and owner and child.tag in _SPANNED and span is None:
                out.append(Problem("coverage", f"<{name}> on {owner} has no sourceSpan"))
            walk(child, owner if child.tag in _INLINE else (child.get("define") or owner), in_overview)

    walk(root, None, False)
    return out


def _spans(root: ElementTree.Element, own: list[dict[str, Any]]) -> list[Problem]:
    """Every span lies inside the body of the define that owns it.

    OWNERSHIP IS THE NEAREST ENCLOSING ELEMENT THAT DECLARES A define, never a reference: a `Concept` nests
    inside its `Population`, and an inline `<ConceptRef define=…>` names its target, not its owner.
    """
    lines = {d["name"]: d["bodyLines"] for d in own}
    out: list[Problem] = []

    def walk(element: ElementTree.Element, owner: str | None) -> None:
        mine = owner if element.tag in _INLINE else (element.get("define") or owner)
        span = element.get("sourceSpan")
        if span and mine in lines:
            numbers = [int(p) for p in span.split("-") if p.strip().isdigit()]
            if numbers and max(numbers) > lines[mine]:
                out.append(Problem("coverage", f"{mine}: span {span} exceeds its {lines[mine]}-line body"))
        for child in element:
            walk(child, mine)

    walk(root, None)
    return out


def _anchors(root: ElementTree.Element, own: list[dict[str, Any]]) -> list[Problem]:
    """SPEC-INLINE-ANCHOR — an inline reference's own span is a line the ELM puts it on."""
    reads = {d["name"]: d["reads"] for d in own}
    refs = {d["name"]: d["references"] for d in own}
    out: list[Problem] = []

    def walk(element: ElementTree.Element, owner: str | None) -> None:
        mine = element.get("define") or owner
        for child in element:
            span = child.get("sourceSpan")
            if child.tag == _Q + "Read" and span and mine in reads:
                sites = {str(r["line"]) for r in reads[mine] if r.get("profile") == child.get("profile") and r.get("line")}
                if span not in sites:
                    out.append(Problem("coverage", f"{mine}: <Read {child.get('profile')}> anchored to {span}, "
                                                   f"the ELM says {sorted(sites) or 'no such read'}"))
            elif child.tag in (_Q + "ConceptRef", _Q + "RuleRef") and span and mine in refs:
                sites = {str(r["line"]) for r in refs[mine] if r["name"] == child.get("define") and r.get("line")}
                if sites and span not in sites:
                    out.append(Problem("coverage", f"{mine}: reference to {child.get('define')!r} anchored to "
                                                   f"{span}, the ELM says {sorted(sites)}"))
            walk(child, mine)

    walk(root, None)
    return out


def _lines(span: str) -> set[int]:
    out: set[int] = set()
    for part in span.split(","):
        bits = [int(b) for b in part.split("-") if b.strip().isdigit()]
        if len(bits) == 1:
            out.add(bits[0])
        elif len(bits) == 2:
            out.update(range(bits[0], bits[1] + 1))
    return out


def _bullets(root: ElementTree.Element) -> list[Problem]:
    """A bullet naming several reads anchors each to its own line, and repeats no span they already cover."""
    out: list[Problem] = []
    for item in root.iter(_Q + "Item"):
        reads = list(item.iter(_Q + "Read"))
        if len(reads) >= 2:
            spans = [r.get("sourceSpan") for r in reads]
            if not all(spans):
                out.append(Problem("coverage", f"a bullet with {len(reads)} reads leaves some unanchored: {spans}"))
            elif len(set(spans)) != len(spans):
                out.append(Problem("coverage", f"two reads on one bullet share an anchor: {spans}"))
        own = item.get("sourceSpan")
        if not own:
            continue
        covered: set[int] = set()
        for tag in ("Read", "ConceptRef", "RuleRef"):
            for ref in item.iter(_Q + tag):
                if ref.get("sourceSpan"):
                    covered |= _lines(ref.get("sourceSpan"))
        if covered and _lines(own) <= covered:
            out.append(Problem("coverage", f"bullet span {own} is fully covered by its components {sorted(covered)}"))
    return out
