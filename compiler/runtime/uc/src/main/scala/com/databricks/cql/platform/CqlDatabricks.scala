package com.databricks.cql.platform

import org.apache.spark.sql.SparkSession

/** CQL on Databricks notebook session entry point (`SVC-RUNTIME-UDF`, docs/deploy.mda).
  *
  * `CqlDatabricks.init(spark)` prepares the current Spark session to run compiled CQL: today it registers the
  * engine's opaque `cql_*` UDF catalog (`EXEC-UDF-LIBRARY`) so emitted SQL resolves the temporal /
  * choice-navigation / interval operators by name. It is the ONE line a notebook author runs from a
  * `%scala` cell:
  *
  * {{{
  * %scala
  * com.databricks.cql.platform.CqlDatabricks.init(spark)
  * }}}
  *
  * ==Why a `%scala` cell (shared / USER_ISOLATION clusters)==
  * On a shared cluster the Python `cql()` client is a Spark Connect session with no py4j gateway, so it
  * cannot register JVM UDFs itself. A `%scala` cell runs in the driver JVM and shares the notebook's
  * session, so registering here makes `cql_*` resolvable for the Python / SQL cells that follow. On a
  * single-user classic session `cql-databricks` registers automatically over py4j and this call is
  * redundant (but harmless).
  *
  * ==Why a CQL on Databricks entry point (not the engine's)==
  * This deliberately WRAPS the engine catalog behind a stable `CqlDatabricks` name so notebooks are insulated from
  * the engine's package rename (`ACC-NAMESPACE-ALIGN`) and so CQL on Databricks has a home for session setup the
  * engine doesn't own (platform-only UDFs, session confs) — added inside `init` without changing the cell
  * anyone has memorized. It re-implements no CQL semantics (`ACC-ENGINE-CLIENT`); it only delegates.
  *
  * Idempotent: the engine's `registerAll` re-registers names harmlessly, so calling `init` once per
  * notebook is safe. */
object CqlDatabricks {

  /** Prepare `spark` to run compiled CQL. Registers the engine's opaque `cql_*` UDF catalog on the
    * session. Safe to call more than once. */
  def init(spark: SparkSession): Unit = {
    // The engine's pre-built UDF catalog (com.databricks.cql.runtime.udf.CqlRuntimeUdfs). Fully
    // qualified rather than imported so this single delegation site is the one place the engine FQN
    // appears — the CqlDatabricksSpec guard asserts it stays resolvable across engine changes.
    com.databricks.cql.runtime.udf.CqlRuntimeUdfs.registerAll(spark)
    // Further session setup (platform-only UDFs, confs) lands here — the `init` cell does not change.
  }

  /** Register the opaque `cql_*` catalog as PERSISTENT Unity Catalog Scala UDFs (`SVC-RUNTIME-UDF-UC`) by
    * running the `CREATE OR REPLACE FUNCTION … LANGUAGE SCALA` DDL from `UcCatalog`. Unlike [[init]] (a
    * per-session `%scala` register that only reaches JVM-backed sessions), this is a ONE-TIME deploy step:
    * once the functions exist in `catalog`.`schema`, emitted SQL resolves them on EVERY compute type,
    * including serverless. Runs from any LANGUAGE-SCALA-capable context (serverless notebook/job/SQL
    * warehouse, DBR 18.2+); the generated DDL is the same regardless. `CREATE OR REPLACE` ⇒ idempotent.
    *
    * @param jarVolumePath absolute `/Volumes/…` path of the uploaded `runtime` fat jar. */
  def registerUcCatalog(spark: SparkSession, catalog: String, schema: String, jarVolumePath: String): Unit =
    UcCatalog.statements(catalog, schema, jarVolumePath).foreach(spark.sql)
}
