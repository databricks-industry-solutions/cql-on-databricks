"""SPEC-TERMINOLOGY-EXTRACT — the curated packages, the SQL they build, and the drift check.

The drift check is the reason the packages are allowed to be curated at all: it reports coding sites the
emit writes that no package names, so "we chose not to show this" stays distinguishable from "nobody
noticed it appeared".
"""
from __future__ import annotations

import re

import pytest

from cql_databricks.result.insights.extractors import (
    DEFAULT_MODEL, EXTRACTORS, KIND_CODING, KIND_SCALAR, Element, elements_for,
)
from cql_databricks.result.insights.terminology import extraction_sql, resource_types_sql


def paths_for(model: str, resource_type: str):
    """The coding elements of a (model, type) — what this module used to own outright."""
    return elements_for(model, resource_type, kind=KIND_CODING)


def test_a_model_without_a_package_yields_nothing_rather_than_a_guess():
    # A model this module has no package for must not fall through to FHIR's paths: QDM's coded fields
    # are neither named nor shaped like FHIR's, so a guess would build SQL against fields that do not
    # exist. Empty reads on the page as "nothing recorded", which is true.
    assert paths_for("QDM", "Encounter") == ()
    assert paths_for(DEFAULT_MODEL, "NoSuchResource") == ()


def test_the_package_is_keyed_by_model_and_type_together():
    # Keying on the type alone would make `Encounter` mean whichever model loaded first — the collision
    # `SPEC-ANCHOR` already records for two libraries declaring one define name.
    assert paths_for("FHIR", "Condition"), "FHIR/Condition is the worked example; it must be packaged"
    assert set(EXTRACTORS) == {"FHIR"}, "a new model needs its own extractor, not an alias to FHIR's"


def test_repeating_and_singular_codeable_concepts_get_different_accessors():
    # THE BUG THIS CATCHES: a repeating CodeableConcept is an ARRAY of them, so `x.type.coding` is
    # `array<array<Coding>>` and explodes to arrays rather than codings. The shape is not inferable from
    # the field name, which is why the package states it.
    condition = {p.path: p for p in paths_for("FHIR", "Condition")}
    assert condition["code"].accessor == "{r}.code.coding"
    assert condition["category"].accessor == "flatten({r}.category.coding)"


def test_no_package_reads_administrative_or_identifier_shaped_fields():
    # Measured on the emitted Condition struct: eleven coding sites, most administrative. `meta.tag`
    # and `meta.security` carry codes and are never what "what is this matching on" means. `Quantity`
    # carries system AND code (a UCUM unit) and `Identifier` carries system — both are why discovery
    # was rejected in favour of curation.
    banned = ("meta", "identifier", "assigner", "extension", "value_quantity", "onset", "abatement")
    for model, types in EXTRACTORS.items():
        for type_name, paths in types.items():
            for p in paths:
                low = p.path.lower()
                assert not any(b in low for b in banned), f"{model}/{type_name}: {p.path} is not clinical"


def test_every_curated_path_carries_a_human_label():
    # The section heading is what a clinician reads (`APP-AUDIENCE`); a field path is not a label.
    for types in EXTRACTORS.values():
        for paths in types.values():
            for p in paths:
                assert p.label and p.label != p.path
                assert p.label[0].isupper(), f"{p.label} should read as a heading"


class TestExtractionSql:
    REL = "cat.sch.example_condition_diagnosis"

    def sql(self) -> str:
        return extraction_sql(self.REL, "Condition", paths_for("FHIR", "Condition"))

    def test_one_branch_per_path_unioned_never_nested(self):
        # TWO LATERAL VIEWS OVER ONE ROW CROSS-PRODUCT. A Condition with 3 diagnosis codings and 2
        # categories would report 6 of each, and the error scales with the record instead of announcing
        # itself. One explode per branch is the only safe shape.
        sql = self.sql()
        paths = paths_for("FHIR", "Condition")
        assert sql.count("LATERAL VIEW explode(") == len(paths)
        assert sql.count(" UNION ALL ") == len(paths) - 1
        for branch in sql.split(" UNION ALL "):
            assert branch.count("LATERAL VIEW") == 1

    def test_counts_are_fan_out_immune(self):
        # `COUNT(*)` on these relations reports rows in the billions for thousands of subjects
        # (`relation.py`), so the SUBJECT figure is always DISTINCT.
        sql = self.sql()
        assert "COUNT(DISTINCT __subject_id) AS subjects" in sql

    def test_every_branch_projects_the_same_shape(self):
        # The union is only well-formed if every branch agrees; `flow.py` follows the same discipline.
        selects = [b[b.index("SELECT"):b.index(" FROM ")] for b in self.sql().split(" UNION ALL ")]
        shapes = {tuple(re.findall(r"AS (\w+)", s)) for s in selects}
        assert len(shapes) == 1, f"branches disagree on shape: {shapes}"

    def test_it_filters_to_the_resource_type_it_was_built_for(self):
        # A relation may hold more than one type; a package belongs to exactly one.
        assert self.sql().count("resourceType = 'Condition'") == len(paths_for("FHIR", "Condition"))

    def test_null_codes_are_excluded_rather_than_counted_as_a_code(self):
        assert "c.code IS NOT NULL" in self.sql()

    def test_ranking_happens_in_sql_not_in_the_client(self):
        # The tail is the point — 170 declared codes firing on one — and a client-side sort over a
        # truncated list ranks a window rather than the data.
        assert "ORDER BY path, subjects DESC" in self.sql()

    def test_no_paths_means_no_statement(self):
        assert extraction_sql(self.REL, "Whatever", ()) == ""

    def test_identifiers_and_literals_are_quoted_through_the_shared_helpers(self):
        # String building around user-supplied names is how an injection lands; `relation.literal`
        # is the one escaper on every metric path.
        sql = extraction_sql(self.REL, "O'Brien", (Element("code", "Code", KIND_CODING, "{r}.code.coding"),))
        assert "'O''Brien'" in sql

    def test_resource_types_are_read_from_the_data(self):
        # Not assumed from the define's reads: a define may return something other than what it
        # retrieved, and the package is selected per type.
        sql = resource_types_sql(self.REL)
        assert "DISTINCT" in sql and "resourceType" in sql


# ── the drift check ───────────────────────────────────────────────────────────────────────────────
#
# CURATION IS ONLY SAFE IF IT CANNOT GO QUIETLY STALE. This walks a real emitted struct schema and
# reports coding sites no package names. It is a REPORT, not an assertion that every site is curated —
# most sites are administrative and deliberately absent — so it fails only when the fixture itself is
# missing, and prints what it found for a human to decide on.

FIXTURE = str(__import__("pathlib").Path(__file__).parent / "__fixtures__" / "condition_struct.txt")


def coding_sites(struct: str) -> set[str]:
    """Field names carrying `coding: array<struct<...>>` — the CodeableConcept signature.

    Deliberately NOT "any struct with a `system` field": that also matches `Identifier`,
    `Reference.assigner` and `Quantity`, and `Quantity` carries `system` AND `code` (a UCUM unit). The
    false-positive rate of the loose test is the measured reason these packages are curated.
    """
    return set(re.findall(r"(\w+):(?:array<)?struct<coding:array<struct<", struct))


@pytest.mark.skipif(not __import__("os").path.exists(FIXTURE), reason="schema fixture not captured")
def test_report_coding_sites_absent_from_the_package():
    struct = open(FIXTURE).read()
    found = coding_sites(struct)
    curated = {p.path for p in paths_for("FHIR", "Condition")}
    uncurated = sorted(found - curated)
    print(f"\nCondition coding sites in the emit: {len(found)}; curated: {len(curated)}")
    print(f"present but not curated (review, do not auto-add): {uncurated}")
    assert found & curated, "the package names nothing the emit actually writes — it has gone stale"


# ── DATA-INSIGHT-EXTRACTION: the Patient extractor ────────────────────────────────────────────────
#
# The same catalog feeds a second surface. These pin the properties that make it reusable rather than a
# terminology thing with a demographics branch bolted on.

class TestPatientExtractor:
    def elements(self):
        return elements_for("FHIR", "Patient", kind=KIND_SCALAR)

    def test_demographics_and_terminology_share_one_catalog(self):
        # If these came from different places the two surfaces would drift, which is the whole reason
        # the mechanism has a name.
        assert self.elements(), "the Patient extractor is what Demographics reads"
        assert elements_for("FHIR", "Condition", kind=KIND_CODING), "Terminology reads the same catalog"

    def test_kind_separates_the_two_extractions(self):
        # A scalar cut is grouped directly; a coding is exploded first. Asking for one kind must never
        # return the other, or the SQL builder receives an accessor of the wrong shape.
        assert all(e.kind == KIND_SCALAR for e in self.elements())
        assert not elements_for("FHIR", "Patient", kind=KIND_CODING)

    def test_every_demographic_carries_a_stable_id(self):
        # The id keys the Stratum, which the client renders and a reader's layout preference may key
        # off; a label is prose and may be reworded.
        ids = [e.id for e in self.elements()]
        assert all(ids), "a demographic cut without an id cannot be a stratum"
        assert len(set(ids)) == len(ids), f"duplicate cut ids: {ids}"

    def test_age_is_not_curated_because_its_bands_belong_to_the_evaluation(self):
        # Age depends on the measurement period, so holding it here would put an evaluation's parameter
        # in a catalog keyed by data model. `profile.py` composes it instead.
        assert "age" not in {e.id for e in self.elements()}

    def test_race_and_ethnicity_are_absent_because_the_emit_cannot_carry_them(self):
        # US Core nests them (`extension[url=…].extension[url=ombCategory].valueCoding`) and the emitted
        # extension struct is FLAT — measured, and the accessor failed with `No such struct field
        # 'extension'`. They stay fabricated in `sample.py` and stay marked, rather than being read by
        # an accessor that cannot resolve.
        assert not {"race", "ethnicity"} & {e.id for e in self.elements()}

    def test_accessors_are_written_against_the_record_placeholder(self):
        # `{r}` is substituted per caller — `p.__resource` for the Patient binding, `__val` for a
        # result relation — so one catalog serves relations that name their record column differently.
        for e in self.elements():
            assert "{r}" in e.accessor, f"{e.id} cannot be retargeted"
