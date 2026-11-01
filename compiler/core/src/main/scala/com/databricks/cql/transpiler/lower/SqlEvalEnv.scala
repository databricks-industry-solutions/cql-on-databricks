package com.databricks.cql.transpiler.lower

import com.databricks.cql.env.{Config, Context, RetrieveRelation, SourceBinding}
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.transpiler.emit.{SqlExpr, SqlNames, SqlRel}

/** The SQL-target evaluation environment (EMIT-SQL-EXECUTE): the Spark-FREE twin of the compiler's
  * `lower.EvalEnv`. It carries the SAME injected shape — the active context, the frozen [[Config]], and
  * the pure-IR materialization-policy inputs (`reuseCounts`/`skipUnfiltered`, threaded from
  * `passes.Materialize`) — but NONE of the effectful Spark surface: no `SparkSession`, no `DataProvider`,
  * no `Materializer`, no `TerminologyProvider` adapter. The SQL back end emits TEXT, so those seams
  * collapse into NAMES the runtime has mounted.
  *
  * THE RETRIEVE SEAM ([[retrieveMount]], BIND-RETRIEVE-UDF). Where the compiler's
  * `EvalEnv.requireData(...).retrieve(rt)` returns a `DataFrame` for `[Encounter]`, the SQL target returns
  * a [[SqlRel.TableFunc]] CALL `[catalog.]retrieve_<Type>(library, value_set, code_property, codes,
  * date_property, date_low, date_high, parameters)` to a client-supplied SQL UDTF — the SQL analog of a
  * `DataProvider` handing back a frame. That function returns the identical
  * `(__subject_id, __resource)` contract (`SystemColumns`, DATA-RETRIEVE-VIEW): the runtime
  * (client/conformance) registers one such UDTF per resource type BEFORE running the script, so the
  * transpiler never constructs a scan — it names the function (from the caller's binding table,
  * BIND-MANIFEST) and feeds it eight EXPLICIT literal arguments it may prune on
  * ([[SqlEvalEnv.retrieveParams]], BIND-EXPLICIT-PARAMS / BIND-PUSHDOWN). A SQL UDTF inlines in Catalyst,
  * so pruning/sharing stay native; the module stays Spark-free (ARCH-PURITY). (The earlier bare
  * `retrieve_<Type>` VIEW mount was RETIRED — the UDTF is now the sole mount contract. The opaque
  * `(context VARIANT, hints VARIANT)` envelope pair was retired in favour of the explicit parameters,
  * 2026-08.)
  *
  * THE TERMINOLOGY SEAM ([[valueSetMount]], BIND-TERMINOLOGY-TABLE). Every `TerminologyProvider.expand(vsId)`
  * (a DataFrame of `(system, code)`) lands in ONE mounted `valuesets` table keyed by value-set id; a
  * membership selects its set with `WHERE valueset_id = '<vsId>'` and renders as a semi-join against that
  * ([[SqlQueryLowering.membershipFilter]]). `terminology = false` fails a membership loudly
  * (CORE-NO-SILENT-MISLOWER), mirroring `EvalEnv.terminology.getOrElse(throw …)`. */
final case class SqlEvalEnv(
    /** The active CQL context; decides the base relation's grouping (LANG-CONTEXT). */
    context: Context,
    /** Frozen engine configuration (TECH-CONFIG) — the SAME `Config` the proto path reads, so
      * `grainFirst`, the materialize mode, and the boundary set all carry over unchanged. */
    config: Config = Config.defaults,
    /** Whether value-set expansion views are mounted (the SQL analog of an injected
      * `TerminologyProvider`). `false` → a membership test fails loudly. */
    terminology: Boolean = false,
    /** The per-name REUSE (refcount) map (DATA-MATERIALIZE-POLICY), threaded from
      * `passes.Materialize.reuseCounts` — the reuse factor the `costly` policy multiplies a candidate's
      * cost by. Consumed by [[SqlMaterialization]] to pick a define's statement kind. Empty → reuse 1. */
    reuseCounts: Map[String, Int] = Map.empty,
    /** The skip-unfiltered define-name set (DATA-SKIP-UNFILTERED) — bare retrieves that are ALWAYS a
      * plain view, never a barrier. Threaded from `passes.Materialize.skipUnfilteredNames`. */
    skipUnfiltered: Set[String] = Set.empty,
    /** ARCH-CTE-DEF: the define currently being lowered, set per-define by [[SqlFrameLowering]] so a
      * local CSE cut inside its query can be named in that define's namespace. `None` outside a define. */
    currentDefine: Option[String] = None,
    /** EMIT-ROOT-INDEPENDENT: each define's parameters, keyed by its own library's names
      * ([[com.databricks.cql.passes.ParameterScope]]), set by [[SqlFrameLowering]]. A retrieve's `parameters`
      * argument carries only the current define's, so a parameter another library declares never changes it. */
    defineParameters: Map[String, Seq[(String, com.databricks.cql.ir.ParameterDef)]] = Map.empty,
    /** ARCH-RESOURCE-HANDLE: the demand (navigated fields / whole-body need) per heterogeneous-`Choice`
      * list define, from `passes.ChoiceDemand.analyze` (the SAME pure pass the compiler uses — Spark-free).
      * The SQL het-union encoding is the demand-pruned HANDLE BY DEFAULT (the locked decision): a
      * resource member slot carries `{__resource_type, __resource_id, <demand fields>}`, never the whole
      * body (the boundary renders a resource by identity anyway, `JsonNormalize.isResourceStruct`). */
    choiceDemand: Map[String, com.databricks.cql.passes.ChoiceDemand.Demand] = Map.empty,
    /** EMIT-SQL-RETRIEVE-DEMAND-BOUND: the projection demand per FHIR resource TYPE, from
      * `passes.RetrieveDemand.analyze` (the SAME pure Spark-free pass). A plain retrieve's scanned
      * `__resource` is pruned to `Narrow(fields)` — identity lineage + only the top-level fields
      * navigated anywhere in the linked library (+ scan-filter fields) — instead of the whole modeled
      * body; `Full` (a whole-body escape / het-Choice arm) keeps the whole struct (routed to the VARIANT
      * carrier in a later phase). Empty → no pruning (every retrieve keeps the full body). */
    retrieveDemand: Map[String, com.databricks.cql.passes.RetrieveDemand.Demand] = Map.empty,
    /** `DATA-RETRIEVE-BARRIER-OPT`: the FILTERED reads this lowering materialized as their own objects,
      * mapped from every [[com.databricks.cql.ir.Retrieve]] node that reads them to the object
      * name. Selected by `passes.RetrieveReads.selected` (empty under the default
      * `materialize.retrieves = none`, which keeps the script byte-identical).
      *
      * Several distinct nodes map to ONE name when they differ only in a local value-set alias — the read
      * identity is the resolved descriptor, not the node. Consulted at the single retrieve seam
      * ([[SqlQueryLowering.filteredRetrieve]]), which returns a bare read of the object instead of
      * re-inlining the mount call and its predicates. The object carries the SAME
      * `(__subject_id, __resource)` shape the inlined form produces, so every consumer is unaffected. */
    materializedReads: Map[com.databricks.cql.ir.Retrieve, String] = Map.empty,
    /** BIND-CAPABILITY-OVERLAP: the customer binding's AVAILABLE capability per FHIR resource TYPE — the
      * top-level physical field names its typed-subset struct declares (read at BIND time from the mount's
      * schema, never a per-row `.schema` probe; `ARCH-NO-RUNTIME-SCHEMA`). A type ABSENT from the map has
      * no declared subset → the full model is available → no gap check (the inert default). When present,
      * a demanded field NOT in the set is a coverage gap: a loud bind-time error (CORE-NO-SILENT-MISLOWER)
      * unless `Config.bindingGapLenient`. */
    available: Map[String, Set[String]] = Map.empty,
    /** ARCH-SQL-CSE: the WITHIN-DEFINE hoist-column namer ([[SqlHoist]]). Reset to a fresh counter per
      * define by [[SqlFrameLowering]] so the `__h<N>` names a define's duplication-prone temporal/interval
      * operands hoist to are UNIQUE across every (possibly nested) projection level of that ONE define — a
      * hoisted select propagates its prior `__h<N>` through `*`, so an outer level must never reuse a
      * number. Fresh per define keeps the numbers small/readable (and golden-stable under sibling
      * reordering); shared by every `hoisted` call within the define via the copied env reference. */
    hoistCounter: java.util.concurrent.atomic.AtomicInteger = new java.util.concurrent.atomic.AtomicInteger(0),
    /** PIPE-SQL-DAG-REUSE — the cross-dataset DAG-cut boundary set. When `Some`, a REFERENCE to a define
      * in this set resolves to a bare read of its PUBLISHED dataset ([[SqlNames.datasetName]]) instead of
      * its local object — so a dependent recipe reads a materialized SIBLING as a declared
      * dataset (`spark.table(cql_<sibling>)`) rather than re-inlining it. This is the SQL-back-end analog
      * of the retired proto's `EvalEnv.referenceDefines`.
      *
      * It is a MUTABLE set (mirroring the mutable `hoistCounter`) that [[SqlFrameLowering]] GROWS as it
      * materializes each define: because `Schedule` lowers a define AFTER its dependencies, by the time a
      * consumer resolves a reference to a materialized define the set already contains it — so ONE lowering
      * pass both discovers the boundaries and cuts the references (no two-pass, no external boundary input).
      * `None` (the default, off the pipeline path) makes every resolver inert → the interactive `compileSql`
      * script is byte-identical (a strict default-inert change). It affects only how a define is
      * REFERENCED, never whether it is emitted — every materialized define still lowers its own body into
      * its own recipe. */
    cutSiblings: Option[scala.collection.Set[String]] = None,
    /** LANG-QUERY-WHERE over a define DAG: the per-subject SCALAR define names already lowered when THIS
      * define is lowered (grown by [[SqlFrameLowering]] as it steps defines in dependency order, like
      * `cutSiblings`). A RELATION query whose clause references one of these (e.g.
      * `… same day as "Most Recent Reading Day"`) LEFT-JOINs that define's per-subject view onto its
      * combined frame and resolves the reference to its value column ([[SqlQueryLowering.combined]]). Empty
      * outside a define / on the Array path — a ref there still fails loudly (CORE-NO-SILENT-MISLOWER). */
    scalarDefs: scala.collection.Set[String] = Set.empty,
    /** BIND-MANIFEST: the caller-supplied retrieve binding table — a per-FHIR-type map to the SQL UDTF
      * ([[SourceBinding]]) that retrieves it, passed as DATA over the compile API. Consulted ONLY in UDTF
      * mount mode ([[retrieveSource]], `Config.retrieveUdtf`); a type ABSENT from the map falls back to the
      * convention `retrieve_<Type>` with no catalog. Empty (the default) → every type is convention-bound.
      * The compiler reads no catalog to discover a function (`ARCH-PURITY`). */
    sources: Map[String, SourceBinding] = Map.empty,
    /** PIPE-EMIT-SQL — the naming context for published dataset names (`{library}_{version}[_{param_hash}]__
      * {define}`). Threaded from the compile request's library identity and parameter overrides. `None`
      * (the default, off the pipeline path) → `datasetName` is never called (the interactive `compileSql`
      * path has no cut and emits no published names). */
    datasetNameCtx: Option[SqlNames.DatasetNameContext] = None,
    /** BIND-TERMINOLOGY-TABLE: optional fully-qualified table name for the value-set expansion mount
      * (e.g. `"catalog.schema.valuesets"`). When `Some`, the emitted SQL references `FROM <fqn>` instead
      * of bare `FROM valuesets`, so a pipeline whose target schema differs from the terminology schema
      * resolves correctly. When `None` (the default), the bare `"valuesets"` convention is preserved. */
    terminologyTable: Option[String] = None,
    /** RUN-RESULTS-BY-EVAL (`DEC-EVAL-ID-LOOKUP`): the evaluation set this pipeline-cut compile writes. A
      * published sibling holds every evaluation set's rows keyed by `__eval_id`, so when this is set a read
      * of one ([[relRef]], [[scalarRef]], [[readRef]]) keeps only this evaluation's rows and drops the key.
      * `None` (the default) reads the published name bare, as before. Ignored off the cut path. */
    publishedEvalId: Option[String] = None,
    /** BIND-PROVIDER-REQUEST: each retrieve id ([[RetrieveCall.id]]) to the relation the client's data provider
      * returned for it. `Some` binds every retrieve per id, and a retrieve with no relation fails; `None`
      * keeps the per-type [[sources]] binding. */
    data: Option[Map[String, RetrieveRelation]] = None,
    /** BIND-PROVIDER-REQUEST: the value-set relations in precedence order, a set read from the first that
      * holds it. `Some` replaces [[terminologyTable]] and [[terminology]]: an empty list has no terminology. */
    terminologyRelations: Option[Seq[RetrieveRelation]] = None,
    /** BIND-PROVIDER-REQUEST: every retrieve this lowering made, by id, in first-seen order, for `describe`.
      * Shared by every copy of the env, like [[hoistCounter]]. */
    retrieveCalls: scala.collection.mutable.LinkedHashMap[String, RetrieveCall] =
      scala.collection.mutable.LinkedHashMap.empty
) {

  /** The published dataset name for define `n`, using the current [[datasetNameCtx]]. Falls back to a
    * bare `sanitize(n)` when the context is absent (should only be reached in cut mode, where the context
    * is always set). */
  private def publishedName(n: String): String = datasetNameCtx match {
    case Some(ctx) => SqlNames.datasetName(n, ctx)
    case None      => SqlNames.sanitize(n) // defensive; cut mode always has a context
  }

  /** PIPE-SQL-DAG-REUSE — the object name a REFERENCE to grain-first Relation define `n` reads: its
    * published dataset when `n` is a cut sibling, else its local view. The consuming define
    * keeps `n`'s list-metadata (distinct flag) from the SAME lowering pass, so the grain-first
    * `(__subject_id, __ordinal, __val)` encoding is preserved across the cut —
    * only the physical name it reads from changes. */
  def relRefName(n: String): String =
    if (cutSiblings.exists(_.contains(n))) publishedName(n) else SqlNames.rel(n, config.objectPrefix)

  /** PIPE-SQL-DAG-REUSE — the object name a REFERENCE to SCALAR define `n` reads: its published dataset
    * when `n` is a cut sibling, else its local `obj(n)` view. The VALUE COLUMN is `SystemColumns.Val`
    * either way (the published dataset carries the same `(__subject_id, __val)` payload,
    * `EMIT-SQL-VALUE-COLUMN`), so only the `FROM`-relation the join reads from is redirected. */
  def scalarRefName(n: String): String =
    if (cutSiblings.exists(_.contains(n))) publishedName(n) else SqlNames.obj(n, config.objectPrefix)

  /** `DATA-RETRIEVE-BARRIER-OPT` — the object name a reference to materialized READ `obj` reads: its
    * PUBLISHED dataset on the pipeline path, its local object otherwise. Unlike `relRefName`/`scalarRefName`
    * this does not test SET MEMBERSHIP: a read reaches this method only when it was selected, and every
    * selected read is emitted (always materialized, always before any define), so being in pipeline-cut
    * mode at all is the whole condition. */
  def readRefName(obj: String): String =
    if (cutSiblings.isDefined) publishedName(obj) else obj

  /** The RELATION a reference to Relation define `n` reads ([[relRefName]]), scoped to [[publishedEvalId]]
    * when it is a published sibling. Every reference site goes through these three, so no site can read
    * another evaluation set's rows. */
  def relRef(n: String): SqlRel = scoped(relRefName(n), n, cutSiblings.exists(_.contains(n)))

  /** The relation a reference to scalar define `n` reads ([[scalarRefName]]), scoped like [[relRef]]. */
  def scalarRef(n: String): SqlRel = scoped(scalarRefName(n), n, cutSiblings.exists(_.contains(n)))

  /** The relation a reference to materialized read `obj` reads ([[readRefName]]), scoped like [[relRef]]. */
  def readRef(obj: String): SqlRel = scoped(readRefName(obj), obj, cutSiblings.isDefined)

  /** `name` bare, or — for a published dataset under [[publishedEvalId]] — `(SELECT * EXCEPT (__eval_id)
    * FROM name WHERE __eval_id = '<id>') AS __scoped_<key>`. The alias must NOT be the published name: the
    * evaluator relocates every occurrence of that name to its three-part results address
    * (`PIPE-CROSS-SCHEMA-QUALIFY`), and a three-part alias does not parse. Every caller wraps the result in a
    * relation of its own, so nothing qualifies a column by this alias. */
  private def scoped(name: String, key: String, published: Boolean): SqlRel = publishedEvalId match {
    case Some(id) if published =>
      val eval = com.databricks.cql.spark.SystemColumns.EvalId
      SqlRel.AliasRel(SqlRel.Select(
        projections = Seq(SqlRel.Proj(SqlExpr.starExcept(Seq(eval)))),
        from = SqlRel.TableRef(name),
        where = Some(SqlExpr.Infix("=", SqlExpr.Col(eval), SqlExpr.str(id)))),
        s"__scoped_${SqlNames.sanitize(key)}")
    case _ => SqlRel.TableRef(name)
  }

  /** The retrieve MOUNT for one retrieve (BIND-RELATION, BIND-PROVIDER-REQUEST): the relation the request
    * binds to the retrieve's id. A table or view is read as `SELECT __subject_id, __resource FROM <name>`, so
    * a column beyond the contract never reaches a join; a function is called with the request fields it
    * declares, by name (`BIND-NAMED-ARGS`). Without `data`, the per-type [[sources]] binding is called with
    * the seven positional arguments. The retrieve's predicates still re-apply ABOVE the relation
    * (BIND-CAPABILITY), so a relation that ignores its request is a correct un-pruned superset. A SQL table
    * function inlines in Catalyst like a view; the compiler names the relation and never bodies it
    * (`ARCH-PURITY`). Every call is recorded for `describe` ([[retrieveCalls]]). */
  def retrieveMount(call: RetrieveCall): SqlRel = {
    retrieveCalls.getOrElseUpdate(call.id, call)
    data match {
      case Some(bound) => bound.get(call.id) match {
        case Some(RetrieveRelation.Table(name, _)) =>
          SqlRel.Select(
            projections = Seq(SqlRel.Proj(SqlExpr.Col(com.databricks.cql.spark.SystemColumns.SubjectId)),
                              SqlRel.Proj(SqlExpr.Col(com.databricks.cql.spark.SystemColumns.Resource))),
            from = SqlRel.TableRef(name))
        case Some(RetrieveRelation.Function(name, parameters, _)) =>
          // Each dotted segment is quoted on its own (`SqlRender.qualifiedName`).
          val dot = name.lastIndexOf('.')
          SqlRel.TableFunc(name.substring(dot + 1), call.namedArgs(parameters),
            if (dot < 0) None else Some(name.substring(0, dot)))
        case None => throw UnsupportedNode("Retrieve",
          s"retrieve ${call.id} [${call.fhirType}] has no relation in the request's data (BIND-PROVIDER-REQUEST)")
      }
      case None =>
        val (fn, catalog) = retrieveSource(call.fhirType)
        SqlRel.TableFunc(fn, call.positionalArgs, catalog)
    }
  }

  /** BIND-MANIFEST: resolve a FHIR resource type to its bound UDTF `(function, catalog)` — the caller's
    * [[sources]] entry, else the convention `retrieve_<rt>` in the default schema (no catalog). The one
    * home for name/catalog resolution; the compiler reads no catalog (`ARCH-PURITY`). */
  def retrieveSource(rt: String): (String, Option[String]) =
    sources.get(rt).map(b => (b.function, b.catalog)).getOrElse((retrieveMountName(rt), None))

  /** The mounted-view NAME for a retrieve (`retrieve_<rt>`), for callers that need the identifier
    * (the retrieve-share barrier / a `spark.table`-style reference). */
  def retrieveMountName(rt: String): String = s"retrieve_$rt"

  /** The value-set expansion MOUNT for `vsId` (BIND-TERMINOLOGY-TABLE): a `(system, code)` relation
    * selected out of the SINGLE mounted `valuesets` expansion table, keyed by the canonical value-set id
    * — `SELECT system, code FROM valuesets WHERE valueset_id = '<vsId>'`. ALL value sets share one
    * physical mount (the way a real terminology export stores expansions — one long table
    * keyed by set, not one table per set), so the runtime binds ONE table instead of N. The SQL analog of
    * `TerminologyProvider.expand`; fails loudly when no terminology is mounted. */
  def valueSetMount(vsId: String): SqlRel = terminologyRelations match {
    case Some(layers) => layeredValueSet(vsId, layers)
    case None =>
      if (!terminology) throw UnsupportedNode("InValueSet",
        s"value-set membership on '$vsId' needs a mounted terminology expansion (SqlEvalEnv.terminology=false)")
      else valueSetFrom(vsId, valueSetsMountName)
  }

  /** `SELECT system, code FROM <table> WHERE valueset_id = '<vsId>'`. */
  private def valueSetFrom(vsId: String, table: String, extra: Seq[SqlRel.Proj] = Nil): SqlRel =
    SqlRel.Select(
      projections = Seq(SqlRel.Proj(SqlExpr.Col("system"), "system"),
                        SqlRel.Proj(SqlExpr.Col("code"), "code")) ++ extra,
      from = SqlRel.TableRef(table),
      where = Some(SqlExpr.Infix("=", SqlExpr.Col(valueSetKeyColumn), SqlExpr.str(vsId))))

  /** BIND-PROVIDER-REQUEST: value set `vsId` read from the FIRST terminology relation that holds it, so an
    * overlay's development set replaces its base's rather than merging with it (`CLIENT-PROVIDER-OVERLAY`).
    * One relation reads it directly. Several are a `UNION ALL` tagged with each layer's position, kept to
    * the rows of the lowest position present. No relation fails the membership (CORE-NO-SILENT-MISLOWER). */
  private def layeredValueSet(vsId: String, layers: Seq[RetrieveRelation]): SqlRel = {
    val tables = layers.map {
      case RetrieveRelation.Table(name, _) => name
      case other => throw new IllegalArgumentException(
        s"a terminology relation is a table or view, not $other (BIND-PROVIDER-REQUEST)")
    }
    tables match {
      case Seq() => throw UnsupportedNode("InValueSet",
        s"value-set membership on '$vsId' needs terminology, and the request has none (BIND-PROVIDER-REQUEST)")
      case Seq(only) => valueSetFrom(vsId, only)
      case many =>
        val layer = "__layer"
        val first = "__first"
        val tagged = SqlRel.UnionAll(many.zipWithIndex.map { case (t, i) =>
          valueSetFrom(vsId, t, Seq(SqlRel.Proj(SqlExpr.int(i.toLong), layer))) })
        val ranked = SqlRel.Select(
          projections = Seq(SqlRel.Proj(SqlExpr.Col("system"), "system"), SqlRel.Proj(SqlExpr.Col("code"), "code"),
            SqlRel.Proj(SqlExpr.Col(layer), layer),
            SqlRel.Proj(SqlExpr.Over(SqlExpr.Call("min", Seq(SqlExpr.Col(layer))), Nil, Nil), first)),
          from = SqlRel.AliasRel(tagged, "__layers"))
        SqlRel.Select(
          projections = Seq(SqlRel.Proj(SqlExpr.Col("system"), "system"), SqlRel.Proj(SqlExpr.Col("code"), "code")),
          from = SqlRel.AliasRel(ranked, "__ranked"),
          where = Some(SqlExpr.Infix("=", SqlExpr.Col(layer), SqlExpr.Col(first))))
    }
  }

  /** The name of the single mounted value-set expansion table (BIND-TERMINOLOGY-TABLE): `(valueset_id,
    * system, code)` rows for EVERY expanded value set the runtime mounted before the script runs.
    * When [[terminologyTable]] is set, the FQN is used instead of the bare convention name. */
  val valueSetsMountName: String = terminologyTable.getOrElse("valuesets")

  /** The key column of [[valueSetsMountName]] carrying the canonical value-set id each expansion row
    * belongs to (the URL/OID `vsId`), so a membership test filters its set out of the shared table. */
  val valueSetKeyColumn: String = "valueset_id"
}

object SqlEvalEnv {
  /** BIND-EXPLICIT-PARAMS: the retrieve UDTF's parameter list, in POSITIONAL order — the single source of
    * truth for the seam's shape, shared by the emitter, the runtime registrars, and the docs. The engine
    * always passes all eight positionally (it reads no catalog to discover a signature, `ARCH-PURITY`),
    * so a binding must declare all eight; the published template gives each a DEFAULT so a hand-written
    * call can omit trailing arguments. */
  val retrieveParams: Seq[(String, String)] = Seq(
    "value_set"     -> "STRING",
    "code_property" -> "STRING",
    "codes"         -> "ARRAY<STRUCT<system STRING, code STRING>>",
    "date_property" -> "STRING",
    "date_low"      -> "STRING",
    "date_high"     -> "STRING",
    "parameters"    -> "MAP<STRING, VARIANT>")

  /** The DEFAULT clause for each [[retrieveParams]] entry — an empty array/map for a collection, NULL for
    * a scalar (the ABSENT convention: a binding tests presence, never a sentinel). */
  def retrieveParamDefault(sqlType: String): String =
    if (sqlType.startsWith("ARRAY")) "array()" else if (sqlType.startsWith("MAP")) "map()" else "NULL"

  /** The parameter list as SQL DDL text (`value_set STRING DEFAULT NULL, …`) — what a binding author
    * (and every runtime registrar) puts between the parentheses of `CREATE FUNCTION`. */
  val retrieveParamsDdl: String =
    retrieveParams.map { case (n, t) => s"$n $t DEFAULT ${retrieveParamDefault(t)}" }.mkString(", ")
}
