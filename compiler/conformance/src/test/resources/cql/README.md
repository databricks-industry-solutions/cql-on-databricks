# Authored CQL corpus (the source of truth)

These `.cql` files are the **authored source of truth** (`overview.md` section 3,
`TECH-ELM-INPUT`). They are committed; their translated **ELM is a generated artifact and
is never committed** — it is produced into `compiler/conformance/build/elm/` by the translation tool
(`:compiler:conformance:translateCorpus`) at build/test time, and the *same* authored
CQL drives both the engine's `Load` (via that ELM) and the Java ground-truth reference (via
the same translator), so neither side of the differential can diverge.

## Layout

- `operators/` — expression-level fixtures, one file per CQL **operator family**. These
  are the "tests which define CQL expressions aimed at testing key operators." In Phase 0
  they prove the translate→Load→round-trip path; as each operator family is lowered
  (Phases 1–4) they become per-operator ground-truth differential targets
  (`CONF-GATE-OPERATOR`, `CONF-GATE-MEASURE`).
- `measures/` — small whole-"measure" fixtures. `TrivialBoolean.cql` is the Phase 0
  trivial measure driven end-to-end through the Java reference ground-truth harness
  (`PHASE-0` exit, `CONF-GROUND-TRUTH`).

## Phase 0 scope note (see gaps.md, gap G2)

These fixtures are authored against the CQL **System model** (no `using FHIR`), so they
translate and run with no external FHIR model-info or terminology dependency — keeping the
Phase 0 harness hermetic. Retrieve-, terminology-, and FHIR-`value[x]`-bearing fixtures
arrive with the data-access work in Phase 2 (`DATA-RETRIEVER`, `LANG-TERMINOLOGY`), which
is when the engine first needs the FHIR model binding.
