"""End-to-end the synthesizer generation on the pilot measure (SYNTH-PDS-ORACLE,
SYNTH-PDS-STRATA, SYNTH-PDS-VERIFY, SYNTH-PDS-DETERMINISM).

The pilot fixture is real translator output: ``__fixtures__/pilot/SynthesizerPilot.cql``, a measure
written for these tests from generic shapes, translated by the Compiler Service (QICore 6.0.0), against
synthetic value-set expansions beside it.

Fixtures (ELM + expansions) are GENERATED, not committed (SYNTH-PDS-FIXTURE-PINS):
the pilot CQL is the committed source, pins.json holds the hashes, and
`python -m cql_databricks_synth fixtures` builds the cache these tests resolve from.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cql_databricks_synth.fixtures import dir_or_skip
from cql_databricks_synth.emit import serialize_value, write_cohort
from cql_databricks_synth.generate import generate
from cql_databricks_synth.strata import population_defines, proportion_strata
from cql_databricks_synth.valuesets import ValueSetIndex
from cql_databricks_synth.verify import verify_cohort

_FIXTURES = dir_or_skip("pilot")
_ELM_PATH = _FIXTURES / "SynthesizerPilot.elm.json"
_VALUESETS = _FIXTURES  # expansions are generated beside the ELM (SYNTH-PDS-FIXTURE-PINS)


@pytest.fixture(scope="module")
def elm() -> dict:
    return json.loads(_ELM_PATH.read_text())


@pytest.fixture(scope="module")
def valuesets() -> ValueSetIndex:
    return ValueSetIndex.from_ndjson_dir(_VALUESETS)


@pytest.fixture(scope="module")
def cohort(elm, valuesets):
    return generate(elm, valuesets, seed=42, per_stratum=3)


def test_proportion_strata_enumerate_the_funnel(elm):
    populations = population_defines(elm)
    assert populations["initial-population"] == "Initial Population"
    assert populations["denominator-exclusion"] == "Denominator Exclusions"
    labels = [s.label for s in proportion_strata(populations)]
    assert labels == [
        "!IPP",
        "IPP+!DEN",
        "IPP+DEN+DENEX",
        "IPP+DEN+!DENEX+NUM",
        "IPP+DEN+!DENEX+!NUM",
        "null:IPP",
    ]


def test_denominator_alias_stratum_is_reported_unsatisfiable(cohort):
    outcome = {o.stratum: o for o in cohort.outcomes}["IPP+!DEN"]
    assert outcome.status == "unsatisfiable"
    assert "Initial Population" in outcome.detail
    assert outcome.conflicts  # the minimal conflicting sites travel with the report


def test_all_expressible_strata_are_satisfied(cohort):
    satisfied = {o.stratum for o in cohort.outcomes if o.status == "satisfied"}
    assert satisfied == {
        "!IPP", "IPP+DEN+DENEX", "IPP+DEN+!DENEX+NUM", "IPP+DEN+!DENEX+!NUM", "null:IPP",
    }
    for outcome in cohort.outcomes:
        if outcome.status == "satisfied":
            assert outcome.patient_count == 3


def test_ground_truth_is_the_stratum_by_construction(cohort):
    by_label = {
        "!IPP": ("false", "false"),
        "IPP+DEN+DENEX": ("true", "true"),
        "IPP+DEN+!DENEX+NUM": ("true", "true"),
        "IPP+DEN+!DENEX+!NUM": ("true", "true"),
    }
    for patient in cohort.patients:
        expected = {k: serialize_value(v) for k, v in patient.expected.items()}
        if patient.stratum in by_label:
            ipp, den = by_label[patient.stratum]
            assert expected["Initial Population"] == ipp
            assert expected["Denominator"] == den
        if patient.stratum == "IPP+DEN+DENEX":
            assert expected["Denominator Exclusions"] == "true"
        if patient.stratum == "IPP+DEN+!DENEX+NUM":
            assert expected["Numerator"] == "true"
        if patient.stratum == "IPP+DEN+!DENEX+!NUM":
            assert expected["Numerator"] == "false"


def test_null_membership_path_is_generated(cohort):
    """Three-valued logic: a missing determining element makes the population null,
    and the manifest records null — not false — as the expected value."""
    null_patients = [p for p in cohort.patients if p.stratum == "null:IPP"]
    assert len(null_patients) == 3
    for patient in null_patients:
        assert patient.expected["Initial Population"] is None
        assert patient.expected["Denominator"] is None
        assert patient.boundary_tags == ["null:initial-population"]


def test_stratum_routes_vary_across_patients(cohort):
    """Different patients in one stratum take different routes out (the coverage point
    of seeding route choice per patient)."""
    outs = [p for p in cohort.patients if p.stratum == "!IPP"]
    signatures = set()
    for patient in outs:
        p = patient.resources[0]
        signatures.add((
            patient.expected["Age In Range"],
            patient.expected["Qualifying Visits"] != [],
            "birthDate" in p,
        ))
    assert len(signatures) > 1


def test_every_resource_is_tagged_synthetic(cohort):
    for patient in cohort.patients:
        for resource in patient.resources:
            security = resource["meta"]["security"]
            assert any(c["code"] == "HTEST" for c in security)
            assert any(t["system"].endswith("/patient-data-synthesizer") for t in resource["meta"]["tag"])


def test_emitted_cohort_reverifies_from_disk(tmp_path, elm, valuesets, cohort):
    write_cohort(cohort, tmp_path)
    assert verify_cohort(elm, valuesets, tmp_path) == []


def test_generation_is_byte_deterministic(tmp_path, elm, valuesets):
    for run in ("a", "b"):
        write_cohort(generate(elm, valuesets, seed=7, per_stratum=2), tmp_path / run)
    files_a = sorted((tmp_path / "a").iterdir())
    files_b = sorted((tmp_path / "b").iterdir())
    assert [f.name for f in files_a] == [f.name for f in files_b]
    for file_a, file_b in zip(files_a, files_b):
        assert file_a.read_bytes() == file_b.read_bytes()


def test_soundness_across_seeds(elm, valuesets):
    """Property-style sweep: whatever routes the seed picks, every emitted patient's
    recomputed defines must equal the manifest ground truth (the generator would have
    raised on any violation; this asserts the sweep completes across the route space)."""
    for seed in (1, 2, 3, 11, 99):
        cohort = generate(elm, valuesets, seed=seed, per_stratum=2)
        assert len(cohort.patients) == 10


def test_manifest_rows_carry_the_join_keys(tmp_path, elm, valuesets, cohort):
    write_cohort(cohort, tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert len(rows) == len(cohort.patients)
    for row in rows:
        assert row["measure_id"] == "SynthesizerPilot"
        assert row["measure_version"] == "1.0.0"
        assert row["cohort_id"] == cohort.cohort_id
        assert row["seed"] == 42
        assert row["config_hash"] == cohort.config_hash
        assert set(row["expected"]) == {d["name"] for d in elm["library"]["statements"]["def"]}


def test_realistic_counts_realize_the_funnel(elm):
    """SYNTH-PDS-STRATA: target rates translate to per-stratum counts that sum to the
    total, with structurally contradictory strata at zero."""
    from cql_databricks_synth.strata import realistic_counts

    populations = population_defines(elm)
    strata = proportion_strata(populations)
    counts = realistic_counts(strata, populations, 1000)
    assert sum(counts.values()) == 1000
    assert counts["IPP+!DEN"] == 0
    assert counts["!IPP"] == 300
    assert counts["null:IPP"] == 10
    denominator = 1000 - 300 - 10
    assert counts["IPP+DEN+DENEX"] == round(denominator * 0.05)
    scored = denominator - counts["IPP+DEN+DENEX"]
    assert counts["IPP+DEN+!DENEX+NUM"] == round(scored * 0.72)
    assert counts["IPP+DEN+!DENEX+!NUM"] == scored - counts["IPP+DEN+!DENEX+NUM"]


def test_streaming_writes_the_same_bytes_as_in_memory(tmp_path, elm, valuesets):
    """SYNTH-PDS-SCALE: generate_to_dir is the same generator core behind a streaming
    writer — identical inputs produce identical files down to the byte."""
    from cql_databricks_synth.generate import generate_to_dir

    counts = {"!IPP": 2, "IPP+DEN+!DENEX+NUM": 3, "null:IPP": 1}
    write_cohort(generate(elm, valuesets, seed=9, counts=counts), tmp_path / "memory")
    generate_to_dir(elm, valuesets, tmp_path / "stream", seed=9, counts=counts)
    names_memory = sorted(p.name for p in (tmp_path / "memory").iterdir())
    names_stream = sorted(p.name for p in (tmp_path / "stream").iterdir())
    assert names_memory == names_stream
    for name in names_memory:
        assert (tmp_path / "memory" / name).read_bytes() == \
            (tmp_path / "stream" / name).read_bytes()


def test_unknown_stratum_label_fails_loud(elm, valuesets):
    from cql_databricks_synth import SynthesizerError

    with pytest.raises(SynthesizerError, match="unknown strata"):
        generate(elm, valuesets, seed=1, counts={"NOT-A-STRATUM": 1})


def test_verify_flags_a_tampered_bundle(tmp_path, elm, valuesets, cohort):
    """The verify pass is a real check: corrupt one determining attribute on disk and
    the mismatch surfaces (KNOWLEDGE-FAIL-LOUD, SYNTH-PDS-VERIFY)."""
    write_cohort(cohort, tmp_path)
    patient_file = tmp_path / "Patient.ndjson"
    rows = [json.loads(line) for line in patient_file.read_text().splitlines()]
    target = next(r for r in rows if "birthDate" in r)
    target["birthDate"] = "1900-01-01"  # far outside the 30-70 age band
    patient_file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    mismatches = verify_cohort(elm, valuesets, tmp_path)
    assert any(m.patient_id == target["id"] and m.define == "Age In Range" for m in mismatches)
