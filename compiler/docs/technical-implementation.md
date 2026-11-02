# Technical implementation

Technology choices. Requirement IDs here are `TECH-*`.

## 1. Scala — `TECH-SCALA`

The compiler is Scala. It emits SQL and never touches data, so the language affects maintainability, not
query speed. Sealed traits, pattern matching and exhaustiveness checks fit a faithful walk over ELM. The
front end (cqframework `cql-to-elm`) and the ELM model are JVM libraries, so a JVM compiler needs no
language bridge.

### 1.1 Toolchain — `TECH-TOOLCHAIN`

Versions are set in `gradle.properties`.

| Component | Version | Notes |
|---|---|---|
| Apache Spark | 4.1.2 | `compileOnly`. Matches Databricks Runtime 18 |
| Scala | 2.13.16 | Spark 4.1 has no Scala 2.12 build |
| JDK | 17 | Bytecode target 17 |
| cqframework | 4.8.0 | Through the `cql-bom` |
| Build | Gradle | One multi-project build (`settings.gradle`) |

## 2. CQL is the input — `TECH-ELM-INPUT`

The public input is CQL. The compiler bundles cqframework `cql-to-elm` and translates in-process: CQL to
ELM JSON to the IR. ELM is a build artifact, never committed. The conformance tests translate CQL once
and give the same ELM to the compiler and to the reference engine.

## 3. Reuse or rewrite — `TECH-REUSE`

| Component | Decision |
|---|---|
| CQL to ELM translator | Reuse cqframework, in-process |
| ELM model | Reuse as the input contract |
| Conformance tests | Reuse: the Java CQF engine and published test decks are the ground truth |
| Operator execution | Rewrite, set-at-a-time in SQL |
| Temporal and interval algebra | Rewrite: native SQL plus opaque JVM UDFs |
| Value-set expansion | Not done. Expansions are read from a table (`DATA-TERMINOLOGY-ADAPTER`) |

## 4. Semantic library — `TECH-SEMANTIC-LIB`

CQL operator semantics live in one place. Native SQL built-ins are used where they are enough, which keeps
the script constant-foldable and pushdown-friendly. Repeated pure-scalar operators that built-ins can't
express (temporal precision, UCUM, interval conversion) lower to an opaque JVM UDF (`EXEC-UDF-FIRST`).
Their semantics live once in `compiler/runtime/udf` (`CqlOps`) and the SQL calls them by name.

## 5. Lowering target — `TECH-LOWERING-TARGET`

The compiler lowers to a Spark SQL script, as text, over opaque UDFs. One statement per define in
dependency order, rendered by `SqlRender` and run with `spark.sql`. The text can be read without a live
session. One target, Spark SQL, means one dialect.

## 6. Spark mapping — `TECH-SPARK-MAP`

| Need | Spark mechanism |
|---|---|
| Value-set membership | Semi-join against the value-set table |
| Date-window pushdown | A literal date range on the scan |
| Context grouping | `GROUP BY __subject_id` |
| Hard scalar semantics | Native built-ins, or an opaque JVM UDF (`EXEC-UDF-FIRST`) |
| Rewrite rules | A fixpoint batch runner (`rules.RuleExecutor`) |

## 7. Determinism — `TECH-DETERMINISM`

No reliance on hash-map order, the wall clock or shuffle order. `__ordinal` keeps lists reproducible. The
define graph is sorted with ties broken by name (`passes.Schedule`).

## 8. Configuration — `TECH-CONFIG`

Configuration is an immutable `Config` (`env/Config.scala`), resolved once from the request's `overrides`
and frozen into the `Env`. Nothing reads mutable global or session state. That keeps lowering pure and
safe to run concurrently.

| Key | Default | Effect |
|---|---|---|
| `optimize.retrieve_pushdown` | `true` | Retrieve predicate pushdown. `false` runs the query as written |
| `materialize.mode` | `costly` | `costly`, `all` or `none` ([data-format.md](data-format.md) section 6) |
| `materialize.skip_unfiltered_retrieves` | `true` | Treat bare retrieves as views |
| `materialize.retrieves` | `none` | Shared filtered reads as their own datasets: `none`, `costly`, `all` (`PIPE-READ-DATASET`) |
| `materialize.retrieve_min_reuse` | `2` | Call sites a read needs under `costly` |
| `materialize.boundaries` | cost-chosen | An explicit set of defines to materialize |
| `emit.object_prefix` | empty | Prefix for emitted session object names (`EMIT-SQL-OBJECT-PREFIX`) |
| `lower.grain_first` | `false` | Keep list defines at relation grain |
| `optimize.conditional_union` | `false` | Lower list-valued conditionals as `UNION ALL` without boxing |
| `verifiers.enabled` | `true` | Run the verifier checkpoints between passes |

`Config.scala` lists the rest (`lower.*`, `bind.gap_lenient`, `debug.dump_dir`).

The conformance tests write per-fixture artifacts (`cql`, `elm.json`, `ir.txt`, `lowered.txt`,
`explain.txt`) to `compiler/conformance/build/dump/<fixture>/` (`DEBUG-PLAN-DUMP`).
`-Dcqldatabricks.dumpDir=<dir>` moves them.
