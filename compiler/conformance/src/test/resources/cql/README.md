# Authored CQL corpus (the source of truth)

These `.cql` files are the **authored source of truth** (`overview.md` section 3,
`TECH-ELM-INPUT`). They are committed; their translated **ELM is a generated artifact and
is never committed** — it is produced into `compiler/conformance/build/elm/` by the translation tool
(`:compiler:conformance:translateCorpus`) at build/test time, and the *same* authored
CQL drives both the engine's `Load` (via that ELM) and the Java ground-truth reference (via
the same translator), so neither side of the differential can diverge.

## Layout

- `operators/` — expression-level fixtures, one file per CQL **operator family**. These
  are the "tests which define CQL expressions aimed at testing key operators." They are
  per-operator ground-truth differential targets (`CONF-GATE-OPERATOR`, `CONF-GATE-MEASURE`).
- `measures/` — small whole-"measure" fixtures. `TrivialBoolean.cql` is a
  trivial measure driven end-to-end through the Java reference ground-truth harness
  (`CONF-GROUND-TRUTH`).

## System-model fixtures

These fixtures are authored against the CQL **System model** (no `using FHIR`), so they
translate and run with no external FHIR model-info or terminology dependency — keeping the
harness hermetic. Retrieve-, terminology-, and FHIR-`value[x]`-bearing fixtures bind the FHIR
model (`DATA-RETRIEVER`, `LANG-TERMINOLOGY`).
