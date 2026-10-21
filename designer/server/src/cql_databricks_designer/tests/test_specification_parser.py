"""APP-NARRATIVE-OVERLAY — the parser turns a Specification instance's inline content into wire segments.

The schema test next door proves an instance is VALID. These prove the parts a reader resolves survive
parsing: references stay bindings, unqualified ones degrade to their words, and the plain projection
reads as a sentence.
"""
from __future__ import annotations


def test_an_unqualified_define_reference_is_dropped_to_prose_not_guessed(tmp_path):
    """An unqualified reference would resolve to whichever library was indexed first — a link to the
    WRONG criterion, indistinguishable from a right one. It degrades to the words instead."""
    from cql_databricks_designer.specification.parser import _segments
    from xml.etree import ElementTree as ET
    NS = "https://cql-databricks.databricks.com/spec/1.0"
    el = ET.fromstring(
        f'<Narrative xmlns="{NS}">an <ConceptRef define="Only A Name">unqualified one</ConceptRef> here</Narrative>')
    segments = _segments(el)
    assert all(type(s).__name__ != "DefineReference" for s in segments)
    assert "".join(s.text for s in segments) == "an unqualified one here"


def test_a_parameter_reference_with_no_name_is_dropped():
    """It would render as a silent gap in a sentence built around it, so it is dropped loudly."""
    from xml.etree import ElementTree as ET

    from cql_databricks_designer.specification.parser import _plain, _segments
    NS = "https://cql-databricks.databricks.com/spec/1.0"
    el = ET.fromstring(f'<Guidance xmlns="{NS}">during the <ParameterRef/> period</Guidance>')
    segments = _segments(el)
    assert all(type(s).__name__ != "ParameterReference" for s in segments)
    # Asserted on the PROJECTION, which is what ships: the dropped element leaves the spaces either side
    # of it, and `_plain` is where interior whitespace is normalised for exactly this reason.
    assert _plain(segments) == "during the period"


# ── SPEC-TERMINOLOGY-REF — a value set or direct reference code named in prose ────────────────────


def _terminology_segments(xml: str):
    """Parse one narrated element's inline content. Kept local: these tests are about the SEGMENT."""
    from xml.etree import ElementTree as ET

    from cql_databricks_designer.specification.parser import _segments
    NS = "https://cql-databricks.databricks.com/spec/1.0"
    return _segments(ET.fromstring(f'<Narrative xmlns="{NS}">{xml}</Narrative>'))


def test_a_named_value_set_survives_as_a_binding_and_keeps_the_prose():
    """The last gap in `SPEC-EXPLAIN-ACCOUNTING`'s outward coverage. `text` is the PROSE and `name` is
    the DECLARATION — substituting the declared name would drop `Example Condition` into a
    clinical sentence in title case."""
    segments = _terminology_segments(
        'patients with an <TerminologyRef library="Example" name="Example Condition">'
        'example condition</TerminologyRef> in the period')
    refs = [s for s in segments if type(s).__name__ == "TerminologyReference"]
    assert len(refs) == 1
    assert refs[0].name == "Example Condition"
    assert refs[0].library == "Example"
    assert refs[0].text == "example condition"


def test_a_terminology_reference_never_carries_the_oid():
    """A narrator names the declaration; the renderer resolves the OID out of the ELM-derived Appendix
    (`SPEC-DERIVED-APPENDIX`). A typed OID is wrong the moment the binding is rebound, and there is
    nowhere on this type to put one."""
    from cql_databricks_designer.specification.model import TerminologyReference
    ref = TerminologyReference(text="example condition", library="Example", name="Example Condition")
    assert set(ref.to_dict()) == {"kind", "text", "library", "name"}
    assert "2.16.840" not in str(ref.to_dict())


def test_an_unqualified_terminology_reference_is_dropped_to_prose_not_guessed():
    """Value-set names repeat across an include closure, so resolving without the library lands the
    reader on whichever was indexed first. On terminology that is a claim about which patients match,
    not a navigation slip — so it degrades to the words instead."""
    from cql_databricks_designer.specification.parser import _plain
    segments = _terminology_segments(
        'an <TerminologyRef name="Example Condition">unqualified one</TerminologyRef> here')
    assert all(type(s).__name__ != "TerminologyReference" for s in segments)
    assert _plain(segments) == "an unqualified one here"


def test_an_empty_terminology_reference_falls_back_to_the_declared_name():
    """An empty element would render as a mark with nothing in it — worse than the identifier."""
    segments = _terminology_segments(
        'bound to <TerminologyRef library="Example" name="Example Condition"/> here')
    refs = [s for s in segments if type(s).__name__ == "TerminologyReference"]
    assert [r.text for r in refs] == ["Example Condition"]


def test_a_bound_terminology_phrase_projects_as_its_own_words():
    """Unlike a `MetricRef`, it HAS text — blanking it would turn "an example condition"
    into "an — condition" in every nav summary quoting the field."""
    from cql_databricks_designer.specification.parser import _plain
    segments = _terminology_segments(
        'an <TerminologyRef library="Example" name="Example Condition">example'
        '</TerminologyRef> condition')
    assert _plain(segments) == "an example condition"
