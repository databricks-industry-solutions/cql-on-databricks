package com.databricks.cql.transpiler.emit

/** Object-name conventions for the emitted SQL (EMIT-SQL-EXECUTE). A define's CQL name can carry spaces
  * and punctuation ("Denominator Exclusions"); a backtick-quoted session view/`CACHE TABLE` tolerates
  * that, but we still SANITIZE every emitted object name to `[A-Za-z0-9_]`, spaces/punctuation collapsing
  * to `_` — the same convention the synthetic join/hoist columns already use. One plain-identifier
  * namespace keeps the by-name reference DAG and the client name contract stable and portable across any
  * host catalog.
  *
  * OBJECT names ONLY. A define's name does NOT appear in its result SCHEMA: every define publishes its
  * payload under the uniform `SystemColumns.Val` (`EMIT-SQL-VALUE-COLUMN`), so a consumer reads
  * `SELECT __val FROM <obj(name)>` and never re-derives [[sanitize]] to find a column. The define name is
  * still used as a JOIN-LOCAL column label when a frame reads several dependency defines at once (each
  * dependency's `__val` is renamed to its object name inside the join subquery) — that is an internal
  * frame namespace, not a published one.
  *
  * ONE source of truth so the transpiler (definition AND every by-name reference) and every consumer (the
  * collect harness, the client) agree on the object a define maps to. The mapping is FORWARD only
  * (define name -> object name); it is intentionally not inverted anywhere (`sanitize` is not injective).
  *
  * SCOPE: this is the SQL-EMIT object namespace ONLY. The compiler proto path's materialize-seam keys
  * (`def_`/`cse_`/`ctx_`, `Config.materializeBoundaries`) are a SEPARATE, recorded contract over
  * RAW define names and are deliberately left untouched. */
object SqlNames {

  /** Sanitize a CQL define name to a plain SQL identifier. */
  def sanitize(name: String): String = name.replaceAll("[^A-Za-z0-9]", "_")

  /** EXEC-OPAQUE-UDF: the reserved `FunctionRef.libraryName` marker the het-union-nav rewrite tags an
    * opaque catalog-UDF call with, so `SqlColumnLowering` lowers it to a raw `Call(<udf name>, …)`
    * instead of treating it as a CQL/FHIRHelpers function. Not a real CQL library — an emit-internal tag. */
  val UdfMarkerLib = "__cqldatabricks_udf"

  /** EMIT-SQL-TEMPORAL-UDF-OPAQUE (opaque): the opaque JVM catalog-UDF names the temporal-operator collapse emits
    * (`Config.opaqueTemporalUdf`), each rendered to a raw `Call(name, …)` in `SqlColumnLowering`. These
    * MUST match the catalog keys in
    * [[com.databricks.cql.runtime.udf.CqlRuntimeUdfs.catalog]] (the emit<->UDF wire contract —
    * duplicated by value, since the runtime-udf module cannot import the transpiler). */
  val TemporalCmp      = "cql_temporal_cmp"      // precision-aware compare (during/overlaps/before/after/same as)
  val TemporalSamePrec = "cql_temporal_same_prec" // same as at an explicit precision
  val TemporalStep     = "cql_temporal_step"     // successor/predecessor + open-bound close
  val DateAdd          = "cql_date_add"          // Date/DateTime ± calendar Quantity
  val AgeAt            = "cql_age_at"             // CalculateAge (Year)
  val TemporalBoundary = "cql_temporal_boundary" // Low/HighBoundary
  val ToInterval       = "cql_to_interval"       // FHIRHelpers.ToInterval
  val ConvertQuantity  = "cql_convert_quantity"  // ELM ConvertQuantity, dynamic (runtime) source unit

  /** EMIT-UDF-QUALIFY: qualify a `cql_*` UDF name with the caller's schema prefix (`udfSchema`).
    * Each dot-separated segment of the schema is backtick-quoted when needed (matching
    * [[SqlRender.quoteIdent]]); the function name itself (a simple `cql_*` identifier) is left
    * unquoted. An already-qualified name (contains `.`) is returned unchanged (defensive). When
    * `schema` is `None` or empty, the bare name is returned (backward-compatible). */
  def qualifyUdf(schema: Option[String], fn: String): String = schema match {
    case Some(s) if s.nonEmpty && !fn.contains('.') && fn.startsWith("cql_") =>
      (s.split('.').map(SqlRender.quoteIdent) :+ fn).mkString(".")
    case _ => fn
  }

  /** EMIT-SQL-OBJECT-PREFIX: apply the session-object namespace. An EMPTY prefix (the default) returns
    * the bare name unchanged, so every existing emit is byte-identical; a non-empty one yields
    * `<prefix>__<name>`. The double underscore matches [[datasetName]]'s separator and keeps a prefixed
    * name distinguishable from a define that merely contains an underscore. */
  def qualify(name: String, prefix: String): String =
    if (prefix.isEmpty) name else sanitize(prefix) + "__" + name

  /** A scalar define's view/table object name. The value column it projects is the uniform
    * `SystemColumns.Val` (`EMIT-SQL-VALUE-COLUMN`), so a consumer reads `SELECT __val FROM <obj(name)>`. */
  def obj(name: String, prefix: String = ""): String = qualify(sanitize(name), prefix)

  /** A grain-first Relation define's view/table object name — the same sanitized identifier as a
    * scalar define (`obj`); CQL define names are unique within a library so the two kinds never
    * collide (EMIT-SQL-DROP-REL-PREFIX). */
  def rel(name: String, prefix: String = ""): String = qualify(sanitize(name), prefix)

  /** A define's per-subject nullity sidecar view (`null_<name>`, ARCH-LIST-NULL-SIDECAR). */
  def nullView(name: String, prefix: String = ""): String = qualify("null_" + sanitize(name), prefix)

  /** PIPE-SQL-DAG-REUSE — the PUBLISHED dataset name for a MATERIALIZED define: the single source of truth
    * for the cross-dataset DAG cut on the SQL back end. It is used for BOTH ends of the cut — the name a
    * dependent recipe reads a materialized SIBLING by (`FROM <dataset>`), AND the pipeline's
    * `@dp.materialized_view(name=…)` / `CREATE OR REFRESH MATERIALIZED VIEW <name>` the recipe publishes
    * under — so a consumer never re-derives it (one contract, in the compiler).
    *
    * The `{library}_{version}[_{param_hash}]__{define}` scheme keeps the published namespace:
    *   1. DISJOINT from the internal per-statement object names (`sanitize`, `null_`)
    *      because the library+version prefix is never empty.
    *   2. UNIQUE across multi-measure pipelines (different libraries produce different prefixes).
    *   3. PARAMETER-AWARE: the same measure compiled with different parameters (e.g. different measurement
    *      periods) produces different datasets, disambiguated by `param_hash` (first 8 of SHA-256).
    *
    * The published dataset carries the SAME payload its internal object does — a scalar's
    * `(__subject_id, __val)` or a relation's grain-first `(__subject_id, __ordinal, __val)`
    * (`EMIT-SQL-VALUE-COLUMN`) — so the encoding a consumer relies on is preserved across the cut and the
    * published schema does not depend on which lowering path claimed the define. */
  def datasetName(name: String, ctx: DatasetNameContext): String = ctx.owners.get(name) match {
    // EMIT-ROOT-INDEPENDENT: a define is named by the library that declares it, never the one being compiled.
    case Some(owner) => DatasetNameContext(owner.library, owner.version, ctx.paramHash).prefix + "__" +
      sanitize(owner.name).toLowerCase
    case None => ctx.prefix + "__" + sanitize(name).toLowerCase
  }

  /** The naming context that qualifies a published dataset name: library identity + optional parameter
    * hash (`PIPE-EMIT-SQL`). Built once per `compilePipeline` call, reused for every define in the plan.
    * `owners` (EMIT-ROOT-INDEPENDENT) names each merged define by the library that declares it.
    * Carried through [[com.databricks.cql.transpiler.lower.SqlEvalEnv]] so the lowering-time
    * `FROM <sibling>` cut uses the same names the recipe publishes under. */
  final case class DatasetNameContext(library: String, version: String, paramHash: String = "",
                                      owners: Map[String, com.databricks.cql.ir.StatementOwner] = Map.empty) {
    /** The fully-qualified prefix: `{library}_{version}[_{param_hash}]`, all lowercased+sanitized. */
    val prefix: String = {
      val lib = sanitize(library).toLowerCase
      val ver = sanitize(version).toLowerCase
      if (paramHash.nonEmpty) s"${lib}_${ver}_$paramHash" else s"${lib}_$ver"
    }
  }

  /** Compute the first 8 hex chars of the SHA-256 of a canonical parameter string. Empty when
    * the parameter map is empty (no hash segment in the dataset name). */
  def paramHash(parameters: Map[String, String]): String =
    if (parameters.isEmpty) ""
    else {
      val canonical = parameters.toSeq.sortBy(_._1).map { case (k, v) => s"$k=$v" }.mkString(";")
      val digest = java.security.MessageDigest.getInstance("SHA-256").digest(canonical.getBytes("UTF-8"))
      digest.take(4).map("%02x".format(_)).mkString // 4 bytes = 8 hex chars
    }
}
