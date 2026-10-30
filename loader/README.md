# Loader

The loader makes synthetic test data. The synthesizer generates synthetic FHIR patients from a measure's compiled
logic, with the expected result of every define for every patient, and a script lands them as a data source.

Measures are imported with `cql_databricks.data_bindings.measure_import.import_measures` in the client
(`CONTENT-IMPORT-MEASURES`), through the agent's `import-quality-measures` skill (`CONTENT-IMPORT-SKILL`). The
deploy loads the example measures and patients in `examples/` itself, with its shipped sources
(`scripts/deploy/shipped_sources`, `DEPLOY-SHIPPED-SOURCES`).

## Components

| Directory | What it does |
|---|---|
| `loader/synthesizer` | `cql_databricks_synth`: generate and verify synthetic cohorts from measure ELM |
| `loader/scripts` | Land a synthesized cohort as the Synthetic Population Large data source |

`loader/scripts` lands through the shipped sources' population lander (`shipped_sources.population`,
`DEPLOY-SHIPPED-SOURCES`). Synthea isn't used: the synthesizer generates every synthetic source from measure logic.

## Synthesizer

```bash
python -m cql_databricks_synth fixtures        # build the pinned measure fixtures into ~/.synth/fixtures
python -m cql_databricks_synth generate --elm ~/.synth/fixtures/<measure> \
    --valuesets ~/.synth/fixtures/<measure> --out /tmp/cohort --patients 1000 --seed 42
python -m cql_databricks_synth verify --elm ~/.synth/fixtures/<measure> \
    --valuesets ~/.synth/fixtures/<measure> --out /tmp/cohort
```

The output is FHIR NDJSON and a manifest of expected results. The
[synthesizer README](synthesizer/README.md) covers fixtures and generation modes.
[Synthetic data](docs/synthetic-data.md) is the step-by-step guide to generating and landing the synthetic
data sources.

The loader's requirements are in [docs/requirements.md](docs/requirements.md).

## Build and test

```bash
scripts/setup.sh                                       # the root venv
.venv/bin/python -m pytest -q loader/synthesizer       # extra: [dev]
```

`scripts/test.sh` runs it.
