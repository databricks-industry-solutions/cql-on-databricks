# Synthesizer

`cql_databricks_synth` generates synthetic patients from a measure's compiled ELM (`SYNTH-PDS-MODULE`). It
enumerates the population strata the measure can express, solves each stratum's constraints into
patients, and writes FHIR NDJSON plus a manifest of every define's expected value for every patient. It
then re-evaluates every patient against the same ELM to check the manifest. The
[loader README](../README.md) shows the three commands.

The generation core uses only the standard library and makes no network calls. ELM comes from the Compiler
Service's `/translate` ahead of time. Value-set expansions come from the terminology table.

## Fixtures

Measure fixtures (per-library ELM and expansions) are generated, never committed
(`SYNTH-PDS-FIXTURE-PINS`). `src/cql_databricks_synth/pins.json` pins their hashes for the four sample measures in
`examples/measures`, with their configs and the conformance copies of their value sets, and for two fixtures
written for the synthesizer's own tests, each with its synthetic value sets: `pilot`, a small measure
(`tests/__fixtures__/pilot/`), and `patterns`, one define per CQL pattern the solver inverts
(`tests/__fixtures__/patterns/`). Each pin lists `examples/measures/FHIRHelpers.cql` with no ELM of its own: the
compiler bundles no FHIRHelpers.

```bash
python -m cql_databricks_synth fixtures                     # all pinned measures
python -m cql_databricks_synth fixtures --measure <key>     # one
python -m cql_databricks_synth fixtures --repin             # record new hashes instead of checking them
```

`fixtures` needs the Compiler Service (`--endpoint`, default `CQL_COMPILER_ENDPOINT` or
`http://127.0.0.1:8080`; start one with `./gradlew :compiler:server:run`). Every pin reads its expansions from
files in the repository; a pin whose expansions come from a terminology table also needs a
Databricks profile (`--profile`, default from `databricks.local.yml`) and `--catalog` (default
`CONTENT_CATALOG`). The cache is `~/.synth/fixtures/<measure>/`, or `CQL_DATABRICKS_SYNTH_CACHE`. Tests
that need a fixture skip when the cache is absent, and say how to build it.

## Generation modes

`generate --elm` takes one library's ELM JSON or a directory of `*.elm.json` files.

| Mode | Flags |
|---|---|
| Uniform | `--per-stratum N` patients per satisfiable stratum (default 1) |
| Realistic | `--patients N`, shaped by the measure's `--config <Measure>.synth.json` (`SYNTH-PDS-MEASURE-CONFIG`) and the rate flags `--out-of-ipp-rate`, `--exclusion-rate`, `--numerator-rate`, `--null-rate`, `--exception-rate`, each of which overrides the config |
| Explicit | `--count STRATUM_LABEL=N`, repeatable |

`population --recipe <file> --out <dir>` builds a population of several measures: one realistic cohort per pinned
measure, with the seed the recipe gives it, merged into one `<Type>.ndjson` per type with no manifest. It is how the
synthetic population small in `examples/data` is built from `recipes/synthetic-small.json`, by hand with
`scripts/generate-sample-data.sh` (`DATA-SYNTH-SMALL`). The synthetic population large has a recipe of the same
shape, `recipes/synthetic-large.json`, built and landed one cohort at a time with expected results by
`scripts/generate-large-data.sh` (`DATA-SYNTH-LARGE`).

`--seed` makes a run repeatable (default 42). `--measurement-period START/END` passes the Measurement Period at
run time, as inclusive ISO dates (`LANG-PARAMETER`); it is required for a measure that declares the period with no
default, and the manifest records it so `verify` uses the same one. A measure with no numerator (an initial
population alone) gets an `IPP` stratum in place of the numerator hit and miss.

## A measure's own realism

What is true of FHIR data in general lives in `distributions.py` and never names a measure. What one measure needs
to look like a population goes in `<Measure>.synth.json` beside its CQL (`SYNTH-PDS-MEASURE-CONFIG`):

```json
{
  "measure": "SynthesizerPilot",
  "version": "1.0.0",
  "populations": {"Initial Population": 0.6, "Denominator Exclusions": 0.1, "Numerator": 0.5},
  "routes": {"Denominator Exclusions": {"Pilot Exclusion Procedures": 3, "Repeated Exclusion Procedure": 1}},
  "quantities": {"Pilot Lab Test": {"range": [4, 6]}},
  "members": {"Pilot Condition": {"pilot-condition-1": 20}}
}
```

- `populations`: the rate of each population, conditional on the one above it. The denominator has none of its own.
- `routes`: weights over the branches of an `or` define, each branch a define or `exists` of one. A define of an
  included library is named by the alias the measure includes it as: `"Common.In Hospice Care"`.
- `quantities`: a plausible range for a declared code, or for every member of a declared value set.
- `members`: weights over a value set's members; an unlisted member keeps weight 1.

Every section is optional and every key must resolve against the measure, or the load fails listing each one. The
file is pinned in `pins.json` and copied into the fixture directory as `measure-config.json`; pass it with
`--config` in realistic mode.

## Landing a cohort

`loader/scripts/synthetic-population/load.py` lands a cohort as the Synthetic Population Large data source. Run it
with the root venv. Use one cohort directory and a distinct `--seed` per measure, so patient IDs don't
collide.

```bash
python loader/scripts/synthetic-population/load.py --ndjson-dir /tmp/cohort --cohort <measure> \
    --catalog <catalog> --profile <profile>
```
