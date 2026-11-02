package com.databricks.cql.runtime.udf

import org.apache.spark.sql.{functions => F, SparkSession}
import org.apache.spark.sql.expressions.UserDefinedFunction

import com.databricks.cql.runtime.eval.{CqlOps, CqlQuantity}

/** EXEC-UDF-LIBRARY / EXEC-UDF-CATALOG (compiler/docs/execution-model.md) — the FIXED,
  * pre-built catalog of opaque JVM UDFs the emitted SQL references by name (EXEC-OPAQUE-UDF). Catalyst
  * treats each as a black box: it does not recurse into it, so the analyzed plan cannot bloat THROUGH
  * it. The compiler never GENERATES a UDF; per call site it SELECTS the catalog variant whose return
  * type matches the resolved ELM type (compile-time polymorphism over a closed set).
  *
  * ==Spark exposure only — semantics live in `CqlOps` (EVAL-OPS-CORE)==
  * This object is the `…runtime.udf` layer of `EVAL-OPS-CORE` (compiler/docs/execution-model.md): every
  * `val` below is a thin [[UserDefinedFunction]] wrapper that DELEGATES into
  * [[com.databricks.cql.runtime.eval.CqlOps]] — the shared semantic core. Nothing here
  * re-implements CQL meaning; `CqlOps` is the single source of truth (also the caller a future coarse
  * `cql_define` evaluator would share). Correctness of the ports is gated Spark-free by `TemporalOpsSpec`
  * and end-to-end by the corpus SQL differential.
  *
  * Heterogeneous-Choice navigation is NOT in this catalog: it lowers NATIVELY (demand-projected top-level
  * slots per union branch, `EMIT-SQL-FLAT-CHOICE`) — Photon-eligible and bounded in analysis — so no
  * `cql_choice_nav_*` UDF exists.
  *
  * ==No-capture recipe (the hard rules the spike, UdfFeasibilitySpec, established)==
  *  1. Every UDF is an object-held `val` (a [[UserDefinedFunction]] built by `functions.udf`), so the
  *     lambda's synthetic class lives in THIS object — never eta-expanded at a registration call site
  *     (which would capture the caller and drag its deps onto the server).
  *  2. This object depends only on Spark (already on the server) + scala-library + `CqlOps`/
  *     [[RegistryCaches]] — all in THIS module (`runtime`), which imports NOTHING from the
  *     compiler. A lambda that calls `CqlOps.temporalCmp(...)` references that same-jar singleton, so no
  *     compiler class ever rides a closure onto the server.
  *  3. Ship the compiled classes to the server ONCE per session (`spark.addArtifact`, OSS/local) — see
  *     [[install]]. Then register by name (`spark.udf.register`, [[registerAll]]).
  *
  * ==Unity Catalog deployment — sandbox type contract (planned second backend, spike-validated)==
  * These are UDFs, and this component (`runtime`) stays their home. Besides the OSS
  * `spark.udf.register` backend above, the catalog is intended to ALSO deploy as **Unity Catalog Scala
  * UDFs** (`CREATE FUNCTION … LANGUAGE SCALA … USING JAR … HANDLER 'com.databricks.cql.runtime.eval.CqlOps.<method>'`),
  * which now run on all compute types incl. serverless (<https://docs.databricks.com/gcp/en/udf/scala-java-uc>).
  * Design + spike results: `compiler/docs/execution-model.md`.
  *
  * A UC Scala UDF handler runs in a sandbox with **no Spark session / no Spark APIs**, and handler
  * selection is by a **Spark SQL encoder over the EXACT Scala parameter type**. The data-type "language
  * mappings" table (<https://docs.databricks.com/gcp/en/sql/language-manual/sql-ref-datatypes>) lists
  * `STRUCT → org.apache.spark.sql.Row`, but that is the DataFrame type system, NOT UC-UDF input binding —
  * a serverless spike (`EXEC-UC-STRUCT-ARG`) established the accepted handler types:
  *   scalars → `Boolean`/`Int`/`Long`/`Double` (or boxed `java.lang.*` for nullable); DECIMAL(p,s) →
  *   `java.math.BigDecimal` (encodes to `DECIMAL(38,18)` — the SQL type must match that precision);
  *   STRING → `String`; BINARY → `Array[Byte]`; ARRAY → `scala.collection.Seq`; MAP →
  *   **`scala.collection.immutable.Map`** (the general `scala.collection.Map` and `java.util.Map` are
  *   REJECTED); STRUCT **param OR return** → a Scala **case class whose schema EXACTLY matches** (NOT
  *   `org.apache.spark.sql.Row`).
  * CONSEQUENCE for this catalog: EVERY entry is now a UC handler as-is. The scalar temporal ops
  * (`temporalCmp`/`temporalSamePrec`/`temporalStep`/`dateAdd`/`ageAt`/`temporalBoundary`) and `toInterval`
  * (scalar in, `CqlInterval` case-class RETURN) need nothing; `convertQuantity` takes the `CqlQuantity` case
  * class (`STRUCT<value: DECIMAL(38,18), unit: STRING>`) + `scala.collection.immutable.Map` factors + a
  * case-class RETURN. `CqlOps` references no `org.apache.spark.*` type at all (choice navigation is native,
  * so no `Row`-nav op exists here). The UC DDL, the fat-jar-to-Volume upload, and name resolution are the
  * downstream host's job (`ARCH-PURITY`); the
  * engine exposes the handler methods + a neutral function manifest (`EXEC-UC-MANIFEST`). */
object CqlRuntimeUdfs {

  /** EXEC-UDF-LIBRARY version pin (TECH-ELM-INPUT discipline): the catalog is pinned to the compiler
    * version that emits calls into it. Read from the build-injected resource; `"unknown"` if absent
    * (e.g. loaded from loose classes in a test). */
  val Version: String = {
    val in = getClass.getResourceAsStream("/runtime.version")
    if (in == null) "unknown"
    else try scala.io.Source.fromInputStream(in, "UTF-8").mkString.trim
    finally in.close()
  }

  // ------------------------------------------ the temporal-operator catalog (EMIT-SQL-TEMPORAL-UDF-OPAQUE opaque)
  // Each collapses a repeated CQL TEMPORAL OPERATOR (the ~530 CASE/named_struct towers per measure statement)
  // to ONE opaque JVM call whose body is the matching `CqlOps` function. Scalar params
  // (inf/precLen/rank/minus/high/op/unit) are always compile-time literals; the count `n` of cql_date_add
  // may be a runtime column (boxed Integer, null -> null).

  val temporalCmp: UserDefinedFunction =
    F.udf((a: String, aInf: Int, b: String, bInf: Int, op: String) => CqlOps.temporalCmp(a, aInf, b, bInf, op))

  val temporalSamePrec: UserDefinedFunction =
    F.udf((a: String, b: String, precLen: Int) => CqlOps.temporalSamePrec(a, b, precLen))

  val temporalStep: UserDefinedFunction =
    F.udf((s: String, minus: Boolean) => CqlOps.temporalStep(s, minus))

  val dateAdd: UserDefinedFunction =
    F.udf((s: String, n: java.lang.Integer, unit: String) => CqlOps.dateAdd(s, n, unit))

  val ageAt: UserDefinedFunction =
    F.udf((birth: String, asOf: String) => CqlOps.ageAt(birth, asOf))

  val temporalBoundary: UserDefinedFunction =
    F.udf((s: String, targetRank: Int, high: Boolean) => CqlOps.temporalBoundary(s, targetRank, high))

  val toInterval: UserDefinedFunction =
    F.udf((start: String, end: String) => CqlOps.toInterval(start, end))

  val convertQuantity: UserDefinedFunction =
    F.udf((q: CqlQuantity, factors: Map[String, java.math.BigDecimal], target: String) =>
      CqlOps.convertQuantity(q, factors, target))

  // ------------------------------------------------------- variant schema-validation (general QA aid)
  // A JSON-FREE, UC-safe conformance check that a VARIANT payload matches an expected model schema — a
  // general aid usable on any VARIANT (e.g. the retrieve UDTF's `parameters` map). The
  // SQL arg is the built-in `schema_of_variant(v)` (a STRING — no raw VARIANT/Spark type crosses into the
  // sandbox-safe handler, and no `to_json` round trip), compared against the model DDL. Usage:
  // `cql_variant_valid(schema_of_variant(v), '<expectedDdl>')`.
  val variantValid: UserDefinedFunction =
    F.udf((actualSchemaDdl: String, expectedDdl: String) => CqlOps.variantValid(actualSchemaDdl, expectedDdl))

  /** The registry the emit side selects from — SQL name -> catalog UDF. The names are the exact
    * function names the compiler emits (`SqlChoiceNav` / the temporal-operator emit), so registration and
    * emission share one source. */
  val catalog: Map[String, UserDefinedFunction] = Map(
    "cql_temporal_cmp"        -> temporalCmp,
    "cql_temporal_same_prec"  -> temporalSamePrec,
    "cql_temporal_step"       -> temporalStep,
    "cql_date_add"            -> dateAdd,
    "cql_age_at"              -> ageAt,
    "cql_temporal_boundary"   -> temporalBoundary,
    "cql_to_interval"         -> toInterval,
    "cql_convert_quantity"    -> convertQuantity,
    "cql_variant_valid"       -> variantValid)

  // ---------------------------------------------------------------------------- registration backends

  /** Register every catalog UDF by name on `spark` (`spark.udf.register`), so emitted SQL can call it.
    * Registers the pre-built `val`s directly — NEVER `method _` (no call-site eta-expansion, per the
    * no-capture recipe). Idempotent: re-registering the same name is harmless. */
  def registerAll(spark: SparkSession): Unit =
    catalog.foreach { case (name, udf) => spark.udf.register(name, udf) }

  /** The OSS/local Connect install path (EXEC-UDF-LIBRARY backend 2): ship this JAR's classes to the
    * server, then register the catalog. Wired ONCE into the session seam (never per query). On
    * Databricks the JAR is instead a UC-governed `CREATE FUNCTION … USING JAR` / pre-installed library. */
  def install(spark: SparkSession, jarPath: String): Unit = {
    spark.addArtifact(jarPath)
    registerAll(spark)
  }
}
