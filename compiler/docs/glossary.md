# Glossary

Terms the compiler docs use. Not normative.

## Standards

- CQL: Clinical Quality Language (HL7). Authored upstream. The compiler never re-authors it.
- ELM: Expression Logical Model, the machine-readable form of CQL (JSON here). The compiler's input.
- FHIR: Fast Healthcare Interoperability Resources (HL7). The data model the compiler supports, R4.
- dQM: Digital Quality Measure, a FHIR `Measure` plus CQL.
- eCQM: Electronic Clinical Quality Measure (CMS).
- VSAC: Value Set Authority Center, a source of value-set expansions.
- UCUM: Unified Code for Units of Measure, the unit system for CQL quantities.
- MeasureReport: the FHIR resource for measure results.

## CQL

- Context: the grouping scope (`Patient`, `Practitioner`, `Unfiltered`). The compiler treats it as a
  grouping key, not a per-patient loop.
- define (`ExpressionDef`): a named CQL expression, and a node in the define graph. Each lowers to one SQL
  statement.
- Retrieve: data access (`[Encounter]`, `[Condition: "Diabetes"]`). Lowers to a `retrieve_<Type>` call.
- 3VL: three-valued logic. A boolean is `true`, `false` or `null`.
- `cqlTrue`: true only when the value is exactly `true`. `not cqlTrue(x)` covers `false` and `null`.
- Equality and equivalence: `=` and `~`, which differ around nulls and precision. List `in` and
  `distinct` use equality.
- Interval: a typed range with open or closed bounds.
- Allen relations: interval operators such as `during`, `overlaps`, `meets`, `before`, `after`.
- Precision: temporal granularity (year, month, day, time). A comparison can be null when precision is
  too coarse to decide.
- Quantity: a `(value, unit)` pair. Arithmetic needs UCUM conversion.
- Choice type: `Choice<T1, T2, …>`, a value of one of several types, as in FHIR `value[x]`.
- Value set: a named set of codes. Membership is the most common measure operation.
- Query: sources, `with`/`without`, `where`, `return`, `sort`, `aggregate`.
- `with` / `without`: a semi-join and an anti-join with a 3VL `such that`.

## Compiler

- IR: the intermediate representation, a sealed ADT that mirrors ELM.
- Pass: a step that transforms the IR (Resolve, Normalize, Optimize) or turns it into SQL (Lower).
- Lowering: generating Spark SQL from the IR.
- Emitted SQL: the output script, one `CREATE OR REPLACE TEMP VIEW` or `CACHE TABLE` per define.
- Grain: the natural shape of a result. A scalar is one row per subject; a list is rows per subject with
  `__ordinal`.
- Materialization barrier: a `CACHE TABLE` statement that cuts the plan.
- Pushdown: moving a filter to the scan so rows are pruned early.
- Subject: the entity being measured, keyed by `__subject_id`.
- Context frame: the per-subject base relation, built once and read by every define.
- Define graph: the dependency graph of defines, sorted to order the statements.
- Sidecar: the per-subject `(__subject_id, __is_null)` relation that tells a null list from an empty one.
- Determinism: the same input gives the same output.
