# The conformance corpus — what this data is and where it came from

Ground truth for "does this measure compute the right population." Every file here is **synthetic**.
There are no real patients and no PHI.

## Provenance

Every file here is written for this repository: the patients, the value-set expansions and the expected
results. None is taken from published measure content. The patients are on the on-disk contract this
repository's retrieve path reads (see [data-format.md](../../../../../docs/data-format.md)).

`measures/` holds one directory per measure written for this repository, named for its library. The
measure gates find them there (`CONF-MEASURE-DISCOVERY`):

| Directory | Measure | Patients |
|---|---|---|
| `measures/BasicEncounterAndCondition/` | An encounter during the measurement period and any condition | 13 |

Each has an `answer-key.json`, written by hand. It names the measure, its version, its measurement period
and its populations (population define to population code), and gives, for each patient, the value of each
population define (1 true, 0 false or null) and why. `RefScoreSpec` checks each key against the Java CQF
reference (`CONF-REF-ANSWER-KEY`) and `MeasureScoreSpec` against the engine (`CONF-GATE-SCOREKEY`).

## Layout

| Path | What it is |
|---|---|
| `measures/<library>/` | One `<Type>.ndjson` per FHIR resource type, and the measure's `answer-key.json`. |
| `valuesets/valueset-cql-test-<name>.ndjson` | The expansion of one value set used by a fixture: `{valueset_id, system, code, display}` rows in the synthetic code system. |
| `qicore/` | The small QICore fixtures' patients (profile-only retrieve, negation rationale, day-precision `during`, age-coded onsets, `value[x]` off a define, `Coverage` and `Immunization` keyed by `beneficiary` and `patient`). |
| `fhir/` | A small general-purpose data set for operator and language tests, not tied to one measure. |

## Terminology

The `measures/`, `qicore/` and `valuesets/` data use only the synthetic code system
`http://example.org/fhir/CodeSystem/cql-test`. The `fhir/` data references a few standard codes (SNOMED CT, LOINC, RxNorm, UCUM and HL7 code systems),
used **solely to exercise logic against synthetic patients**. This directory is **not a terminology
distribution**: no code system is reproduced in whole or in part. Full attribution is in
[`NOTICE`](../../../../../../NOTICE.md).

**Value set definitions are not vendored.** Only the expansions each test needs are here.
