# Engineering principles

Code-quality rules for the compiler: `PROC-DRY` and `PROC-SOLID`. They constrain how the compiler is
built, not what CQL means. The cheapest way to get a wrong answer is to write the same rule twice and let
the copies drift.

## 1. One representation of each fact — `PROC-DRY`

A semantic rule, a physical convention, a value normalization or an evaluation path is written once.
Callers reference it. Two lines that only look alike but encode separate decisions stay separate.

Examples in this repository:

- One lowering path for every context. An Unfiltered library and a Patient measure both go through
  [`SqlFrameLowering`](../core/src/main/scala/com/databricks/cql/transpiler/lower/SqlFrameLowering.scala).
  Only the [`SqlEvalEnv`](../core/src/main/scala/com/databricks/cql/transpiler/lower/SqlEvalEnv.scala)
  differs. An Unfiltered library is the one-subject case, keyed by `SystemColumns.UnfilteredSubject`
  (`ARCH-SET-EVAL`).
- One differential driver.
  [`Differential`](../conformance/src/test/scala/com/databricks/cql/it/Differential.scala) runs every
  case; scalar, measure and FHIR fixtures are rows of data (`Differential.Case`).
- One value normalization. The compiler's results and the reference's results both pass through
  [`JsonNormalize`](../conformance/src/main/scala/com/databricks/cql/harness/JsonNormalize.scala).
- One home for semantics. Three-valued logic lives in
  [`Bool3`](../core/src/main/scala/com/databricks/cql/sema/Bool3.scala) (`LANG-3VL`,
  `TECH-SEMANTIC-LIB`). Lowering follows it.

## 2. SOLID — `PROC-SOLID`

Applied with plain Scala: sealed ADTs, immutable case classes, small traits and functions.

- Single responsibility. The compile-time [`Env`](../core/src/main/scala/com/databricks/cql/env/Env.scala)
  carries pure inherited attributes (scope, parameters, terminology handles) and no session (`ARCH-ENV`,
  `ARCH-PURITY`). `SqlEvalEnv` carries what lowering binds to by name: retrieve functions, the value-set
  table, the UDF schema.
- Open/closed. A new data model or terminology source is a new binding behind a seam, not an edit to the
  passes (`ARCH-SEAMS`). A new operator is a new IR case and its lowering, behind a conformance test
  (`PROC-TDD`).
- Liskov substitution. Any retrieve binding with the `(__subject_id, __resource)` contract can stand in
  for another. The conformance data and a production source are interchangeable, which is what makes the
  differential meaningful.
- Interface segregation. The seam traits are small. `DataProvider` has one method, `retrieve`.
  `TerminologyProvider` has one, `expand`.
- Dependency inversion. The core depends on those abstractions, never on a storage layout or a
  terminology service. Dependencies are passed in explicitly, not discovered through a global registry,
  so a test can wire in-memory adapters (`CONF-GROUND-TRUTH`).
