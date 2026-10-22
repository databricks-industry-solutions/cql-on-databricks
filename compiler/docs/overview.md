# Overview

What the compiler does, what it doesn't do, and the rules every other doc assumes.

## What it is

The compiler takes authored CQL (quality measures, phenotypes, clinical rules) and compiles it to a Spark SQL
script. Nobody re-implements the clinical logic by hand.

It evaluates set-at-a-time. The unit of work is one CQL expression evaluated across the whole population,
grouped by the CQL context key (Patient, Practitioner and so on). A per-patient engine walks the ELM tree
once per patient. This compiler walks the define graph once and lowers each define to SQL over all
subjects.

## Trust model

The authored CQL and its ELM are the source of truth. They are never re-implemented in SQL by an engineer.

Trust comes from matching results on conformance tests, not from running the reference interpreter. The
differential reference is the Java CQF engine (`org.cqframework:engine-jvm`), run in-process in the tests.
See [testing-and-conformance.md](testing-and-conformance.md).

## Scope

In scope:

- Batch population evaluation of authored CQL, through ELM.
- The FHIR R4 data model. The model sits behind a seam so others can be added.
- Patient, Practitioner and Unfiltered contexts.
- CQL semantics: three-valued logic, ordered lists, intervals and temporal precision, UCUM quantities,
  terminology membership, Choice types.
- Spark SQL as the only target.

Out of scope:

- Low-latency single-patient decision support. Single-patient evaluation works but isn't optimized.
- Writing a CQL parser. The compiler uses the cqframework `cql-to-elm` translator in-process
  (`TECH-ELM-INPUT`).
- Other SQL dialects or execution backends.
- QDM and other models. The seams exist; the bindings don't.
- Building `MeasureReport` or QRDA output. The compiler emits results in the CQL data model plus the data
  needed to trace them (`DATA-EXPORT`, [data-format.md](data-format.md) section 7).

## Rules

1. Faithfulness over speed (`CORE-FAITHFULNESS`). A fast wrong answer is a failure.
2. No silent mis-lowering (`CORE-NO-SILENT-MISLOWER`). Unsupported constructs fail at compile time.
3. Determinism (`CORE-DETERMINISM`). Same ELM, same data and same compiler version give the same output.
4. Isolation (`CORE-ISOLATE-ANOMALY`). One patient's bad data never fails the batch. CQL-defined runtime
   conditions become per-row results, as the spec says.

## Where things are

The reading order is in [the compiler README](../README.md). Requirement IDs are in
[requirements.md](requirements.md). Requirements that span features are in
[docs/requirements.md](../../docs/requirements.md), and [docs/systems.md](../../docs/systems.md) maps the
features. Terms are defined in [glossary.md](glossary.md).

Requirement IDs are `<TYPE>-<SLUG>` (for example `LANG-3VL`, `ARCH-PURITY`, `CONF-GATES`). Cite them in
code and tests (`PROC-CITE-REQ`).
