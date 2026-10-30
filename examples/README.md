# examples — notebooks and content

Seven notebooks and three content directories that show how to run a CQL measure. They are published to `/Shared/CqlDatabricks/examples` in your workspace. See [`../README.md`](../README.md) for details on each notebook.

The content directories are **your responsibility in a real deployment** — not part of the product:

```
measures/     the CQL — example measures, the helper libraries they include, and FHIRHelpers
valuesets/    the example value sets the measures use
data/         a FHIR NDJSON dataset of 4,000 synthetic patients
```

The directories are beside the notebooks so that each notebook can derive the path from its own location.
The deploy loads all three into your workspace (`CONTENT-EXAMPLE-PRELOAD`).

## measures/

Four example quality measures in the shape CMS uses for FHIR measures (`CONTENT-EXAMPLE-STANDARDS`): QI-Core
6.0.0, proportion scoring, a measurement period passed in at run time, exclusions and
exceptions. Each has a `.cql`, a `.measure.json`, and a `.synth.json` the synthesizer reads for its realism
(`SYNTH-PDS-MEASURE-CONFIG`):

| Library | What it computes | Better |
|---|---|---|
| `HypertensionBPControl` | Adults 18–85 with hypertension whose most recent blood pressure in the period is below 140/90 mmHg | Higher |
| `DiabetesHighOrMissingA1c` | Adults 18–75 with diabetes whose most recent HbA1c is above 9%, has no result, or was not done | Lower |
| `AdultFluImmunization` | Adults seen in the flu season who were vaccinated in it; declined or contraindicated vaccines are exceptions | Higher |
| `ColorectalScreening` | Adults 45–75 current on colorectal cancer screening, each test with its own lookback | Higher |

They include one helper library, `MeasureCommon.cql`: qualifying visits, age, condition and procedure
timing, hospice and palliative care.

`FHIRHelpers.cql` is FHIRHelpers 4.0.1, unmodified, from the cqframework `quick` 4.8.0 artifact (Apache License
2.0). Every library includes it. The compiler bundles no FHIRHelpers, so an include of it resolves from this
copy, like any other library (`LANG-FHIRHELPERS-FROM-CONTENT`).

These are examples written for this repository, not published measures.

In a real deployment, this is a **Databricks Git Folder** (your measures repo) or a **Unity Catalog Volume**. The client resolves `include` statements by name:

```python
client.cql(library="HypertensionBPControl").exec()
```

Over the example patients, for 2026-01-01 to 2026-12-31, `HypertensionBPControl` gives:
- `Initial Population` and `Denominator`: 590 of 4,000 patients
- after `Denominator Exclusions`: 543 patients
- `Numerator` among those 543: 337, a rate of about 62%

## valuesets/

One NDJSON file per value set, one row per code: `{valueset_id, system, code, display}`. The value sets are
the repository's own (`http://example.org/fhir/ValueSet/*`), not published value sets. Their codes come
only from code systems that are free to use:

| Code system | Used for | Terms |
|---|---|---|
| ICD-10-CM, ICD-10-PCS | Diagnoses; dialysis, transplant, colectomy, colonoscopy | Published by CMS and NCHS, free to use |
| HCPCS Level II | Wellness visits, hospice care, colonoscopy, sigmoidoscopy | Published by CMS, free to use |
| LOINC | Blood pressure, HbA1c, stool tests | Free, under the LOINC license |
| CVX | Influenza vaccines | Published by CDC, free to use |
| HL7 terminology | Reasons a vaccine was not given, discharge to hospice, coverage type | CC0 |
| `http://example.org/fhir/CodeSystem/local-codes` | Office, telehealth and preventive visits; CT colonography | Written for this repository |

No CPT or SNOMED CT code is used: both need a license. Visit types and CT colonography use local codes instead.

## data/

The synthetic population small (`DATA-SYNTH-SMALL`): FHIR NDJSON, one file per resource type. `mount()` reads it
against the compiler's FHIR model, and the deploy lands it as the data source `synthetic-population-small`.
- `Patient.ndjson`: 4,000 synthetic patients
- `Encounter.ndjson`: 2,422 encounters
- `Condition.ndjson`: 1,270 conditions
- `Observation.ndjson`: 1,167 blood pressure readings and laboratory results
- `Procedure.ndjson`: 295 procedures
- `Immunization.ndjson`: 332 immunizations, given or not given

The synthesizer built it from `loader/synthesizer/recipes/synthetic-small.json`: about 1,000 patients for each
example measure, at realistic rates set by the measure's `.synth.json`, with patient ids `pds-<seed>-<n>`. Every
resource claims its QI-Core profile in `meta.profile` and carries the synthesizer's synthetic markers. It ships with
no expected results: the answer keys are the conformance tests' (`compiler/conformance`). To regenerate it, run
`scripts/generate-example-data.sh` by hand; see [synthetic-data.md](../loader/docs/synthetic-data.md).
