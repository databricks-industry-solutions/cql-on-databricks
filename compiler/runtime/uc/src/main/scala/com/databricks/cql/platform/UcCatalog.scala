package com.databricks.cql.platform

/** `SVC-RUNTIME-UDF-UC` (docs/deploy.mda) — the Databricks-side generator that turns the
  * engine's platform-neutral UC manifest (`EXEC-UC-MANIFEST`, `com.databricks.cql.runtime.udf.UcFunctionManifest`)
  * into the `CREATE OR REPLACE FUNCTION … LANGUAGE SCALA` DDL that registers the opaque `cql_*` catalog as
  * **persistent Unity Catalog Scala UDFs**. Registered once, the functions resolve on every compute type —
  * serverless notebooks/jobs, SQL warehouses, Spark Declarative Pipelines, classic DBR 18.2+ — lifting the
  * "serverless is excluded" limit of the classic `%scala` path (`SVC-RUNTIME-UDF`).
  *
  * All Databricks/UC concern lives HERE, never in the engine (`APP-DECOUPLE`, `ARCH-PURITY`): the engine
  * publishes only the neutral spec (SQL name, handler `object.method`, arg/return SQL types, determinism);
  * this repo supplies the DDL text, the fat-jar Volume path (`java_dependencies`), the `environment_version`,
  * and the `catalog`.`schema` the functions live in. It re-implements no CQL semantics (`ACC-ENGINE-CLIENT`)
  * — the handlers are the engine's `CqlOps` methods named by the manifest.
  *
  * The generated shape (per compiler/docs/execution-model.md, spike-validated on Environment Version 4 =
  * Scala 2.13.16 / JDK 17):
  *
  * {{{
  * CREATE OR REPLACE FUNCTION `cat`.`sch`.`cql_age_at`(a0 STRING, a1 STRING)
  * RETURNS INT
  * LANGUAGE SCALA
  * DETERMINISTIC
  * ENVIRONMENT (
  *   java_dependencies = '["/Volumes/cat/sch/runtime/udf/runtime-<ver>.jar"]',
  *   environment_version = '4')
  * HANDLER 'com.databricks.cql.runtime.eval.CqlOps.ageAt';
  * }}}
  *
  * `CREATE OR REPLACE` makes redeploys idempotent. Parameter names are positional placeholders (`a0..aN`) —
  * UC binds handler arguments by SQL type, not name (doc section 2). Identifiers are backtick-quoted so a
  * reserved schema name (the default UC schema is literally `default`) is always legal. */
object UcCatalog {

  /** Environment Version 4 ⇒ Scala 2.13.16 + JDK 17 (matches `TECH-TOOLCHAIN`). */
  private val EnvironmentVersion = "4"

  /** One `CREATE OR REPLACE FUNCTION` statement per catalog function, fully qualified into
    * `catalog`.`schema`, with the engine fat jar at `jarVolumePath` on the `java_dependencies` path.
    *
    * @param catalog       UC catalog the functions are created in.
    * @param schema        UC schema (the emitted SQL is qualified to `catalog.schema` by the compiler, or
    *                      this schema is placed on the session search path, so bare `cql_*` names resolve).
    * @param jarVolumePath absolute `/Volumes/…` path of the uploaded `runtime` fat jar.
    */
  def statements(catalog: String, schema: String, jarVolumePath: String): Seq[String] =
    // Fully qualified rather than imported so the engine FQN appears at exactly one site (guarded by
    // UcCatalogSpec against an engine package rename, ACC-NAMESPACE-ALIGN) — mirrors CqlDatabricks.init.
    com.databricks.cql.runtime.udf.UcFunctionManifest.entries.map { spec =>
      val params = spec.argTypes.zipWithIndex.map { case (t, i) => s"a$i $t" }.mkString(", ")
      val determinism = if (spec.deterministic) "DETERMINISTIC" else "NOT DETERMINISTIC"
      s"""CREATE OR REPLACE FUNCTION `$catalog`.`$schema`.`${spec.sqlName}`($params)
         |RETURNS ${spec.returnType}
         |LANGUAGE SCALA
         |$determinism
         |ENVIRONMENT (
         |  java_dependencies = '["$jarVolumePath"]',
         |  environment_version = '$EnvironmentVersion')
         |HANDLER '${spec.handler}';""".stripMargin
    }
}
