# Engine design

The compiler is a multi-pass optimizer over a typed intermediate representation (IR). It emits a Spark SQL
script.

## 1. Set-at-a-time evaluation

Each CQL define is evaluated across the whole subject set at once (`ARCH-SET-EVAL`), grouped by context
(`Patient`, `Practitioner` and so on). The engine steps over defines, and within each define treats
subjects as rows.

Each define keeps its natural grain (`ARCH-GRAIN`). A scalar is one value per subject. A list is rows at
the element grain (`__subject_id` + `__ordinal`), not an array. Same-grain consumers then filter and
project instead of joining and exploding.

## 2. Passes

```
ELM JSON
→ Load: lift into the sealed-ADT IR
→ Resolve: build the define graph, reject reference cycles
→ Normalize: inline functions, bind parameters, resolve codes and concepts, inline let-queries
→ Optimize: retrieve pushdown (terminology, date range, Period range)
→ Lower: one SQL statement per define; native SQL first, opaque UDF where needed
→ Schedule: order the define graph, pick materialization cut points, assemble the script
→ SQL script
```

Load (ELM to IR) and Lower (IR to SQL) are the only steps that cross a representation. The rest are IR to
IR. Code: `passes/Pipeline.scala`, `passes/Optimize.scala`, `transpiler/lower/`, `passes/Schedule.scala`.

The result type of every node is computed on demand (`TypeEncoding.typeOf`, `ARCH-TYPE-ATTR`), not
stored, so it can't go stale after a rewrite.

## 3. IR and rules

The IR is a sealed ADT that mirrors the ELM node set (`ir/Ir.scala`). Traversal is `transformUp` and
`transformDown` over partial functions (`ir/TreeNode.scala`): a rule names only the cases it rewrites.

Rewrites are grouped into batches run to a fixpoint with an iteration cap (`rules.RuleExecutor`).

## 4. Environment

State flows down the tree as an immutable `Env` (`env/Env.scala`, `ARCH-ENV`), never through mutable
fields or globals. It carries the scope chain, the current context, parameter bindings, terminology
handles and the engine configuration (`TECH-CONFIG`).

## 5. Purity

Lowering performs no Spark action, no I/O and no shared mutation. Schema comes from types and the static
FHIR model, never from a running Spark session (`ARCH-NO-RUNTIME-SCHEMA`).

Each retrieve and the context frame are built once and referenced by name (`ARCH-SCAN-SHARE`). Each define
is its own named statement (`CREATE OR REPLACE TEMP VIEW`, or `CACHE TABLE` at a materialization
barrier), referenced by name, not inlined.

## 6. Conditionals

`if C then A else B` lowers to:

```
(A where cqlTrue(C))  UNION ALL  (B where not cqlTrue(C))
```

Each branch is lowered on its own and projected into the shared output schema. Every subject lands in
exactly one branch (`ARCH-CONDITIONAL`, `ARCH-COND-UNION`).

## 7. Retrieve pushdown

The Optimize pass collects predicates from every consumer of a retrieve and pushes the non-lossy ones to
the scan (`ARCH-PUSHDOWN`): value-set membership, literal date ranges and Period ranges
(`rules.TerminologyPushdown`, `DateRangePushdown`, `PeriodRangePushdown`). The precise filters stay
downstream, so the pushed filter only has to be a superset. The whole-measure differential runs the
optimized path against the Java reference, which catches a wrong pushdown (`CONF-GATE-MEASURE`).

## 8. Robustness

1. Sealed ADTs force every node to be handled. An unsupported node raises `UnsupportedNode` at compile
   time, never a plausible wrong plan (`CORE-NO-SILENT-MISLOWER`).
2. CQL semantics (3VL, intervals, quantities) live in a semantic library tested on its own
   (`TECH-SEMANTIC-LIB`).
3. Verifier checkpoints run between passes (`CONF-VERIFIER`): `WellFormedLibrary` after Load,
   `DefineGraph.assertAcyclic` in Resolve, `OnlyKernelNodes` and `WellTyped` after Normalize.
4. Compile-time failures fail the whole measure. CQL-defined runtime conditions become per-row results.
5. No dependence on hash-map order, the wall clock or shuffle order. `__ordinal` keeps lists
   reproducible (`TECH-DETERMINISM`).

## 9. Extension points

- New data model: a new retrieve binding and model binding (`ARCH-SEAMS`, [data-model.md](data-model.md)).
- New terminology source: a new value-set table. The compiler doesn't change.
- New optimization: a rule added to a batch.
- New operator: an IR node, its lowering and a conformance test.

Spark SQL is the only lowering target (`ARCH-EXECUTE-NOT-TRANSPILE`).
