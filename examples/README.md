# examples — notebooks and content

Six notebooks and three content directories that show how to run a CQL measure. They are published to `/Shared/CqlDatabricks/examples` in your workspace. See [`../README.md`](../README.md) for details on each notebook.

The content directories are **your responsibility in a real deployment** — not part of the product:

```
measures/     the CQL — sample measures, the helper libraries they include, and FHIRHelpers
valuesets/    the sample value sets the measures use
data/         a small FHIR NDJSON dataset with 48 synthetic patients
```

The directories are beside the notebooks so that each notebook can derive the path from its own location.
The deploy loads all three into your workspace (`CONTENT-SAMPLE-PRELOAD`).

## measures/

Four sample quality measures in the shape CMS uses for FHIR measures (`CONTENT-SAMPLE-STANDARDS`): QI-Core
6.0.0, proportion scoring, a measurement period passed in at run time, exclusions, exceptions and
supplemental data. Each has a `.cql` and a `.measure.json`:

| Library | What it computes | Better |
|---|---|---|
| `HypertensionBPControl` | Adults 18–85 with hypertension whose most recent blood pressure in the period is below 140/90 mmHg | Higher |
| `DiabetesHighOrMissingA1c` | Adults 18–75 with diabetes whose most recent HbA1c is above 9%, has no result, or was not done | Lower |
| `AdultFluImmunization` | Adults seen in the flu season who were vaccinated in it; declined or contraindicated vaccines are exceptions | Higher |
| `ColorectalScreening` | Adults 45–75 current on colorectal cancer screening, each test with its own lookback | Higher |

They include two helper libraries:
- `SampleMeasureCommon.cql` — qualifying visits, age, condition and procedure timing, hospice and palliative care
- `SampleSupplementalData.cql` — supplemental data: administrative gender and payer. Birth sex, race and
  ethnicity are not reported yet: the compiler does not yet read those US Core extensions correctly.

`FHIRHelpers.cql` is FHIRHelpers 4.0.1, unmodified, from the cqframework `quick` 4.8.0 artifact (Apache License
2.0). Every library includes it. The compiler bundles the same library, so the client resolves the include
itself; the copy here is so the content you load is complete.

These are samples written for this repository, not published measures.

In a real deployment, this is a **Databricks Git Folder** (your measures repo) or a **Unity Catalog Volume**. The client resolves `include` statements by name:

```python
client.cql(library="HypertensionBPControl").exec()
```

Over the sample patients, for 2026-01-01 to 2026-12-31, `HypertensionBPControl` gives:
- `Initial Population` and `Denominator`: 11 of 48 patients
- after `Denominator Exclusions`: 7 patients
- `Numerator` among those 7: 3, a rate of 3 of 7

## valuesets/

One NDJSON file per value set, one row per code: `{valueset_id, system, code, display}`. The value sets are
the repository's own (`http://example.org/fhir/ValueSet/sample-*`), not published value sets. Their codes come
only from code systems that are free to use:

| Code system | Used for | Terms |
|---|---|---|
| ICD-10-CM, ICD-10-PCS | Diagnoses; dialysis, transplant, colectomy, colonoscopy | Published by CMS and NCHS, free to use |
| HCPCS Level II | Wellness visits, hospice care, colonoscopy, sigmoidoscopy | Published by CMS, free to use |
| LOINC | Blood pressure, HbA1c, stool tests | Free, under the LOINC license ([`../NOTICE.md`](../NOTICE.md)) |
| CVX | Influenza vaccines | Published by CDC, free to use |
| HL7 terminology | Reasons a vaccine was not given, discharge to hospice, coverage type | CC0 |
| `http://example.org/fhir/CodeSystem/sample-local-codes` | Office, telehealth and preventive visits; CT colonography | Written for this repository |

No CPT or SNOMED CT code is used: both need a license. Visit types and CT colonography use local codes instead.

## data/

FHIR NDJSON: one file per resource type. `mount()` reads it against the compiler's FHIR model.
- `Patient.ndjson`: 48 synthetic patients (`pat-001`..`pat-048`)
- `Encounter.ndjson`: 49 encounters
- `Condition.ndjson`: 30 conditions
- `Observation.ndjson`: 35 blood pressure readings and laboratory results
- `Procedure.ndjson`: 11 procedures
- `Immunization.ndjson`: 7 immunizations, given or not given
- `Coverage.ndjson`: 11 coverages

Each patient is written to test one case of one measure, such as an age limit, a threshold, a lookback edge,
an exclusion or an exception: `pat-001`..`pat-016` hypertension, `pat-017`..`pat-026` diabetes, `pat-027`..`pat-035`
flu immunization and `pat-036`..`pat-048` colorectal screening. Every resource claims its QI-Core profile in
`meta.profile`.

All data is synthetic. The expected results for each measure, per patient and population, with the reason,
are in `expected-results/<Library>.json`. Each value is the population define's own value: a patient can be
in a numerator define and also excluded.
