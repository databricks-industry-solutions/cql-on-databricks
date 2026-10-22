# Loader

The loader puts content and test data into Unity Catalog. It has two tools:

- The importer loads measure content and tags the schemas so Data Bindings can find them. The deploy uses it
  to load the sample measures and patients in `examples/`; you can use it to import an eCQI measure package
  you choose, behind its terms.
- The synthesizer generates synthetic FHIR patients from a measure's compiled logic, with the expected
  result of every define for every patient.

Neither tool depends on another feature, except that the importer takes its tag names from the
`cql-databricks` client.

## Components

| Directory | What it does |
|---|---|
| `loader/importer` | `content_ingest`: load the sample content (`samples.py`) or one eCQI package (`ecqi.py`), then tag it |
| `loader/synthesizer` | `cql_databricks_synth`: generate and verify synthetic cohorts from measure ELM |
| `loader/scripts` | Land a synthesized cohort as the Synthetic Population Large data source |

`content_ingest` also holds the shipped data sources' parts (`SHIPPED-SOURCES-IN-LOADER`): the population lander
every shipped source lands through (`population.py`), the change-log exemplar and the sources' names
(`sources.py`). Synthea isn't used: the synthesizer generates every synthetic source from measure logic.

## Importer

The deploy loads the sample measures, their value sets and their patients (`scripts/deploy/provision-samples.py`):

| Where | What it holds |
|---|---|
| `<catalog>.sample_measures.measure`, `.library`, `.valueset` | The sample measures and libraries from `examples/measures`, and their value sets |
| `<catalog>.synthetic_population_small` | The data source `synthetic-population-small`: the patients in `examples/data` |

To import an eCQI measure package yourself, run the importer through `scripts/with-local-env.sh`, which exports
the workspace settings from `databricks.local.yml` and local-run settings from the optional root `.env`:

```bash
scripts/with-local-env.sh .venv/bin/python -m content_ingest ecqi <package-file-name-or-zip>
```

It shows the third-party terms and the package's own copyright, asks you to type `I AGREE` before the download
and again before loading, then writes `<catalog>.ecqi_measures.measure` and `.library` (the package as shipped)
and `<catalog>.vsac.valueset` (expansions from VSAC with your own `UMLS_API_KEY`). The
[importer README](importer/README.md) covers the flags.

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
.venv/bin/python -m pytest -q loader/importer          # extra: [dev]. CI also installs [spark]
.venv/bin/python -m pytest -q loader/synthesizer       # extra: [dev]
```

`scripts/test.sh` runs both suites.
