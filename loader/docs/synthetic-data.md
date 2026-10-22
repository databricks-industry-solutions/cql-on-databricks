# Synthetic data

The synthetic data sources, how they are generated, and how to land them in your workspace. Every patient in them
is fabricated by the synthesizer from the sample measures in `examples/measures`.

| Data source (tag) | Schema in `uc_catalog` | What it holds |
|---|---|---|
| `synthetic-population-small` | `synthetic_population_small` | About 1,000 patients per sample measure, in `examples/data`. The deploy lands it, and it is the default data source (`DATA-SYNTH-SMALL`) |
| `synthetic-population-large` | `synthetic_population_large` | 250,000 patients requested per sample measure, each with its expected results. You generate and land it (`DATA-SYNTH-LARGE`) |

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

## Small: the sample patients

The small population is committed in `examples/data`, so nobody has to synthesize it: `scripts/deploy/deploy.sh`
lands it. It ships with no expected results; the answer keys are the conformance tests' (`compiler/conformance`).
Its recipe, `loader/synthesizer/recipes/synthetic-small.json`, names each measure's seed, the patient count and the
measurement period. Regenerate it by hand after a change to a sample measure, its config, the recipe or the
synthesizer, then review and commit the diff. No build, test or deploy step runs it:

```bash
scripts/generate-sample-data.sh
```

The script builds and starts a local Compiler Service, builds the recipe's measure fixtures, writes the population
into `examples/data` and stops the service. It needs no workspace. The build is deterministic, and the synthesizer
tests fail when the committed files differ from what the recipe builds. It writes about 7 MB.

## Large: the synthesizer cohorts

Generate, verify and land a cohort per measure. Run each workspace command through `scripts/with-local-env.sh`,
which sets the catalog and the profile from `databricks.local.yml`, and deploy first
([deploy guide](../../docs/deploy.md)).

```bash
M=<measure>; S=<seed>; F=~/.synth/fixtures/$M
.venv/bin/python -m cql_databricks_synth generate --elm $F --valuesets $F --out /tmp/$M --patients 250000 --seed $S \
    --measurement-period 2026-01-01/2026-12-31 --config $F/measure-config.json
.venv/bin/python -m cql_databricks_synth verify --elm $F --valuesets $F --out /tmp/$M
scripts/with-local-env.sh .venv/bin/python loader/scripts/synthetic-population/load.py --ndjson-dir /tmp/$M --cohort $M
```

| Measure | Seed |
|---|---|
| `HypertensionBPControl` | 1 |
| `DiabetesHighOrMissingA1c` | 2 |
| `AdultFluImmunization` | 3 |
| `ColorectalScreening` | 4 |

Each measure has its own seed: patient IDs are `pds-<seed>-<n>`, so two cohorts with the same seed collide.
`pilot` and `patterns` are the synthesizer's own test fixtures, not measures to land. `--measurement-period` is
required, because the sample measures declare their period with no default. The data source holds every cohort
landed so far. Landing a cohort again replaces only that cohort.

A 250,000-patient cohort is 560 to 900 MB of NDJSON. It takes 1 to 6 minutes to generate, 1 to 2 to verify
and 4 to 5 to land. A measure with no null stratum emits fewer patients: the null share is dropped.

The [synthesizer README](../synthesizer/README.md) covers the other generation modes.
