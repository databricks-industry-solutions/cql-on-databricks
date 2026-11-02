"""SPEC-XSD — the Specification standard is ENFORCED, not merely published.

`schema/specification.xsd` is the product's own standard for what a Specification IS. A schema nobody
validates against is documentation, so this is the gate: the schema must compile, every bundled
instance must validate against it, and the invariants the model depends on must actually be
enforceable rather than only described in prose.

The negative cases below are the point. Each one is a way a producer could emit a plausible-looking
Specification that the decided model forbids (the `SPEC-*` requirements) — a fabricated
metric, an unqualified source chip, prose with no provenance — and each must FAIL validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import xmlschema

import cql_databricks_designer

# The schema sits inside the package, so it is anchored on the package rather than on a parent count.
# `xmlschema` is a test dependency (the `dev` extra): imported, never skipped, because a gate that skips
# when its validator is missing is documentation.
SCHEMA_DIR = Path(cql_databricks_designer.__file__).parent / "schema"
NS = "https://cql-databricks.databricks.com/spec/1.0"


@pytest.fixture(scope="module")
def schema():
    """The compiled schema. Compilation failure IS a test failure — an XSD that no processor can
    build is not a standard."""
    return xmlschema.XMLSchema(str(SCHEMA_DIR / "specification.xsd"))


def _instances() -> list[Path]:
    return sorted((SCHEMA_DIR / "examples").glob("*.xml"))


def test_examples_directory_is_not_empty():
    """A green suite with no fixtures would prove nothing."""
    assert _instances(), "no Specification instances found under schema/examples/"


@pytest.mark.parametrize("path", _instances(), ids=lambda p: p.name)
def test_bundled_instance_validates(schema, path: Path):
    schema.validate(str(path))


def _minimal(body: str) -> str:
    """A schema-valid Specification skeleton with `body` spliced into the population criteria."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Specification xmlns="{NS}" schemaVersion="1.0">
  <Identity library="L" version="1" context="patients"/>
  <Provenance/>
  <PopulationCriteria>
    <Population kind="initial-population" define="Initial Population" library="L"
                stableId="s1" id="pop-IP">
      {body}
    </Population>
  </PopulationCriteria>
</Specification>"""


def test_minimal_specification_validates(schema):
    """The floor: identity + provenance + one population, no narration and no run."""
    assert schema.is_valid(_minimal(""))


def test_rule_requires_derived_polarity(schema):
    """SPEC-POLARITY is derived from the ELM, so it is REQUIRED — a rule that omits it is asking the
    renderer to guess whether it disqualifies."""
    rule = ('<Concept define="C" library="L" stableId="s2" id="con-C" provenance="derived">'
            '<Rule define="R" library="L" stableId="s3" id="rule-R" provenance="derived"/>'
            '</Concept>')
    assert not schema.is_valid(_minimal(rule))


def test_structure_cannot_claim_to_be_narrated(schema):
    """A Concept's structure is `derived` by construction. Marking it `narrated` would assert that a
    human wrote a grouping the ELM actually dictated."""
    concept = ('<Concept define="C" library="L" stableId="s2" id="con-C" provenance="narrated"/>')
    assert not schema.is_valid(_minimal(concept))


def test_prose_must_declare_its_provenance(schema):
    """The whole point of the split: a reader must always know whether prose is authored or generated."""
    narrative = "<Narrative>Patients with a recorded condition.</Narrative>"
    assert not schema.is_valid(_minimal(narrative))


def test_unreviewed_draft_is_serialisable(schema):
    """Fixes the v0.3 draft's B3. Generate → edit → Accept needs a working state to persist; if an
    unreviewed draft cannot be written down, the authoring cycle has nowhere to live."""
    narrative = ('<Narrative provenance="narrated" method="ai-drafted" reviewState="unreviewed">'
                 "Patients with a recorded condition.</Narrative>")
    assert schema.is_valid(_minimal(narrative))


def test_narrated_prose_carries_inline_references(schema):
    """Inline refs are structural, which is what replaces scanning prose for known define names."""
    narrative = ('<Narrative provenance="narrated">Excludes patients meeting '
                 '<ConceptRef library="Exclusions" define="Has End Of Life Care"/>.</Narrative>')
    assert schema.is_valid(_minimal(narrative))


def test_source_reference_must_be_library_qualified(schema):
    """Two libraries may declare one name, so an unqualified chip resolves to the wrong define."""
    sources = '<Sources><DefineRef define="Qualifying BP Reading"/></Sources>'
    assert not schema.is_valid(_minimal(sources))


def test_metric_must_state_whether_it_was_computed(schema):
    """SPEC-METRIC — `computed` is required so 'no run yet' can never be mistaken for a real zero."""
    metric = '<Metric form="count" subjectUnit="patients" provenance="derived"/>'
    assert not schema.is_valid(_minimal(metric))


def test_uncomputed_metric_carries_no_value(schema):
    """The not-computed STATE is representable without inventing a figure to sit beside it."""
    metric = '<Metric form="count" subjectUnit="patients" computed="false" provenance="derived"/>'
    assert schema.is_valid(_minimal(metric))


def test_metric_subject_unit_follows_context_not_prose(schema):
    """F9 — an episode-of-care measure counts episodes. 'members' is not a unit this model admits."""
    metric = ('<Metric form="count" subjectUnit="members" value="10" computed="true"'
              ' provenance="derived"/>')
    assert not schema.is_valid(_minimal(metric))


def test_metric_carries_both_grains(schema):
    """SPEC-METRIC — a resource-valued define counts patients AND the resources themselves; the two
    answer different questions, so the document does not have to choose between them."""
    metric = ('<Metric form="cardinality-histogram" subjectUnit="patients"'
              ' resultType="List&lt;FHIR.Encounter&gt;" value="149882"'
              ' elementGrain="Encounter" elementValue="612390" computed="true" provenance="derived"/>')
    assert schema.is_valid(_minimal(metric))


def test_boolean_metric_needs_no_element_grain(schema):
    """A Boolean says something about the subject and nothing else — there is no second thing to
    count, and the schema must not force one to be invented."""
    metric = ('<Metric form="count" subjectUnit="patients" resultType="Boolean" value="112441"'
              ' computed="true" provenance="derived"/>')
    assert schema.is_valid(_minimal(metric))


def test_semantics_flags_co_occur(schema):
    """Fixes B1 — the four categories are independent, and a rule may trip several at once."""
    rule = ('<Concept define="C" library="L" stableId="s2" id="con-C" provenance="derived">'
            '<Rule define="R" library="L" stableId="s3" id="rule-R" polarity="computational"'
            ' provenance="derived">'
            '<SemanticsFlag category="aggregation">Aggregates before filtering.</SemanticsFlag>'
            '<SemanticsFlag category="null-propagation">Nulls are excluded, not false.</SemanticsFlag>'
            "</Rule></Concept>")
    assert schema.is_valid(_minimal(rule))


def test_population_carries_a_stable_id(schema):
    """Fixes B2 — run-scoped measurements join on stableId, and populations carry the counts."""
    no_stable_id = f"""<?xml version="1.0" encoding="UTF-8"?>
<Specification xmlns="{NS}" schemaVersion="1.0">
  <Identity library="L" version="1" context="patients"/>
  <Provenance/>
  <PopulationCriteria>
    <Population kind="numerator" define="Numerator" library="L" id="pop-N"/>
  </PopulationCriteria>
</Specification>"""
    assert not schema.is_valid(no_stable_id)


def test_episode_and_stratifier_populations_are_expressible(schema):
    """F8/F9 — published eCQMs are the demo corpus and several use stratifiers and SDEs; a model that
    cannot express them cannot describe the content we ship."""
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<Specification xmlns="{NS}" schemaVersion="1.0">
  <Identity library="L" version="1" context="episodes" scoring="ratio"/>
  <Provenance/>
  <PopulationCriteria>
    <Population kind="measure-observation" define="MO" library="L" stableId="s1" id="pop-MO"
                group="g1"/>
    <Population kind="stratifier" define="Strat" library="L" stableId="s2" id="pop-S"/>
    <Population kind="supplemental-data-element" define="Payer Stratifier" library="L" stableId="s3"
                id="pop-SDE"/>
  </PopulationCriteria>
</Specification>"""
    assert schema.is_valid(doc)


def test_definitions_entry_declares_elimination(schema):
    """SPEC-DEFINITIONS — Definitions is the only home for functions and eliminated defines, so an
    entry must say which it is rather than leaving the reader to infer it."""
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<Specification xmlns="{NS}" schemaVersion="1.0">
  <Identity library="L" version="1" context="patients"/>
  <Provenance/>
  <Definitions>
    <Library name="L" root="true" resolved="true">
      <Entry name="Payer Stratifier" kind="define" stableId="s9" id="def-SDEPayer"/>
    </Library>
  </Definitions>
</Specification>"""
    assert not schema.is_valid(doc)
