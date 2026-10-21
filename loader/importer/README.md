# Importer

`content_ingest` loads CQL measure content into Unity Catalog. It has two inputs, which share the same table
writers and tags. The [loader README](../README.md) lists what it writes and how to run it.

| Input | Run by | What it loads |
|---|---|---|
| The sample measures, value sets and patients in `examples/` (`samples.py`, `CONTENT-SAMPLE-PRELOAD`) | The deploy (`scripts/deploy/provision-samples.py`) | `<catalog>.sample_measures` (knowledge and terminology) and the data source `sample-patients` |
| One eCQI measure package you choose (`ecqi.py`, `CONTENT-ECQI-TERMS-GATE`) | You: `python -m content_ingest ecqi <package>` | `--schema` (measures and libraries) and `--valueset-schema` (their VSAC expansions) |

The repository ships no official measure content and no list of measures. Everything in `examples/` is
written for this repository.

## Importing an eCQI package

```bash
scripts/with-local-env.sh .venv/bin/python -m content_ingest ecqi <package-file-name-or-zip>
```

Name the package by its file name on the measure's eCQI page, or pass a zip you already have. The import:

1. Shows the third-party terms and asks you to type `I AGREE`. Nothing is downloaded before that.
2. Downloads the package from the eCQI Resource Center to `CONTENT_CACHE_DIR`.
3. Shows each measure's own `copyright` field, verbatim, and asks you to type `I AGREE` again. Nothing is
   loaded before that.
4. Loads the package's `Measure` and `Library` resources unchanged (`CONTENT-ECQI-LOAD-UNCHANGED`): no edit to the
   Measurement Period, no stripping, no renaming. The period is a run-time parameter.
5. Expands the value sets its libraries name from VSAC, with your own `UMLS_API_KEY`. A value set that cannot
   be expanded stops the import.
6. Tags both schemas, so the Apps and `cql(library=...)` find the measures.

The terms text is a draft, pending legal review (`ecqi.TERMS_STATUS`).

## Flags

| Flag | Default | What it does |
|---|---|---|
| `--catalog` | `CONTENT_CATALOG` (no built-in name; required unless `--no-catalog`) | Destination catalog |
| `--schema` | `ecqi_measures` | Schema for measures and libraries |
| `--valueset-schema` | `vsac` | Schema for value-set expansions |
| `--knowledge-name`, `--terminology-name` | the schema name | The tag values |
| `--accept-terms` | off | Accept both prompts without asking, for a non-interactive run. The texts are still shown, and the acceptance is logged |
| `--no-valuesets` | off | Load the measures without their expansions. Needed when `UMLS_API_KEY` is not set |
| `--no-catalog` | off | No catalog prefix, for local Spark. Nothing is tagged |
| `--dry-run` | off | Read and transform only. Write nothing |
| `-v`, `--verbose` | off | Debug logging |

`python -m content_ingest tag` tags `--schema` and `--valueset-schema` as they are and loads nothing.

## Environment

| Variable | What it is |
|---|---|
| `CONTENT_CATALOG` | Destination catalog. `scripts/with-local-env.sh` sets it from the bundle's `uc_catalog` |
| `CONTENT_CACHE_DIR` | Download directory. Default `~/.content-ingest/cache` |
| `SPARK_REMOTE` | A Spark Connect URL |
| `DATABRICKS_HOST` | Use Databricks Connect, with `DATABRICKS_SERVERLESS` or `DATABRICKS_CLUSTER_ID` |
| `UMLS_API_KEY` | Your own VSAC key. Required unless `--no-valuesets` |

The session comes from `SPARK_REMOTE`, then `DATABRICKS_HOST`, then a local Spark session. Started outside
`scripts/with-local-env.sh` in a checkout that has `databricks.local.yml` or `.env`, the importer refuses
to run.

## Synthetic patients

Synthea isn't used: the synthesizer generates synthetic data sources from measure logic
([synthetic data](../docs/synthetic-data.md)).

## Tables and load strategy

| Table | Load |
|---|---|
| `measure`, `library` | MERGE on `(url, version)` |
| `valueset` | Delete the source package's rows, then append |
| `retrieve_<type>_data` (a data source's mounts) | Overwrite |

`measure` and `library` hold each resource as a `VARIANT` column, `resource`, beside `url` and `version`. A
data source's mount holds it as `__resource`, a typed `STRUCT` from `resource-schemas.json`.
