# Vendored cqframework CQL operator test suite (`CONF-GATE-OPERATOR`)

These XML files are vendored **verbatim** from the cqframework CQL conformance test suite and used
as the per-operator corpus of the operator gate.

- **Source:** https://github.com/cqframework/cql-tests — `tests/cql/`
- **Pinned commit:** `06667083335f645e46e6f88bbd3b4d8d232574df` (the single source of truth for the vendored snapshot)
- **License:** Apache License 2.0 (the upstream repo's `LICENSE`). These files are redistributed
  unmodified under that license.

## Files vendored in this slice

Aligned to operator families the engine already implements + gates elsewhere:

| File | Operators |
|------|-----------|
| `CqlLogicalOperatorsTest.xml` | `and` / `or` / `xor` / `not` / `implies` (3VL) |
| `CqlNullologicalOperatorsTest.xml` | `Coalesce` / `IsNull` / `IsTrue` / `IsFalse` |
| `CqlComparisonOperatorsTest.xml` | `=` `!=` `~` `<` `<=` `>` `>=` |
| `CqlArithmeticFunctionsTest.xml` | `+` `-` `*` `/` `div` `mod` `^` and the math functions |
| `CqlAggregateFunctionsTest.xml` | `Count` `Sum` `Min` `Max` `Avg` `Median` `Mode` `Variance` `StdDev` `PopulationVariance` `PopulationStdDev` `Product` `AllTrue` `AnyTrue` (`LANG-AGGREGATE-FUNCTIONS`) |

## How they are consumed

`OperatorTests` (test code) DOM-parses each file into cases; each `<test>` becomes a `define` over its
`<expression>`, paired with a `define` over the declared `<output>`. The cases run through the SAME
engine path as every other fixture (PROC-DRY):

- `OperatorTranslationSpec` (no Spark) — every included case must normalize to kernel IR, or fail
  *only* on a node kind documented in `OperatorTests.UnsupportedNodes`.
- `OperatorConformanceSpec` (Spark Connect) — supported cases are executed and diffed against both
  the Java CQF reference and the declared `<output>`.

Excluded from this slice (tracked, not silent): cases marked `invalid` (expected-error — a deferred
negative path), cases needing the `system.long` / `tuple` capabilities, and cases needing an
unsupported ELM node (the `OperatorTests.UnsupportedNodes` frontier).

**Not yet vendored — the other 11 of the upstream suite's 16 files:** `CqlIntervalOperatorsTest`,
`CqlListOperatorsTest`, `CqlStringOperatorsTest`, `CqlDateTimeOperatorsTest`,
`CqlAggregateTest`, `CqlConditionalOperatorsTest`,
`CqlTypeOperatorsTest`, `CqlTypesTest`, `CqlQueryTests`, `ValueLiteralsAndSelectors`,
`CqlErrorsAndMessagingOperatorsTest`. Extending the slice is the cheapest remaining conformance
coverage win (doc/testing-and-conformance.md, Gate 1 coverage frontier).

To update: re-fetch the five files at a new upstream commit, update the **Pinned commit** above, then
run `OperatorTranslationSpec` and reconcile any new node kinds.
