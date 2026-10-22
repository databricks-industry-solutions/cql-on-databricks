# Synthetic data

How to generate the synthetic data sources and land them in your workspace. Nothing synthetic is stored in
this repository: you generate it, and it is fabricated.

| Data source (tag) | Schema in `uc_catalog` | What it holds |
|---|---|---|
| `synthetic-population-large` | `synthetic_population_large` | Synthesizer cohorts, 250,000 patients requested per measure, each patient with its expected results (`DATA-SYNTH-LARGE`) |

The sample patients (`sample-patients`, `DATA-SAMPLE-SOURCE`) are not covered here: `scripts/deploy/deploy.sh`
loads them from `examples/data`.

## Before you start

- Deploy first ([deploy guide](../../docs/deploy.md)).
- Run `scripts/setup.sh` once, from the repository root.
- Run each workspace command through `scripts/with-local-env.sh`. It sets the catalog and the profile
  from `databricks.local.yml`.

## Large: the synthesizer cohorts

The `fixtures` step gets each measure's ELM from a local Compiler Service. Start one in another shell and
leave it running:

```bash
./gradlew :compiler:server:run          # http://127.0.0.1:8080
```

Then build the fixtures once, and generate, verify and land a cohort per measure:

```bash
scripts/with-local-env.sh .venv/bin/python -m cql_databricks_synth fixtures

M=<measure>; F=~/.synth/fixtures/$M
.venv/bin/python -m cql_databricks_synth generate --elm $F --valuesets $F --out /tmp/$M --patients 250000 --seed 1 \
    --measurement-period 2026-01-01/2026-12-31 --config $F/measure-config.json
.venv/bin/python -m cql_databricks_synth verify --elm $F --valuesets $F --out /tmp/$M
scripts/with-local-env.sh .venv/bin/python loader/scripts/synthetic-population/load.py --ndjson-dir /tmp/$M --cohort $M
```

Repeat the last four lines for each pinned measure (none of the sample measures is pinned yet; `pilot` is the
synthesizer's own test fixture, not a measure to land). `--measurement-period` is required for a measure that
declares its period with no default, as the sample measure does. Use a different `--seed` for each measure:
patient IDs are `pds-<seed>-<n>`, so two cohorts with the same seed collide. The data source holds every
cohort landed so far. Landing a cohort again replaces only that cohort. `--config` is the measure's own realism
(`SYNTH-PDS-MEASURE-CONFIG`); drop it for a measure that has none.

A 250,000-patient cohort is 560 to 900 MB of NDJSON. It takes 1 to 6 minutes to generate, 1 to 2 to verify
and 4 to 5 to land. A measure with no null stratum emits fewer patients: the null share is dropped.

The [synthesizer README](../synthesizer/README.md) covers the other generation modes.
