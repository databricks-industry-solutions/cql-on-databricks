# Synthetic data

The synthetic data sources, how they are generated, and how to land them in your workspace. Every patient in them
is fabricated by the synthesizer from the example measures in `examples/measures`.

| Data source (tag) | Schema in `uc_catalog` | What it holds |
|---|---|---|
| `synthetic-population-small` | `synthetic_population_small` | About 1,000 patients per example measure, in `examples/data`. The deploy lands it, and it is the default data source (`DATA-SYNTH-SMALL`) |
| `synthetic-population-large` | `synthetic_population_large` | 250,000 patients requested per example measure, each with its expected results. You generate and land it (`DATA-SYNTH-LARGE`) |

Both are built in the realistic mix, shaped by each measure's `<Measure>.synth.json`
(`SYNTH-PDS-MEASURE-CONFIG`), and every patient is re-verified as it is generated (`SYNTH-PDS-VERIFY`).

## Before you start

- Run `scripts/setup.sh` once, from the repository root.
- The `fixtures` step gets each measure's ELM from a local Compiler Service. Start one in another shell and leave
  it running:

  ```bash
  ./gradlew :compiler:server:run          # http://127.0.0.1:8080
  ```

- Build the pinned fixtures once (`SYNTH-PDS-FIXTURE-PINS`):

  ```bash
  .venv/bin/python -m cql_databricks_synth fixtures
  ```

## Small: the example patients

The small population is committed in `examples/data`, so nobody has to synthesize it: `scripts/deploy/deploy.sh`
lands it. It ships with no expected results; the answer keys are the conformance tests' (`compiler/conformance`).
Its recipe, `loader/synthesizer/recipes/synthetic-small.json`, names each measure's seed, the patient count and the
measurement period. Regenerate it by hand after a change to an example measure, its config, the recipe or the
synthesizer, then review and commit the diff. No build, test or deploy step runs it:

```bash
scripts/generate-example-data.sh
```

The script builds and starts a local Compiler Service, builds the recipe's measure fixtures, writes the population
into `examples/data` and stops the service. It needs no workspace. The build is deterministic, and the synthesizer
tests fail when the committed files differ from what the recipe builds. It writes about 7 MB.

## Large: the synthesizer cohorts

Its recipe, `loader/synthesizer/recipes/synthetic-large.json`, names each measure's seed, the patient count per
cohort and the measurement period. Generate and land it by hand, after deploying ([deploy guide](../../docs/deploy.md)).
No build, test or deploy step runs it:

```bash
scripts/generate-large-data.sh                       # every cohort in the recipe
scripts/generate-large-data.sh --measure <measure>   # one; repeatable
```

For each cohort the script generates it with its expected results into a scratch directory, verifies it, lands it
with `loader/scripts/synthetic-population/load.py` (through `scripts/with-local-env.sh`, which sets the catalog
and the profile from `databricks.local.yml`) and deletes the scratch copy. Like the small one, it first builds the
measure fixtures with a local Compiler Service.

Each measure has its own seed: patient IDs are `pds-<seed>-<n>`, so two cohorts with the same seed collide.
`pilot` and `patterns` are the synthesizer's own test fixtures, not measures to land. `--measurement-period` is
required, because the example measures declare their period with no default. The data source holds every cohort
landed so far. Landing a cohort again replaces only that cohort.

A 250,000-patient cohort is 560 to 900 MB of NDJSON, uploaded gzipped as 10 to 18 MB. Generating, verifying and
landing one took 3.5 to 5 minutes end to end on 2026-10-22, about 18 minutes for all four. A measure with no null stratum emits fewer patients: the null share is dropped.

The [synthesizer README](../synthesizer/README.md) covers the other generation modes.
