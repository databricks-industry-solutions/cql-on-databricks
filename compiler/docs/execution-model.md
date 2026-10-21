# Execution model

How the optimized IR becomes Spark SQL, and how that SQL runs. Covers the `EXEC-*` and `EVAL-*`
requirements.

## The script

The compiler emits a Spark SQL script (`EXEC-SQL-BACKEND`): one statement per define, in dependency order.
Most defines are `CREATE OR REPLACE TEMP VIEW`. Materialization barriers are `CACHE TABLE`. SQL-body
helpers are `CREATE OR REPLACE TEMPORARY FUNCTION` (`transpiler/emit/SqlRender.scala`). The client runs it
with `spark.sql`.

Every define is evaluated set-at-a-time across the population, keyed by `__subject_id`.

The script is text, so it can be read and reviewed (`EXEC-PLAN-VISIBILITY`). It also keeps the compiler
off the execution classpath: the compiler never needs a Spark session (`ARCH-PURITY`).

## Two layers — `EVAL-TWO-LAYER`

Each define lowers into two layers woven into one plan:

- The data-access layer: relational structure (`FROM`, `JOIN`, `WHERE`, `GROUP BY`, `EXISTS`).
- The calc layer: CQL scalar semantics (3VL, precision-aware temporals, intervals, quantities).

Calc results flow back into relational operators, so pushdown still works.

Data-access layer:

- `EVAL-RETRIEVE-RELATIONAL`: a retrieve lowers to a relational subplan over the retrieve function.
- `EVAL-VS-PUSHDOWN`: value-set membership is a semi-join at the retrieve.
- `EVAL-JOIN-PUSHDOWN`: `with` and `without` lower to semi- and anti-joins with a 3VL `such that`.
- `EVAL-STATUS-RELATIONAL`: status and profile helpers inline to relational predicates.

Calc layer:

- `EVAL-OP-GRANULARITY`: a fixed, versioned catalog of operators. No measure-specific UDFs.
- `EVAL-3VL-VIA-SQL`: logical and null operators lower to native Spark SQL, whose Kleene logic matches
  CQL.
- `EVAL-NATIVE-MARSHAL`: operators take and return native Spark types (structs, ISO strings, arrays).
- `EVAL-COMPOSITE-IN-PLAN`: tuples and lists are built with native constructors such as `named_struct`.
  No UDF returns a composite.
- `EVAL-UNION-DEFER`: a heterogeneous `union` becomes `UNION ALL` of homogeneous arms, with the
  operator after it pushed into each arm.
- `EVAL-HOF-CONTROL`: control flow lowers to `CASE` and Spark higher-order functions (`aggregate`,
  `transform`, `filter`).
- `EVAL-CHOICE-NARROW`, `EMIT-SQL-FLAT-CHOICE`: Choice navigation lowers natively, to demand-pruned
  top-level slots per union arm. There is no Choice UDF.

## Native first, opaque where it explodes — `EXEC-NATIVE-FIRST`, `EXEC-OPAQUE-UDF`

Some CQL constructs (partial-precision temporals, `toInterval`, unit conversion) expand into deep
`CASE`/`struct` trees that make Catalyst analysis expensive. SQL-body functions don't help, because
Catalyst inlines them. An opaque JVM UDF is a black box to the analyzer, so one call replaces the tree.

So the compiler lowers to native SQL where it is compact and relational (3VL, same-precision comparison,
value-set semi-joins), and to an opaque UDF where the native form would explode. Repeated pure-scalar
operators default to the UDF (`EXEC-UDF-FIRST`, `EMIT-SQL-TEMPORAL-UDF-OPAQUE`).

Native SQL stays for: relational and set operations, filters that must push down, constant-foldable
literals, values with no fixed return type (built in-plan with `named_struct`) and towers already behind
a barrier.

A composite UDF that folds a recurring composition is allowed only when each part is also callable on its
own, the composite measurably beats the parts, and it is named for the pattern (`EXEC-UDF-COARSE`).

## The UDF catalog — `EXEC-UDF-LIBRARY`, `EXEC-UDF-CATALOG`

`compiler/runtime/udf` is a versioned JAR with a fixed catalog of opaque UDFs, registered by name and
declared deterministic. The compiler never generates a UDF. At each call site it picks the catalog entry
for the resolved type.

The catalog (`CqlRuntimeUdfs.catalog`): `cql_temporal_cmp`, `cql_temporal_same_prec`,
`cql_temporal_step`, `cql_date_add`, `cql_age_at`, `cql_temporal_boundary`, `cql_to_interval`,
`cql_convert_quantity`, `cql_variant_valid`.

CQL semantics live once in `runtime.eval.CqlOps` (`EVAL-OPS-CORE`). The UDFs are thin wrappers over it.
Expensive registries (UCUM, time zones) are built once per executor JVM (`EXEC-UDF-REGISTRY-CACHE`,
`EVAL-GLOBAL-PRELOAD`).

The same functions are registered two ways:

- With `spark.udf.register` after `spark.addArtifact`, for local Spark Connect and the conformance suite
  (`CqlRuntimeUdfs.install`).
- As Unity Catalog Scala UDFs, by `compiler/runtime/uc` from the manifest in `UcFunctionManifest`
  (`EXEC-UC-MANIFEST`).

## Temporal units in data — `EXEC-SQL-TEMPORAL-RUNTIME-UNIT`

Most `date ± Quantity` has a unit known at compile time. FHIR `Age` carries its unit in the data
(`Age.code`), so the unit is a column. The opaque-UDF path handles that. The native `make_interval` path
needs the unit at compile time, which is a known gap.

## Other requirements

- `EXEC-CANON-TYPE`, `EXEC-DEMAND-TYPING`: temporals, quantities and intervals are normalized once,
  above the retrieve, as structs of primitives, and only for the fields a measure computes on.
- `EXEC-PATIENT-PARTITION`: the plan partitions by `__subject_id`, so a patient's resources are
  colocated.
- `EXEC-CQF-LEVERAGE`: the CQF project supplies the ELM front end and the ground-truth oracle.
- `EXEC-SELFDIFF`: UDF and native lowering must give identical results. A difference is a bug.

## Known trade-offs

- A patient with very many resources is one large row.
- Struct equality and ordering need canonical units and code order.
- Semi-join versus nested-loop choices need a cardinality signal the compiler doesn't have.

## Not done

- Per-patient evaluation inside a UDF or `mapPartitions`.
- Spark UDTs for normalized types.
- SQL-body functions for hard semantics, because Catalyst inlines them.
- Generating UDFs per compilation.
