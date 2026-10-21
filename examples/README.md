# examples — notebooks and content

Six notebooks and two content directories that show how to run a CQL measure. They are published to `/Shared/CqlDatabricks/examples` in your workspace. See [`../README.md`](../README.md) for details on each notebook.

The two content directories are **your responsibility in a real deployment** — not part of the product:

```
measures/     the CQL — a sample measure and the helper library it includes
data/         a small FHIR NDJSON dataset with twenty synthetic patients
```

The directories are beside the notebooks so that each notebook can derive the path from its own location.

## measures/

- `BasicEncounterAndCondition.cql` — the sample measure (deliberately small, for easy verification)
- `EncounterDuringPeriod.cql` — a helper library the measure includes
- `BasicEncounterAndCondition.measure.json` — the FHIR Measure resource
- `BasicEncounterAndCondition.synth.json` — how the synthesizer makes a realistic population for it (`SYNTH-PDS-MEASURE-CONFIG`)

In a real deployment, this is a **Databricks Git Folder** (your measures repo) or a **Unity Catalog Volume**. The client resolves `include` statements by name:

```python
client.cql(library="BasicEncounterAndCondition").exec()
```

The sample measure defines:
- `Has Encounter During Period`: true for 18 of 20 patients (for 2026-01-01 to 2026-12-31)
- `Has Any Condition`: true for 16 of 20 patients
- `Initial Population`: true for 16 of 20 (both encounter and condition)

## data/

FHIR NDJSON: one file per resource type. `mount()` reads it against the compiler's FHIR model.
- `Patient.ndjson`: 20 synthetic patients (`pat-001`..`pat-020`)
- `Encounter.ndjson`: 29 encounters
- `Condition.ndjson`: 16 conditions

All data is synthetic. Expected results over the sample measure are documented in `expected-results/BasicEncounterAndCondition.json`.
