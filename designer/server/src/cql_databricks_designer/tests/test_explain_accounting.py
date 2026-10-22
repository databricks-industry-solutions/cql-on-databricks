"""SPEC-EXPLAIN-ACCOUNTING, SPEC-EXPLAIN-INPUTS, APP-NARRATIVE-OVERLAY — the run-time checks a narration passes.

Coverage is a NUMBER, so it is checked, and every failure mode it guards against is SILENT in the product: an
unresolvable `<Read>` renders as ordinary prose, a span past a body highlights nothing, an unnarrated define is
simply not there to notice. A generated narration can therefore lose exactly the parts that make it verifiable
and still look finished. `explain/checks.py` is what stops it being shown; these are its tests.

THE FIXTURE IS SYNTHETIC: a two-library hypertension sample with a reference set written by hand in the
shape `explain/reference_set.py` derives, and a narration that passes every check. Each test breaks one thing
and asserts the check that names it.
"""
from __future__ import annotations

import copy
from xml.etree import ElementTree

import pytest

from cql_databricks_designer.explain.checks import NS, check
from cql_databricks_designer.specification import parse_specification

PROBLEM_LIST = "qicore-condition-problems-health-concerns"
ENCOUNTER_DIAGNOSIS = "qicore-condition-encounter-diagnosis"

REFERENCE_SET = {
    "library": "SampleBP",
    "version": "1.0.0",
    "translatorOptions": "EnableAnnotations,EnableLocators,EnableResultTypes",
    "includes": [{"localIdentifier": "Common", "path": "SampleCommon", "version": "1.0.0"}],
    "declared": {"valueSets": [{"name": "Essential Hypertension", "id": "urn:oid:2.16.840.1.000000.1"}],
                 "codes": [], "parameters": [{"name": "Measurement Period", "type": "Interval<DateTime>"}]},
    "defines": [
        {"name": "Initial Population", "library": "SampleBP", "returns": "Boolean", "locator": "10:1-12:40",
         "bodyLines": 2, "body": 'exists "Hypertension Diagnoses"\n  and exists Common."Qualifying Encounters"',
         "metric": {"key": "SampleBP::Initial Population", "grain": "subject"},
         "references": [{"name": "Hypertension Diagnoses", "line": 1},
                        {"name": "Qualifying Encounters", "library": "Common", "line": 2}],
         "reads": [], "valueSets": [], "codes": [], "parameters": []},
        {"name": "Hypertension Diagnoses", "library": "SampleBP", "returns": "List<Condition>",
         "locator": "14:1-16:60", "bodyLines": 2,
         "body": '[ConditionProblemsHealthConcerns: "Essential Hypertension"]\n'
                 '  union [ConditionEncounterDiagnosis: "Essential Hypertension"]',
         "metric": {"key": "SampleBP::Hypertension Diagnoses", "grain": "element"},
         "references": [],
         "reads": [{"resourceType": "Condition", "profile": PROBLEM_LIST, "valueSet": "Essential Hypertension",
                    "line": 1},
                   {"resourceType": "Condition", "profile": ENCOUNTER_DIAGNOSIS,
                    "valueSet": "Essential Hypertension", "line": 2}],
         "valueSets": [{"name": "Essential Hypertension", "library": "SampleBP", "line": 1}],
         "codes": [], "parameters": []},
        {"name": "Measurement Interval", "library": "SampleBP", "returns": "Interval<DateTime>",
         "locator": "18:1-19:30", "bodyLines": 1, "body": '"Measurement Period"', "metric": None,
         "references": [], "reads": [], "valueSets": [], "codes": [], "parameters": ["Measurement Period"]},
    ],
}

PROSE = 'provenance="narrated" method="ai-drafted" reviewState="unreviewed"'

VALID = f"""<Specification xmlns="{NS}" schemaVersion="1.0">
  <Identity library="SampleBP" version="1.0.0" context="patients" scoring="proportion"/>
  <Provenance/>
  <Overview>
    <HowToRead {PROSE} sourceTag="howToRead">Each criterion says who it counts, then where it is strict.</HowToRead>
  </Overview>
  <PopulationCriteria>
    <Population kind="initial-population" define="Initial Population" library="SampleBP"
                stableId="pop-ip" id="pop-IP">
      <Narrative {PROSE} sourceTag="description" sourceSpan="1-2">Adults with
        <ConceptRef library="SampleBP" define="Hypertension Diagnoses" sourceSpan="1">diagnosed hypertension</ConceptRef>
        who had a
        <ConceptRef library="SampleCommon" define="Qualifying Encounters">qualifying outpatient visit</ConceptRef>;
        <MetricRef library="SampleBP" define="Initial Population"/> patients qualify.</Narrative>
    </Population>
  </PopulationCriteria>
  <Common>
    <Rule define="Hypertension Diagnoses" library="SampleBP" stableId="rule-htn" id="rule-HTN"
          polarity="qualifying" provenance="derived">
      <Text {PROSE} sourceTag="description" sourceSpan="1-2">A diagnosis of essential hypertension, recorded
        <List kind="include">
          <Item><Read profile="{PROBLEM_LIST}" sourceSpan="1">on the problem list</Read>
            or <Read profile="{ENCOUNTER_DIAGNOSIS}" sourceSpan="2">as an encounter diagnosis</Read></Item>
        </List>
        <MetricRef library="SampleBP" define="Hypertension Diagnoses" grain="element"/> diagnoses in all.</Text>
    </Rule>
    <Rule define="Measurement Interval" library="SampleBP" stableId="rule-mi" id="rule-MI"
          polarity="computational" provenance="derived">
      <Text {PROSE} sourceTag="description" sourceSpan="1">The measurement period, start to end inclusive.</Text>
    </Rule>
  </Common>
</Specification>"""


def _messages(text: str) -> list[str]:
    return [p.message for p in check(text, REFERENCE_SET)]


def _edit(fn) -> str:
    """The valid instance with one change made by ``fn(root)``."""
    ElementTree.register_namespace("", NS)
    root = ElementTree.fromstring(VALID)
    fn(root)
    return ElementTree.tostring(root, encoding="unicode")


def _find(root: ElementTree.Element, path: str) -> ElementTree.Element:
    found = root.find(path, {"s": NS})
    assert found is not None, path
    return found


def test_the_fixture_passes_every_check():
    """The floor: a narration that closes every direction has no problems at all."""
    assert check(VALID, REFERENCE_SET) == []


def test_the_fixture_loads_into_the_wire_model():
    overlay = parse_specification(VALID)
    assert set(overlay.elements) >= {"SampleBP::Initial Population", "SampleBP::Hypertension Diagnoses"}


def test_malformed_xml_is_one_problem():
    assert _messages("```xml\n" + VALID + "\n```")[0].startswith("not well-formed XML")


def test_a_schema_violation_is_reported():
    text = _edit(lambda r: _find(r, ".//s:Rule").attrib.pop("polarity"))
    assert any(m.startswith("schema:") for m in _messages(text))


def test_the_identity_must_be_the_library_asked_for():
    text = _edit(lambda r: _find(r, "s:Identity").set("version", "2.0.0"))
    assert any("Identity is SampleBP@2.0.0" in m for m in _messages(text))


@pytest.mark.parametrize("attribute, value", [("provenance", "authored"), ("method", "human-written")])
def test_generated_prose_cannot_claim_another_author(attribute, value):
    text = _edit(lambda r: _find(r, ".//s:Narrative").set(attribute, value))
    assert any("provenance='narrated' method='ai-drafted'" in m for m in _messages(text))


def test_generated_prose_names_the_tag_it_drafts():
    text = _edit(lambda r: _find(r, ".//s:Narrative").attrib.pop("sourceTag"))
    assert any("has no sourceTag" in m for m in _messages(text))


def test_a_derived_element_is_never_carried():
    """SPEC-DERIVED-APPENDIX — an Appendix restates OIDs that go stale with nothing to catch them."""
    def add(root):
        ElementTree.SubElement(root, f"{{{NS}}}Appendix")
    assert any("<Appendix>" in m for m in _messages(_edit(add)))


def test_an_invented_profile_is_an_identifier_problem():
    text = _edit(lambda r: _find(r, ".//s:Read").set("profile", "us-core-condition"))
    problems = check(text, REFERENCE_SET)
    assert any(p.kind == "identifier" and "us-core-condition" in p.message for p in problems)


def test_a_reference_to_an_undeclared_define_is_an_identifier_problem():
    text = _edit(lambda r: _find(r, ".//s:ConceptRef").set("define", "Hypertension Dx"))
    assert any("'Hypertension Dx'> is not declared by SampleBP" in m for m in _messages(text))


def test_a_reference_to_a_library_not_included_is_an_identifier_problem():
    refs = lambda r: r.findall(".//s:ConceptRef", {"s": NS})  # noqa: E731
    text = _edit(lambda r: refs(r)[1].set("library", "Elsewhere"))
    assert any("library='Elsewhere'" in m for m in _messages(text))


def test_a_reference_carries_prose_not_the_declared_name():
    def rename(root):
        _find(root, ".//s:ConceptRef").text = "Hypertension Diagnoses"
    assert any("repeats the declared name" in m for m in _messages(_edit(rename)))


def test_every_define_is_narrated():
    """Direction 1."""
    def drop(root):
        common = _find(root, "s:Common")
        common.remove(common.findall("s:Rule", {"s": NS})[1])
    assert any("defines with no narration: ['Measurement Interval']" in m for m in _messages(_edit(drop)))


def test_every_measurable_element_cites_its_figure():
    """Direction 4."""
    def drop(root):
        narrative = _find(root, ".//s:Narrative")
        narrative.remove(_find(narrative, "s:MetricRef"))
    assert any("cite none: ['Initial Population']" in m for m in _messages(_edit(drop)))


def test_no_figure_is_cited_for_an_element_that_has_none():
    def add(root):
        text = _find(root, ".//s:Rule[@define='Measurement Interval']/s:Text")
        ElementTree.SubElement(text, f"{{{NS}}}MetricRef", library="SampleBP", define="Measurement Interval")
    assert any("cited for elements that have none: ['Measurement Interval']" in m for m in _messages(_edit(add)))


def test_every_profile_the_logic_reads_is_bound():
    """Direction 3."""
    def unbind(root):
        item = _find(root, ".//s:Item")
        item.remove(item.findall("s:Read", {"s": NS})[1])
    assert any(f"no phrase binds: ['{ENCOUNTER_DIAGNOSIS}']" in m for m in _messages(_edit(unbind)))


def test_every_criterion_field_carries_a_span():
    """Direction 2, forward."""
    text = _edit(lambda r: _find(r, ".//s:Rule[@define='Measurement Interval']/s:Text").attrib.pop("sourceSpan"))
    assert any("<Text> on Measurement Interval has no sourceSpan" in m for m in _messages(text))


def test_no_overview_field_carries_a_span():
    text = _edit(lambda r: _find(r, ".//s:HowToRead").set("sourceSpan", "1"))
    assert any("Overview <HowToRead> carries sourceSpan" in m for m in _messages(text))


def test_a_span_stays_inside_its_own_body():
    """Owned by the nearest declaring element, never by a reference's target."""
    text = _edit(lambda r: _find(r, ".//s:Rule[@define='Measurement Interval']/s:Text").set("sourceSpan", "1-3"))
    assert any("Measurement Interval: span 1-3 exceeds its 1-line body" in m for m in _messages(text))


def test_an_inline_read_is_anchored_where_the_elm_puts_it():
    """SPEC-INLINE-ANCHOR."""
    text = _edit(lambda r: _find(r, ".//s:Read").set("sourceSpan", "2"))
    messages = _messages(text)
    assert any(f"<Read {PROBLEM_LIST}> anchored to 2, the ELM says ['1']" in m for m in messages)


def test_two_reads_on_one_bullet_have_two_anchors():
    text = _edit(lambda r: r.findall(".//s:Read", {"s": NS})[1].set("sourceSpan", "1"))
    assert any("share an anchor" in m for m in _messages(text))


def test_a_bullet_repeats_no_span_its_components_cover():
    text = _edit(lambda r: _find(r, ".//s:Item").set("sourceSpan", "1-2"))
    assert any("fully covered by its components" in m for m in _messages(text))


def test_the_check_judges_only_the_narrated_librarys_own_defines():
    """A define another library declares is that library's narration's to account for."""
    other = copy.deepcopy(REFERENCE_SET)
    other["defines"].append({**other["defines"][2], "name": "Elsewhere", "library": "SampleCommon"})
    assert check(VALID, other) == []
