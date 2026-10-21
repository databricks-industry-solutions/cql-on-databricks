"""APP-DATA-PROFILE / APP-NO-UI-MOCK — one define's profile, derived where it can be.

The thing worth pinning here is not the arithmetic, it is the PROVENANCE. This payload is the first in
the product that is part real and part invented, and the whole argument for allowing that is that a
reader can tell which is which without being told. So: what is derived says so, what is fabricated says
so, and the two claims are made per block (and per stratum, where a block is mixed within itself).

Fixtures use mock synthetic measure data (ExampleMeasure): a boolean population relation whose
value column is named after the define, and a list-valued relation whose `__ordinal` carries no values.
"""
from __future__ import annotations

from unittest.mock import patch

from cql_databricks.result.insights.extractors import (
    DEFAULT_MODEL, KIND_SCALAR, elements_for,
)

import pytest

from cql_databricks.result.insights.evaluation import MetricsUnavailable, StateClosure
from cql_databricks.result.insights.model import PROVENANCE_MIXED, PROVENANCE_RUN
from cql_databricks.result.insights.profile import define_profile
from cql_databricks.result.address import evaluation_version


def _warehouse(warehouse_id, sql):
    """The warehouse these tests stand in for: the derivations read through an injected ``execute``
    (DERIVATIONS-TO-LIBRARY), and each test patches this hook where it patched the service's executor."""
    raise AssertionError("a test must patch _warehouse")


def _execute(warehouse_id="wh-1"):
    return lambda sql: _warehouse(warehouse_id, sql)


CATALOG = "acme_results"
DATA_SOURCE = "conformance"
PERIOD = ("2026-01-01", "2026-12-31")
SUFFIX = evaluation_version(DATA_SOURCE, period_start=PERIOD[0], period_end=PERIOD[1])
PATIENT_FN = "acme.conformance.retrieve_Patient"


class _Row:
    def __init__(self, id_: str, name: str, version: str) -> None:
        self.id, self.library_name, self.library_version = id_, name, version


class _Define:
    def __init__(self, define_name: str, mv_name: str) -> None:
        self.define_name, self.mv_name = define_name, mv_name


#: The suffix of the same data source with no period stated — a different evaluation, and one the
#: no-period test still has to find a compiled library for.
NO_PERIOD_SUFFIX = evaluation_version(DATA_SOURCE, period_start=None, period_end=PERIOD[1])


class _State:
    def list_libraries(self, data_source_id: str):
        if data_source_id != DATA_SOURCE:
            return []
        return [_Row("lib-measure", "ExampleMeasure", f"1.0.0_{SUFFIX}"),
                _Row("lib-noperiod", "ExampleMeasure", f"1.0.0_{NO_PERIOD_SUFFIX}")]

    def list_defines(self, library_id: str):
        return [_Define("Initial_Population", "initial_population"),
                _Define("Example_Results", "example_results")]


#: The boolean population relation. Its value column is named after the DEFINE, which is why the
#: value column is found by elimination rather than by looking for `__val`.
BOOLEAN = {"__subject_id": "STRING", "Initial_Population": "BOOLEAN"}
#: A list-valued relation. `__ordinal` is present and carries no values (measured), so the element
#: grain is absent rather than zero.
LIST_VALUED = {"__subject_id": "STRING", "__ordinal": "STRING", "__val": "STRUCT"}


def _run(define="Initial Population", columns=BOOLEAN,
         counts=(67, None), patient_function=PATIENT_FN, rows=None, **overrides):
    """Drive the builder with every warehouse read stubbed, capturing the SQL it issued."""
    issued: list[str] = []

    # Keyed off whatever relation the builder RESOLVES to rather than a hard-coded schema, so a test
    # that changes the evaluation (the no-period case) still describes the relation it asks about.
    def describe(_catalog, refs, **_kw):
        return {r: columns for r in refs}

    def count(_warehouse_id, cols, *, eval_id):
        assert eval_id in (SUFFIX, NO_PERIOD_SUFFIX)  # RUN-RESULTS-BY-EVAL: one evaluation set per count
        return {r: counts for r in cols}

    def execute(_warehouse_id, sql):
        issued.append(sql)
        if rows is not None:
            return rows(sql)
        if "IS NULL THEN" in sql:          # the null-rate probe
            return [["0", "67"]]
        if "GROUP BY 1 ORDER BY n DESC, v" in sql:   # top values
            return [["true", "67"], ["false", "1"]]
        return [["female", "65"], ["male", "2"]]     # a stratum cut

    kwargs = dict(
        library="ExampleMeasure", version="1.0.0",
        define=define, define_library="ExampleMeasure",
        data_source_id=DATA_SOURCE, period_start=PERIOD[0], period_end=PERIOD[1], eval_id=SUFFIX, draft="",
        closure=StateClosure(_State()), patient_function=patient_function,
        results_catalog=CATALOG, execute=_execute(),
    )
    kwargs.update(overrides)
    with patch("cql_databricks.result.insights.profile.relation_columns", side_effect=describe), \
         patch("cql_databricks.result.insights.profile.count_relations", side_effect=count), \
         patch(f"{__name__}._warehouse", side_effect=execute):
        return define_profile(**kwargs), issued


# ── what is derived ───────────────────────────────────────────────────────────────────────────────

def test_the_value_profile_is_read_not_invented():
    profile, _ = _run()
    assert profile.values.subjects == 67
    assert profile.values.null_rate == 0.0


def test_the_two_grains_come_from_the_same_counts_the_metric_set_uses():
    """APP-SPEC-MODEL — the pane and the document must not print different numbers for one define,
    so both read `count_relations` rather than each counting the relation their own way."""
    profile, _ = _run(counts=(17, 41))
    assert (profile.values.subjects, profile.values.elements) == (17, 41)


def test_top_values_group_by_the_value_column_found_by_elimination():
    """The value column of a boolean relation is named after the DEFINE, so a reader looking for
    `__val` finds nothing. `relation.value_column` eliminates the keys the emit owns instead."""
    profile, issued = _run()
    assert [(s.value, s.count) for s in profile.sample] == [("true", 67), ("false", 1)]
    assert any("`Initial_Population`" in sql and "GROUP BY 1" in sql for sql in issued)


def test_a_struct_valued_define_renders_its_values_as_json():
    """A scalar define returns a CQL Quantity and a list-valued one a resource, so there is no single
    field to project — picking one per FHIR type is the client-side knowledge APP-DECOUPLE keeps
    out of this layer."""
    _, issued = _run(define="Example Results",
                     columns=LIST_VALUED,
                     counts=(33, None))
    assert any("to_json(`__val`)" in sql for sql in issued)


def test_strata_are_cut_over_the_subjects_the_define_is_TRUE_of():
    """On a measured deck 96% of a boolean relation's rows are false, so cutting every subject the
    relation TOUCHES would describe the population the pipeline scanned, not the one it selected."""
    _, issued = _run()
    cut = next(sql for sql in issued if "GROUP BY 1 ORDER BY n DESC" in sql and "__resource" in sql)
    assert "CASE WHEN `Initial_Population` THEN __subject_id END" in cut


def test_the_patient_binding_is_called_with_no_argument():
    """BIND-RETRIEVE-UDF, EMIT-ROOT-INDEPENDENT — the same call the evaluated total makes: the data source is the
    population, so no argument names a measure."""
    _, issued = _run()
    assert any("retrieve_Patient() p" in sql for sql in issued)
    assert not any("library" in sql for sql in issued)


def test_age_is_measured_at_the_start_of_the_measurement_period():
    """What the label says, and what an eCQM means by an age criterion."""
    _, issued = _run()
    assert any("months_between('2026-01-01'" in sql for sql in issued)


def test_no_period_means_no_age_band_rather_than_an_age_measured_from_today():
    """A band measured from the wall clock would drift between two reads of the same evaluation."""
    profile, _ = _run(period_start=None)
    assert "age" not in {s.id for s in profile.strata}
    assert "sex" in {s.id for s in profile.strata}


def test_no_patient_binding_drops_the_cuts_and_keeps_the_rest():
    """Unlike the evaluated total — which is a BASE and must be stated or the whole set fails — a
    missing demographic cut is an absence the rest of the profile survives."""
    profile, _ = _run(patient_function=None)
    assert profile.values.subjects == 67
    assert not [s for s in profile.strata if not s.sampled]


# ── what is NOT derived, and says so ──────────────────────────────────────────────────────────────

def test_cardinality_is_omitted_rather_than_sampled():
    """It sits inside `values`, a block this builder marks as REAL, and an invented histogram inside a
    real block is the one thing a per-block mark cannot express. `__ordinal` carries no values yet."""
    profile, _ = _run(counts=(33, None))
    assert profile.values.cardinality == []


def test_nothing_on_this_payload_is_fabricated_any_more():
    """SUPERSEDES three tests that pinned the opposite, and the reversal is on evidence rather than
    tidiness. `value_sets` is superseded by `SPEC-TERMINOLOGY-EXTRACT`, which reads real codes off the
    define's own results; `temporal` is rendered by nothing since it left the Demographics tab; and the
    race / ethnicity / payer cuts are now known to be UNDERIVABLE rather than merely underived — US Core
    nests race and ethnicity under `extension[url=…].extension[url=ombCategory]` and the emitted
    extension struct is flat, so the accessor fails with `No such struct field 'extension'`
    (`DATA-INSIGHT-EXTRACTION`), and payer lives on Coverage, not Patient.

    The old rule — keep a fabricated cut and MARK it, because removing it quietly shrinks a pane the
    design was built around — was right while "no table answers this YET" was true. It is not true, and
    an invented bar stops being the lesser evil against an absent one once the absence is a finding."""
    profile, _ = _run(define="Example Results",
                      columns=LIST_VALUED, counts=(33, 80))
    assert profile.sampled == []
    assert profile.value_sets == []
    assert profile.temporal == []
    assert not [s for s in profile.strata if s.sampled]


def test_a_wholly_derived_payload_says_run_without_qualification():
    """`mixed` on a page with nothing to warn about is a warning that trains readers to ignore it — and
    it was doing exactly that: the banner was raised by `value_sets` and `temporal`, blocks no surface
    drew, over tabs whose every figure was real."""
    profile, _ = _run()
    assert profile.provenance == PROVENANCE_RUN
    assert profile.sampled == []


def test_the_derived_cuts_are_the_only_cuts():
    """Every cut comes from the Patient extractor (`DATA-INSIGHT-EXTRACTION`) plus the composed age
    band. Nothing rides along, so the set is exactly the catalog — which is also what makes adding a
    demographic an entry in `extractors.py` rather than an edit to the query builder."""
    profile, _ = _run()
    curated = {e.id for e in elements_for(DEFAULT_MODEL, "Patient", kind=KIND_SCALAR)}
    assert {s.id for s in profile.strata} == curated | {"age"}


# ── absence ───────────────────────────────────────────────────────────────────────────────────────

def test_an_evaluation_that_was_never_run_raises_rather_than_profiling_nothing():
    with pytest.raises(MetricsUnavailable, match="has not been run"):
        _run(data_source_id="never-run")


def test_a_define_with_no_relation_here_raises_rather_than_returning_an_empty_profile():
    """CORE-NO-SILENT-MISLOWER — "never materialized here" and "materialized and found nothing" are
    opposite claims, and an empty profile would render as the second."""
    with pytest.raises(MetricsUnavailable, match="no result relation"):
        _run(define="Never Reached")
