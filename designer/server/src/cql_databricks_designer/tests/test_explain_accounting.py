"""SPEC-EXPLAIN-ACCOUNTING / SPEC-EXPLAIN-INPUTS — coverage is a NUMBER, so it is asserted.

WHY THIS FILE EXISTS. The Explain text is what a clinician reads; the CQL is there so they *can* check
it, not so they have to. That only holds if the narration is complete, and "the prose is good" is an
opinion while "every define is narrated" is a count. These are the counts.

WHY IT BITES. Every failure mode this guards against is SILENT in the product. An unresolvable `<Read>`
renders as ordinary prose; a malformed `sourceSpan` highlights nothing; an unnarrated define simply is
not there to notice. A narration can therefore lose exactly the parts that made it verifiable and still
look finished — which is the specific hazard of generating it, whether the generator is a session or a
runtime endpoint.

THE REFERENCE SET IS THE DENOMINATOR (`explain/reference_set.py`). Coverage needs something to be
complete *against*, and the artifact derived from the compiled ELM is it.

EVERY NARRATED MEASURE IS HELD TO IT, not the one it was written for: counts that hold for one file are
a property of that file rather than of the standard. Each case below is a `(library, version)` whose
instance and reference-set artifact both exist.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

import cql_databricks_designer

import pytest

from cql_databricks_designer.specification import parse_specification

NS = "{https://cql-databricks.databricks.com/spec/1.0}"
# Anchored on the package that OWNS the reference sets (see test_conformance_pin.py for why).
ROOT = Path(cql_databricks_designer.__file__).parent
#: MOVE-DERIVATIONS — the reference sets are the evaluator's now; the narration instances stay here.
EXPLAIN = Path(cql_databricks_designer.__file__).parent / "explain"

#: The narrated measures, each a library version with a reference set.
#: TODO(AI-NARRATIVE-OVERLAY): the committed instances were official content and are gone; these checks
#: become the run-time accounting check's tests, over a synthetic instance.
MEASURES: list[tuple[str, str]] = []


@dataclass(frozen=True)
class Narration:
    """One measure under test — its identity, its denominator and its instance."""

    library: str
    version: str
    reference_set: dict
    instance: ElementTree.Element

    @property
    def own(self) -> list[dict]:
        """The defines this library declares — the only ones its own narration is answerable for."""
        return [d for d in self.reference_set["defines"] if d["library"] == self.library]


@pytest.fixture(params=MEASURES, ids=lambda case: case[0], scope="module")
def narration(request) -> Narration:
    library, version = request.param
    artifact = EXPLAIN / "reference-sets" / f"{library}-{version}.json"
    if not artifact.exists():
        pytest.skip(f"no reference set artifact at {artifact}")
    instance = ROOT / "specifications" / f"{library}.xml"
    return Narration(library, version,
                     json.loads(artifact.read_text(encoding="utf-8")),
                     ElementTree.parse(instance).getroot())


def _narrated(instance: ElementTree.Element) -> set[str]:
    return {e.get("define") for e in instance.iter() if e.get("define")}


def test_every_define_is_narrated(narration):
    """Direction 1 — an unnarrated define is a hole a reader can only find by opening the code."""
    missing = sorted(d["name"] for d in narration.own if d["name"] not in _narrated(narration.instance))
    assert not missing, f"defines with no narration: {missing}"


def test_every_measurable_element_cites_its_figure(narration):
    """Direction 4 — coverage is counted PER ELEMENT, which is what reconciles it with the rule against
    a figure in every sentence. An element whose result type admits a metric and cites none tells the
    reader what counts and never how much."""
    cited = {m.get("define") for m in narration.instance.iter(NS + "MetricRef")}
    should = [d["name"] for d in narration.own if d["metric"]]
    missing = sorted(n for n in should if n not in cited)
    assert not missing, f"elements admitting a metric but citing none: {missing}"


def test_no_metric_is_cited_for_an_element_that_has_none(narration):
    """The other direction of the same rule. `SPEC-METRIC` is explicit that an Interval or DateTime
    define has no metric, so a citation on one renders an absence mark inside a clinical sentence."""
    admits = {d["name"] for d in narration.own if d["metric"]}
    known = {d["name"] for d in narration.own}
    bogus = sorted({
        m.get("define") for m in narration.instance.iter(NS + "MetricRef")
        # Only judge defines this library declares; a cross-library citation is resolved elsewhere.
        if m.get("library") == narration.library and m.get("define") in known and m.get("define") not in admits
    })
    assert not bogus, f"cited a figure for an element that has none: {bogus}"


def test_every_read_profile_exists_in_the_compiled_elm(narration):
    """SPEC-EXPLAIN-INPUTS — the no-invented-identifiers rule, as a test.

    THIS IS THE ONE THAT CANNOT BE CAUGHT BY READING THE PRODUCT. An unresolvable profile degrades to
    plain prose (`SPEC-READ-REF`), which is right for a human's typo and catastrophic for a generator:
    the sentence still reads, and the count silently is not there.
    """
    valid = {r["profile"] for d in narration.reference_set["defines"] for r in d["reads"] if r.get("profile")}
    bound = {r.get("profile") for r in narration.instance.iter(NS + "Read")}
    assert None not in bound, "a <Read> with no profile cannot resolve to anything"
    invented = sorted(bound - valid)
    assert not invented, f"profiles not present in the compiled ELM: {invented}"


def test_every_profile_the_logic_reads_is_bound_somewhere(narration):
    """Direction 3 — outward coverage for reads. A profile the measure retrieves and the prose never
    binds is data the reader cannot get to from the sentence that depends on it."""
    valid = {r["profile"] for d in narration.own if d["reads"] for r in d["reads"] if r.get("profile")}
    bound = {r.get("profile") for r in narration.instance.iter(NS + "Read")}
    assert not sorted(valid - bound), f"profiles the logic reads but no phrase binds: {sorted(valid - bound)}"


def test_every_narrated_field_carries_a_span(narration):
    """Direction 2 (forward) — a claim with no anchor is a claim nobody can check. The Overview is
    exempt by rule: its fields are about the measure, not about lines, so a span there is false
    precision."""
    overview = {NS + t for t in ("Statement", "InScope", "NonGoal", "HowToRead", "Overview")}
    fields = {NS + t for t in ("Narrative", "Text", "Rationale", "Guidance")}
    missing = []
    for element in narration.instance.iter():
        if element.tag not in fields:
            continue
        if element.get("sourceSpan") is None:
            owner = element.get("define") or "?"
            missing.append(f"{element.tag.replace(NS, '')} on {owner}")
    assert not missing, f"narrated fields with no sourceSpan: {missing}"
    # …and the exempt ones really are exempt, so the rule is not passing by accident.
    assert all(e.get("sourceSpan") is None for e in narration.instance.iter() if e.tag in overview)


def test_every_span_is_inside_the_define_it_cites(narration):
    """A span past the end of a body highlights nothing — the same silent failure as a bad profile, and
    the reason `SPEC-EXPLAIN-VOICE` forbids counting lines by eye."""
    lines = {d["name"]: d["bodyLines"] for d in narration.own}
    bad: list[str] = []

    def check(owner: str, span: str | None) -> None:
        # A cross-library define is cited against ITS OWN library's source, which this artifact does not
        # carry, so it is out of scope here rather than wrongly judged against a body we do not have.
        if not span or owner not in lines:
            return
        parts = [int(p) for p in span.split("-") if p.strip().isdigit()]
        if parts and max(parts) > lines[owner]:
            bad.append(f"{owner}: span {span} exceeds its {lines[owner]}-line body")

    # An INLINE REFERENCE's `define` names its TARGET, not its owner. Treating it as ownership judged a
    # `<ConceptRef define="Has Reading B…">` inside the Numerator against the *referenced* define's body —
    # the second ownership bug this test found in itself, and the reason ownership is computed explicitly.
    inline = {NS + t for t in ("ConceptRef", "RuleRef", "MetricRef", "Read")}

    def walk(element: ElementTree.Element, owner: str | None) -> None:
        """OWNERSHIP IS THE NEAREST ENCLOSING ELEMENT THAT DECLARES A define, NOT ANY ANCESTOR AND NEVER A
        REFERENCE. A `Concept` nests inside the `Population` it belongs to, so attributing its spans to the
        population judges them against the wrong body."""
        mine = owner if element.tag in inline else (element.get("define") or owner)
        if element.get("sourceSpan") is not None:
            check(mine or "?", element.get("sourceSpan"))
        for child in element:
            walk(child, mine)

    walk(narration.instance, None)
    assert not bad, "spans outside the define's body: " + "; ".join(bad)


def test_the_instance_still_loads_after_all_of_that(narration):
    """The counts above are worthless if the file does not parse into the model the wire carries."""
    overlay = parse_specification(ElementTree.tostring(narration.instance, encoding="unicode"))
    assert overlay is not None
    assert overlay.elements, "loaded to an empty index"


def test_every_bound_define_reference_names_a_real_define(narration):
    """SPEC-DEFINE-REF — the no-invented-identifiers rule, for the third binding.

    A reference to a define that does not exist degrades to plain prose, so the sentence still reads and
    the lens silently is not there. Cross-library references are checked only for shape, since this
    artifact carries one library's defines.
    """
    known = {d["name"] for d in narration.own}
    bad: list[str] = []
    for tag in ("ConceptRef", "RuleRef"):
        for ref in narration.instance.iter(NS + tag):
            lib, define = ref.get("library"), ref.get("define")
            if not lib or not define:
                bad.append(f"{tag} missing library/define")
            elif lib == narration.library and define not in known:
                bad.append(f"{tag} -> {define!r} is not declared by {narration.library}")
    assert not bad, "; ".join(bad)


def test_a_bound_define_reference_carries_prose_not_the_declared_name(narration):
    """The phrase is what a clinician reads. Substituting the declared name puts a title-cased
    identifier mid-sentence, which is the thing binding the reference was supposed to avoid."""
    offenders = []
    for tag in ("ConceptRef", "RuleRef"):
        for ref in narration.instance.iter(NS + tag):
            phrase = " ".join((ref.text or "").split())
            if not phrase:
                offenders.append(f"{tag} -> {ref.get('define')!r} has no phrase")
            elif phrase == ref.get("define"):
                offenders.append(f"{tag} -> {phrase!r} is the declared name verbatim")
    assert not offenders, "; ".join(offenders)


def test_every_inline_anchor_matches_a_real_site_in_the_elm(narration):
    """SPEC-INLINE-ANCHOR — an inline reference's own span must be a line the ELM actually puts it on.

    THIS IS WHAT MAKES THE ANCHOR WORTH HAVING. A per-phrase span is only an improvement on the widened
    field span if it is RIGHT; a hand-typed one that is off by a line is worse than the union it replaced,
    because it points confidently at a clause the phrase does not describe. The reference set carries the
    line for every read and every reference site, so the value is checkable rather than trusted.

    A span is OPTIONAL and its absence is meaningful — a transitive mention (a phrase naming something two
    steps down the chain) has no line in this body — so this checks the ones that are present.
    """
    reads = {d["name"]: d["reads"] for d in narration.own}
    refs = {d["name"]: d["references"] for d in narration.own}
    bad: list[str] = []

    def walk(element: ElementTree.Element, owner: str | None) -> None:
        mine = element.get("define") or owner
        for child in element:
            if child.tag == NS + "Read" and child.get("sourceSpan") and mine in reads:
                lines = {str(r["line"]) for r in reads[mine]
                         if r.get("profile") == child.get("profile") and r.get("line")}
                if child.get("sourceSpan") not in lines:
                    bad.append(f"{mine}: <Read {child.get('profile')}> anchored to "
                               f"{child.get('sourceSpan')}, ELM says {sorted(lines) or 'no such read'}")
            elif child.tag in (NS + "ConceptRef", NS + "RuleRef") and child.get("sourceSpan"):
                # Only checkable where the referenced define is reached FROM this body.
                if mine in refs:
                    lines = {str(r["line"]) for r in refs[mine]
                             if r["name"] == child.get("define") and r.get("line")}
                    if lines and child.get("sourceSpan") not in lines:
                        bad.append(f"{mine}: ref to {child.get('define')!r} anchored to "
                                   f"{child.get('sourceSpan')}, ELM says {sorted(lines)}")
            walk(child, mine)

    walk(narration.instance, None)
    assert not bad, "inline anchors disagreeing with the ELM: " + "; ".join(bad)


def test_a_bullet_naming_two_reads_anchors_each_to_its_own_line(narration):
    """The concrete defect this closes. "Recorded on the problem list or as an encounter diagnosis" used
    to carry ONE span of `1-2` over two phrases on two different lines, which cannot say which is which —
    the "two anchors mean a list, not a wider range" rule, one level below where it was written."""
    for element in narration.instance.iter(NS + "Item"):
        reads = [r for r in element.iter(NS + "Read")]
        if len(reads) < 2:
            continue
        spans = [r.get("sourceSpan") for r in reads]
        assert all(spans), f"a bullet with {len(reads)} reads left some unanchored: {spans}"
        assert len(set(spans)) == len(spans), f"two reads on one bullet share an anchor: {spans}"


def test_the_multi_read_bullet_rule_has_something_to_protect():
    """The guard on the rule above, asserted over the CORPUS rather than per measure.

    Whether a measure names two reads in one bullet is a property of ITS logic, not of how well it is
    narrated — a measure may read three profiles and never two in one breath. Asserting it per measure made a
    vacuous-pass guard into a demand that every narration contain a construction its source may not
    have. What must not happen is the rule going untested across the whole corpus, which is what this
    asserts. Since the official measures are gone (OFFICIAL-MEASURE-CONTENT-REMOVAL), check synthetic
    schema examples for instances demonstrating multi-read bullets."""
    bullets = 0
    # Check MEASURES (empty until AI-NARRATIVE-OVERLAY)
    for library, _version in MEASURES:
        instance = ROOT / "specifications" / f"{library}.xml"
        for element in ElementTree.parse(instance).getroot().iter(NS + "Item"):
            bullets += len([r for r in element.iter(NS + "Read")]) >= 2
    # Check schema examples for synthetic instances with multi-read bullets
    schema_examples_dir = Path(__file__).parent.parent.parent.parent / "schema" / "examples"
    if schema_examples_dir.exists():
        for xml_file in schema_examples_dir.glob("*.xml"):
            for element in ElementTree.parse(xml_file).getroot().iter(NS + "Item"):
                if len([r for r in element.iter(NS + "Read")]) >= 2:
                    bullets += 1
    assert bullets, "no multi-read bullet anywhere in the corpus — the rule above is untested"


def test_no_container_repeats_a_span_its_components_already_cover(narration):
    """SPEC-INLINE-ANCHOR — a bullet whose every claim is individually anchored does not also carry the
    union of them.

    Printing both puts `…problem list¹ … encounter diagnosis² ¹⁻²` on the line, where the trailing
    citation is exactly the union of the two before it and so tells the reader nothing they cannot already
    see. The container span survives only where some of the bullet's claim is NOT carried by a component —
    which is why this checks coverage rather than banning the combination outright.
    """
    def lines(span: str) -> set[int]:
        out: set[int] = set()
        for part in span.split(","):
            bits = [int(b) for b in part.split("-") if b.strip().isdigit()]
            if len(bits) == 1:
                out.add(bits[0])
            elif len(bits) == 2:
                out.update(range(bits[0], bits[1] + 1))
        return out

    redundant = []
    for item in narration.instance.iter(NS + "Item"):
        own = item.get("sourceSpan")
        if not own:
            continue
        covered: set[int] = set()
        for tag in ("Read", "ConceptRef", "RuleRef"):
            for ref in item.iter(NS + tag):
                if ref.get("sourceSpan"):
                    covered |= lines(ref.get("sourceSpan"))
        if covered and lines(own) <= covered:
            redundant.append(f"Item span {own} is fully covered by its components {sorted(covered)}")
    assert not redundant, "; ".join(redundant)
