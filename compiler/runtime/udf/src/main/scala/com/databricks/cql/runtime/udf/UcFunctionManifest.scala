package com.databricks.cql.runtime.udf

/** One catalog function's UC-deployment contract — everything a host needs to emit a
  * `CREATE FUNCTION … LANGUAGE SCALA … USING JAR` statement, and nothing platform-specific. Pure data:
  * the SQL function name, the fully-qualified Scala handler (`object.method`), the ordered argument SQL
  * types, the return SQL type, and whether the function is deterministic. No Databricks/UC type appears
  * here — the DDL text, the Volume path, and the catalog/schema are the host's to supply (`ARCH-PURITY`). */
final case class UcFunctionSpec(
  sqlName: String,
  handler: String,
  argTypes: Seq[String],
  returnType: String,
  deterministic: Boolean = true)

/** EXEC-UC-MANIFEST (compiler/docs/execution-model.md) — the platform-NEUTRAL description of the opaque UDF catalog
  * as **Unity Catalog Scala UDFs**. A downstream host (e.g. CQL on Databricks) consumes this to
  * generate the `CREATE FUNCTION … LANGUAGE SCALA … ENVIRONMENT(java_dependencies=…, environment_version='4')
  * HANDLER '<fqn>'` DDL, upload the fat jar to a UC Volume, and place the functions on the SQL search path
  * so the emitted bare `cql_*` names resolve (`EXEC-UC-NAME-RESOLVE`). The engine itself emits NO DDL and
  * carries NO platform concept; it only publishes this contract.
  *
  * ==Scope (`EXEC-UC-STRUCT-ARG`, spike-validated)==
  * A serverless spike established the handler-type contract: a SQL `STRUCT` binds via a case class whose
  * schema EXACTLY matches (incl. `DECIMAL(38,18)`) — NOT `org.apache.spark.sql.Row`; `MAP` binds only via
  * `scala.collection.immutable.Map`; `ARRAY` via `scala.collection.Seq`; `BINARY` via `Array[Byte]`; and a
  * `STRUCT` RETURN via a case class. Every catalog UDF is listed here: the 6 pure-scalar temporal operators,
  * `cql_to_interval` (scalar in, `CqlInterval` case-class return), and `cql_convert_quantity` (the
  * `CqlQuantity` case-class Quantity param + `immutable.Map` factors + case-class return).
  *
  * There is NO `cql_choice_nav_*` here: heterogeneous-Choice navigation lowers NATIVELY (demand-projected
  * top-level slots per union branch, `EMIT-SQL-FLAT-CHOICE`) — Photon-eligible and never emitted as a UDF.
  *
  * The `sqlName`s are exactly the emitted `cql_*` names (cross-checked against [[CqlRuntimeUdfs.catalog]]),
  * and each `handler` resolves to a real method on `…runtime.eval.CqlOps`. */
object UcFunctionManifest {

  /** The handler object whose methods back the catalog (the single `CqlOps` object). */
  private val HandlerObject = "com.databricks.cql.runtime.eval.CqlOps"
  private def h(method: String): String = s"$HandlerObject.$method"

  /** The UC-ready catalog entries (spike-validated signatures). */
  val entries: Seq[UcFunctionSpec] = Seq(
    UcFunctionSpec("cql_temporal_cmp",       h("temporalCmp"),      Seq("STRING", "INT", "STRING", "INT", "STRING"), "BOOLEAN"),
    UcFunctionSpec("cql_temporal_same_prec", h("temporalSamePrec"), Seq("STRING", "STRING", "INT"),                  "BOOLEAN"),
    UcFunctionSpec("cql_temporal_step",      h("temporalStep"),     Seq("STRING", "BOOLEAN"),                        "STRING"),
    UcFunctionSpec("cql_date_add",           h("dateAdd"),          Seq("STRING", "INT", "STRING"),                  "STRING"),
    UcFunctionSpec("cql_age_at",             h("ageAt"),            Seq("STRING", "STRING"),                         "INT"),
    UcFunctionSpec("cql_temporal_boundary",  h("temporalBoundary"), Seq("STRING", "INT", "BOOLEAN"),                 "STRING"),
    UcFunctionSpec("cql_to_interval",        h("toInterval"),       Seq("STRING", "STRING"),                         "STRUCT<low: STRING, high: STRING>"),
    UcFunctionSpec("cql_convert_quantity",   h("convertQuantity"),
      Seq("STRUCT<value: DECIMAL(38,18), unit: STRING>", "MAP<STRING, DECIMAL(38,18)>", "STRING"),
      "STRUCT<value: DECIMAL(38,18), unit: STRING>"),
    // General QA aid: schema conformance of any VARIANT payload (e.g. the retrieve hint/context envelope)
    // against a model DDL. Both args are STRING (the actual is `schema_of_variant(v)` —
    // UC-bindable, no raw VARIANT/Spark type in the sandbox handler, no JSON round trip); returns BOOLEAN.
    UcFunctionSpec("cql_variant_valid",      h("variantValid"),     Seq("STRING", "STRING"),                         "BOOLEAN"))

  /** Minimal, dependency-free JSON serialization (this module deliberately carries no JSON library). A JSON
    * array of objects: `[{"sqlName":…,"handler":…,"argTypes":[…],"returnType":…,"deterministic":…}, …]`. */
  def toJson: String = {
    def esc(s: String): String = s.flatMap {
      case '"'  => "\\\""
      case '\\' => "\\\\"
      case c    => c.toString
    }
    def q(s: String): String = "\"" + esc(s) + "\""
    def arr(ss: Seq[String]): String = ss.map(q).mkString("[", ",", "]")
    entries.map { e =>
      s"""{"sqlName":${q(e.sqlName)},"handler":${q(e.handler)},"argTypes":${arr(e.argTypes)},""" +
        s""""returnType":${q(e.returnType)},"deterministic":${e.deterministic}}"""
    }.mkString("[", ",", "]")
  }
}
