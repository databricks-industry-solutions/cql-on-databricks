package com.databricks.cql.transpiler.lower

import com.databricks.cql.env.{Config, Context, DataBinding, RetrieveRelation, TerminologyBinding}
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.transpiler.emit.{SqlExpr, SqlNames, SqlRel}

/** The SQL-target evaluation environment (EMIT-SQL-EXECUTE): the Spark-FREE twin of the compiler's
  * `lower.EvalEnv`. It carries the SAME injected shape — the active context, the frozen [[Config]], and
  * the pure-IR materialization-policy inputs (`reuseCounts`/`skipUnfiltered`, threaded from
  * `passes.Materialize`) — but NONE of the effectful Spark surface: no `SparkSession`, no `DataProvider`,
  * no `Materializer`, no `TerminologyProvider` adapter. The SQL back end emits TEXT, so those seams
  * collapse into NAMES the runtime has mounted.
  *
  * THE RETRIEVE SEAM ([[retrieveMount]], BIND-CALL-SITE). Where the compiler's
  * `EvalEnv.requireData(...).retrieve(rt)` returns a `DataFrame` for `[Encounter]`, the SQL target names the
  * relation the request's [[data]] binds to the retrieve's call site, read as `SELECT __subject_id, __resource FROM
  * <name>` (`BIND-CONTRACT`). The retrieve's filters re-apply above it (`BIND-CAPABILITY`), so the transpiler never
  * constructs a scan and the module stays Spark-free (ARCH-PURITY). A retrieve with no relation fails.
  *
  * THE TERMINOLOGY SEAM ([[valueSetMount]], BIND-CALL-SITE). A membership reads its value set's `(system, code)` rows
  * from the relations [[terminologyBinding]] gives its site, the first that holds it winning, keyed by
  * `valueset_id`, and renders as a semi-join ([[SqlQueryLowering.membershipFilter]]). No terminology fails a
  * membership loudly (CORE-NO-SILENT-MISLOWER).
  *
  * `describe` lowers before anything is bound, so it lowers with [[describing]]'s placeholder relations, which are
  * named in no SQL anyone runs. */
final case class SqlEvalEnv(
    /** The active CQL context; decides the base relation's grouping (LANG-CONTEXT). */
    context: Context,
    /** Frozen engine configuration (TECH-CONFIG) — the SAME `Config` the proto path reads, so
      * `grainFirst`, the materialize mode, and the boundary set all carry over unchanged. */
    config: Config = Config.defaults,
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
    /** EMIT-ROOT-INDEPENDENT, BIND-RETRIEVE-REQUEST: each define's parameters, keyed by its own library's names
      * ([[com.databricks.cql.passes.ParameterScope]]). The caller sets it from the library BEFORE
      * `ParameterResolver` inlines parameters: after that no define references one, so a scope computed there is
      * empty. A retrieve's `parameters` carries only the current define's, so a parameter another library
      * declares never changes it. */
    defineParameters: Map[String, Seq[(String, com.databricks.cql.ir.ParameterDef)]] = Map.empty,
    /** ARCH-RESOURCE-HANDLE: the demand (navigated fields / whole-body need) per heterogeneous-`Choice`
      * list define, from `passes.ChoiceDemand.analyze` (the SAME pure pass the compiler uses — Spark-free).
      * The SQL het-union encoding is the demand-pruned HANDLE BY DEFAULT (the locked decision): a
      * resource member slot carries `{__resource_type, __resource_id, <demand fields>}`, never the whole
      * body (the boundary renders a resource by identity anyway, `JsonNormalize.isResourceHandle`). */
    choiceDemand: Map[String, com.databricks.cql.passes.ChoiceDemand.Demand] = Map.empty,
    /** EMIT-SQL-RETRIEVE-DEMAND-BOUND: the projection demand per FHIR resource TYPE, from
      * `passes.RetrieveDemand.analyze` (the SAME pure Spark-free pass). A plain retrieve's scanned
      * `__resource` is pruned to `Narrow(fields)` — identity lineage + only the top-level fields
      * navigated anywhere in the linked library (+ scan-filter fields) — instead of the whole modeled
      * body; `Full` (a whole-body escape / het-Choice arm) keeps the whole struct. Empty → no pruning (every retrieve keeps the
      * full body). */
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
      * dataset (`spark.table(cql_<sibling>)`) rather than re-inlining it.
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
    /** PIPE-EMIT-SQL — the naming context for published dataset names (`{library}_{version}[_{param_hash}]__
      * {define}`). Threaded from the compile request's library identity and parameter overrides. `None`
      * (the default, off the pipeline path) → `datasetName` is never called (the interactive `compileSql`
      * path has no cut and emits no published names). */
    datasetNameCtx: Option[SqlNames.DatasetNameContext] = None,
    /** BIND-CALL-SITE: how the request binds each retrieve call site, by rule or per site. A site with no
      * relation fails, and so does any retrieve when it is `None`: CQL that retrieves nothing needs no data. */
    data: Option[DataBinding] = None,
    /** BIND-CALL-SITE: how the request binds each value-set site, by rule or per site. `None` fails every
      * membership. */
    terminologyBinding: Option[TerminologyBinding] = None,
    /** BIND-CALL-SITE: every call site this lowering bound, by id, in first-seen order, for `describe`. Shared by
      * every copy of the env, like [[hoistCounter]]. */
    callSites: scala.collection.mutable.LinkedHashMap[String, CallSite] =
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

  /** The RELATION a reference to Relation define `n` reads ([[relRefName]]). */
  def relRef(n: String): SqlRel = SqlRel.TableRef(relRefName(n))

  /** The relation a reference to scalar define `n` reads ([[scalarRefName]]). */
  def scalarRef(n: String): SqlRel = SqlRel.TableRef(scalarRefName(n))

  /** The relation a reference to materialized read `obj` reads ([[readRefName]]). */
  def readRef(obj: String): SqlRel = SqlRel.TableRef(readRefName(obj))

  /** BIND-CALL-SITE: the library, version and own name of the define being lowered, which a call site is in;
    * none outside a define. */
  def siteOwner(lib: com.databricks.cql.ir.Library): Option[(String, String, String)] =
    currentDefine.map { d => val o = lib.ownerOf(d); (o.library, o.version, o.name) }

  /** `DATA-MODEL-SUBSET`: the shape of each resource type the bound data returns, from the request. `None` when
    * the lowering binds no data or only describes ([[describing]]): paths are then checked against FHIR R4
    * alone, and every choice slot is read. */
  def shapes: Option[Map[String, com.databricks.cql.sema.ResourceShape.Checked]] =
    data.filterNot(_ eq SqlEvalEnv.DescribeData).map(_.shapes)

  /** BIND-CALL-SITE: this env with [[SqlEvalEnv.DescribeData]] and [[SqlEvalEnv.DescribeTerminology]], for a
    * lowering that only reports call sites and shape (`describe`) and runs nothing. */
  def describing: SqlEvalEnv =
    copy(data = Some(SqlEvalEnv.DescribeData), terminologyBinding = Some(SqlEvalEnv.DescribeTerminology))

  /** The retrieve MOUNT for one retrieve call site (BIND-RELATION, BIND-CALL-SITE, BIND-CONTRACT): the table or view
    * the request binds to the site, read as the engine's `(__subject_id, __resource)`. The relation returns the
    * resource as its `resource` struct, and optionally `patient_id`, `min_date` and `max_date` (`DATA-SUBJECT-KEY`,
    * `BIND-PREFILTER`); the engine reads its own columns from them here ([[contractMount]]), so a column beyond the
    * contract never reaches a join. The retrieve's predicates still re-apply ABOVE the relation (BIND-CAPABILITY),
    * so a relation that ignores its request is a correct un-pruned superset. The compiler names the relation and
    * never bodies it (`ARCH-PURITY`). Every site is
    * recorded for `describe` ([[callSites]]). A lowering that only describes reads its placeholder relations as
    * the two engine columns, in SQL no one runs. */
  def retrieveMount(call: RetrieveCall, site: CallSite): SqlRel = {
    callSites.getOrElseUpdate(site.id, site)
    val binding = data.getOrElse(throw UnsupportedNode("Retrieve",
      s"retrieve site ${site.id} [${call.fhirType}] needs data, and the request binds none (BIND-CALL-SITE)"))
    val relation = binding.relationFor(site.id, call.fhirType).getOrElse(throw UnsupportedNode("Retrieve",
      s"retrieve site ${site.id} [${call.fhirType}] has no relation: the request's data has no rule and no " +
        "entry for it (BIND-CALL-SITE)"))
    shapes match {
      case None =>
        SqlRel.Select(
          projections = Seq(SqlRel.Proj(SqlExpr.Col(SystemColumns.SubjectId)), SqlRel.Proj(SqlExpr.Col(SystemColumns.Resource))),
          from = SqlRel.TableRef(relation.table))
      case Some(s) =>
        val shape = s.getOrElse(call.fhirType, throw UnsupportedNode("Retrieve",
          s"retrieve site ${site.id} [${call.fhirType}] has no data shape: the request's data shapes cover " +
            s"${if (s.isEmpty) "no type" else s.keys.toSeq.sorted.mkString(", ")} (DATA-MODEL-SUBSET)"))
        SqlEvalEnv.contractMount(call, relation.table, shape)
    }
  }

  /** The value-set MOUNT for `vsId` (BIND-CALL-SITE, LANG-VALUESET-VERSION): the `(system, code)` rows of one
    * expansion of the value set, selected out of the relations the request binds to the membership's site. One
    * relation holds every value set, keyed by the canonical id, the way a terminology export stores expansions,
    * with a `version` column. The site's request carries the version the CQL declaration pins, if any. The SQL
    * analog of `TerminologyProvider.expand`; fails loudly with no terminology. */
  def valueSetMount(vsId: String, site: CallSite): SqlRel = {
    callSites.getOrElseUpdate(site.id, site)
    val binding = terminologyBinding.getOrElse(throw UnsupportedNode("InValueSet",
      s"value-set membership on '$vsId' needs terminology, and the request binds none (BIND-CALL-SITE)"))
    val pinned = site.request.value.get("version").flatMap(_.strOpt)
    versionedValueSet(vsId, pinned, binding.relationsFor(site.id).getOrElse(Nil))
  }

  /** `SELECT system, code, version FROM <table> WHERE valueset_id = '<vsId>'`. */
  private def valueSetFrom(vsId: String, table: String, extra: Seq[SqlRel.Proj] = Nil): SqlRel =
    SqlRel.Select(
      projections = Seq(SqlRel.Proj(SqlExpr.Col("system"), "system"), SqlRel.Proj(SqlExpr.Col("code"), "code"),
                        SqlRel.Proj(SqlExpr.Col(valueSetVersionColumn), valueSetVersionColumn)) ++ extra,
      from = SqlRel.TableRef(table),
      where = Some(SqlExpr.Infix("=", SqlExpr.Col(valueSetKeyColumn), SqlExpr.str(vsId))))

  /** BIND-CALL-SITE: every row of value set `vsId`, every version, from the FIRST terminology relation that holds
    * it, so an overlay's development set replaces its base's rather than merging with it
    * (`CLIENT-RESOLVER-OVERLAY`). One relation reads it directly. Several are a `UNION ALL` tagged with each
    * layer's position, kept to the rows of the lowest position present. No relation fails the membership
    * (CORE-NO-SILENT-MISLOWER). */
  private def layeredValueSet(vsId: String, layers: Seq[RetrieveRelation]): SqlRel = {
    val v = valueSetVersionColumn
    layers.map(_.table) match {
      case Seq() => throw UnsupportedNode("InValueSet",
        s"value-set membership on '$vsId' needs terminology, and the request has none for it (BIND-CALL-SITE)")
      case Seq(only) => valueSetFrom(vsId, only)
      case many =>
        val layer = "__layer"
        val first = "__first"
        val tagged = SqlRel.UnionAll(many.zipWithIndex.map { case (t, i) =>
          valueSetFrom(vsId, t, Seq(SqlRel.Proj(SqlExpr.int(i.toLong), layer))) })
        val ranked = SqlRel.Select(
          projections = Seq(SqlRel.Proj(SqlExpr.Col("system"), "system"), SqlRel.Proj(SqlExpr.Col("code"), "code"),
            SqlRel.Proj(SqlExpr.Col(v), v), SqlRel.Proj(SqlExpr.Col(layer), layer),
            SqlRel.Proj(SqlExpr.Over(SqlExpr.Call("min", Seq(SqlExpr.Col(layer))), Nil, Nil), first)),
          from = SqlRel.AliasRel(tagged, "__layers"))
        SqlRel.Select(
          projections = Seq(SqlRel.Proj(SqlExpr.Col("system"), "system"), SqlRel.Proj(SqlExpr.Col("code"), "code"),
            SqlRel.Proj(SqlExpr.Col(v), v)),
          from = SqlRel.AliasRel(ranked, "__ranked"),
          where = Some(SqlExpr.Infix("=", SqlExpr.Col(layer), SqlExpr.Col(first))))
    }
  }

  /** LANG-VALUESET-VERSION: the `(system, code)` rows of ONE expansion of value set `vsId`, read from the layer
    * [[layeredValueSet]] picks, or a statement that fails, naming the value set.
    *
    * CQL 1.5 Author's Guide, Valuesets: "If the binding specifies a valueset version, then the expansion set must
    * be derived from that specific version", so a `pinned` declaration reads that version's rows and fails when
    * the layer has none. With no version the expansion is "constructed using the most current published version
    * of the valueset". A version here is free text with no order, so nothing here can tell which is most current:
    * an unpinned value set must hold exactly one version (no version counts as one), and several fail rather
    * than merge or guess. A value set no relation holds fails too, where it would otherwise match no code and
    * silently under-count every population that retrieves through it.
    *
    * The rows are read through one global aggregate, which returns a row even over no input: it checks them and
    * collects the codes in the same pass, and the codes are exploded back into rows. So the check runs with the
    * read on every compute and for every relation a resolver binds, with no extra round trip, and no optimizer
    * can drop it the way it drops a filter over a relation it proves empty (a view keyed by literals). It runs
    * whenever the membership is evaluated over rows; with no rows to test, the value set cannot change an answer.
    * The expansion is one row's array, as small as the broadcast side of the membership join it feeds. */
  private def versionedValueSet(vsId: String, pinned: Option[String], layers: Seq[RetrieveRelation]): SqlRel = {
    import SqlExpr.{Call, Col, Field, Infix, Lambda, Struct, int, str}
    val v = Col(valueSetVersionColumn)
    def fail(message: SqlExpr): SqlExpr = Call("raise_error", Seq(message))
    val held = Call("array_join", Seq(Call("array_sort", Seq(Call("collect_set",
      Seq(Call("coalesce", Seq(v, str("(no version)"))))))), str(", ")))
    val absent = Infix("=", Call("count", Seq(Col("*"))), int(0)) ->
      fail(str(s"value set '$vsId' is in no terminology relation the run binds " +
        s"(${layers.map(_.table).mkString(", ")}) (LANG-VALUESET-VERSION)"))
    val codeOf = (x: SqlExpr) => Struct(Seq("system" -> Field(x, "system"), "code" -> Field(x, "code")))
    val (wrongVersion, codes) = pinned match {
      case Some(p) =>
        (Infix("=", Call("count_if", Seq(Infix("=", v, str(p)))), int(0)) ->
          fail(Call("concat", Seq(str(s"value set '$vsId' has no version '$p': it holds "), held,
            str(" (LANG-VALUESET-VERSION)")))),
         Call("transform", Seq(
           Call("filter", Seq(Call("collect_list", Seq(Struct(Seq("system" -> Col("system"), "code" -> Col("code"),
             valueSetVersionColumn -> v)))), Lambda(Seq("x"), Infix("=", Field(Col("x"), valueSetVersionColumn), str(p))))),
           Lambda(Seq("x"), codeOf(Col("x"))))))
      case None =>
        (Infix(">", Call("size", Seq(Call("collect_set", Seq(Call("coalesce", Seq(v, str(""))))))), int(1)) ->
          fail(Call("concat", Seq(str(s"value set '$vsId' holds several versions, "), held,
            str(", and its declaration pins none: pin one (valueset \"...\": '<url>' version '<version>') " +
              "or bind terminology that holds one version (LANG-VALUESET-VERSION)")))),
         Call("collect_list", Seq(Struct(Seq("system" -> Col("system"), "code" -> Col("code"))))))
    }
    val checked = SqlRel.Select(
      projections = Seq(SqlRel.Proj(SqlExpr.Case(Seq(absent, wrongVersion), Some(codes)), "__codes")),
      from = SqlRel.AliasRel(layeredValueSet(vsId, layers), "__vs_rows"))
    SqlRel.Select(
      projections = Seq(SqlRel.Proj(Field(Col("__code"), "system"), "system"),
                        SqlRel.Proj(Field(Col("__code"), "code"), "code")),
      from = SqlRel.LateralView(SqlRel.AliasRel(checked, "__vs_checked"), Call("explode", Seq(Col("__codes"))),
        "__vs_codes", Seq("__code")))
  }

  /** The key column of a value-set relation carrying the canonical value-set id each expansion row
    * belongs to (the URL/OID `vsId`), so a membership test filters its set out of the shared table. */
  val valueSetKeyColumn: String = "valueset_id"

  /** The column of a value-set relation carrying the version of the expansion each row belongs to (FHIR
    * `ValueSet.version`, the version a CQL declaration pins); null when the source gives none
    * (LANG-VALUESET-VERSION). */
  val valueSetVersionColumn: String = "version"
}

object SqlEvalEnv {
  import com.databricks.cql.sema.{FhirModel, ResourceShape}
  import SqlExpr._

  /** BIND-CONTRACT, DATA-SUBJECT-KEY, BIND-PREFILTER: a relation on the contract read as the engine's
    * `(__subject_id, __resource)`.
    *
    * `__resource` is the relation's `resource` struct, passed through untouched: the mount renames it and never
    * rebuilds it, so the warehouse plans no struct. It carries no lineage; lowering reads a resource's identity
    * from its own `id` and its type from the retrieve (`DATA-EXPORT`). `__subject_id` is the row's patient_id
    * (`DATA-SUBJECT-KEY`). On a Patient or a Patient-compartment type it is the root `patient_id` when the relation
    * returns it, a stored key the warehouse can filter and cluster on; else `resource.id` for a Patient, or the id
    * after the last `/` of the first of `subject`, `patient` and `beneficiary` whose `reference` the shape holds. A
    * type with no patient context has none: its key is null, and a `patient_id` it returns is not read.
    *
    * Filter and refine: when the relation returns `min_date` and `max_date` and the retrieve carries a literal date
    * range, the mount keeps only rows whose dates can meet it, `max_date >= low AND min_date <= high`, widened by a
    * day on each side so no time zone drops a row. It is the coarse filter; the retrieve's own date test, above the
    * mount, is the refine step and decides. */
  private[lower] def contractMount(call: RetrieveCall, table: String, shape: ResourceShape.Checked): SqlRel = {
    val rt = shape.resourceType
    val resource = Col(ResourceShape.ResourceColumn)
    def refOf(field: String): Boolean = shape.struct.field(field).exists(_.shape match {
      case s: com.databricks.cql.sema.FhirShape.Struct => s.field("reference").isDefined
      case _                                          => false
    })
    val subject: SqlExpr =
      if (rt != "Patient" && !FhirModel.isPatientContext(rt)) Cast(Null, "STRING")
      else if (shape.hasPatientId) Cast(Col(ResourceShape.PatientId), "STRING")
      else if (rt == "Patient") Cast(Field(resource, "id"), "STRING")
      else FhirModel.SubjectReferenceFields.filter(refOf) match {
        case Seq() => throw UnsupportedNode("Retrieve",
          s"the $rt data at $table has no patient key: return a `${ResourceShape.PatientId}` column, or the " +
            s"${FhirModel.SubjectReferenceFields.filter(f => FhirModel.navType(rt, f).isDefined).mkString(" or ")} " +
            "reference in `resource` (BIND-CONTRACT, DATA-SUBJECT-KEY)")
        case refs =>
          val ref = refs.map(f => Field(Field(resource, f), "reference"))
          Call("substring_index", Seq(if (ref.size == 1) ref.head else Call("coalesce", ref), str("/"), Lit("-1")))
      }
    SqlRel.Select(
      projections = Seq(SqlRel.Proj(subject, SystemColumns.SubjectId), SqlRel.Proj(resource, SystemColumns.Resource)),
      from = SqlRel.TableRef(table),
      where = coarseDates(call, shape))
  }

  private val IsoDay = "^(\\d{4}-\\d{2}-\\d{2}).*".r

  /** BIND-PREFILTER: the coarse date filter, when the retrieve's range is a literal of day precision or finer and
    * the relation returns both `min_date` and `max_date`. A range it cannot read as days gets no coarse filter, never a
    * guessed one. */
  private def coarseDates(call: RetrieveCall, shape: ResourceShape.Checked): Option[SqlExpr] =
    if (!shape.hasDates) None
    else (call.dateLow, call.dateHigh) match {
      case (Some(IsoDay(lo)), Some(IsoDay(hi))) =>
        val low  = java.time.LocalDate.parse(lo).minusDays(1)
        val high = java.time.LocalDate.parse(hi).plusDays(1)
        Some(Infix("AND",
          Infix(">=", Col(ResourceShape.MaxDate), Lit(s"DATE'$low'")),
          Infix("<=", Col(ResourceShape.MinDate), Lit(s"DATE'$high'"))))
      case _ => None
    }

  /** BIND-CALL-SITE: the schema of [[describing]]'s placeholder relations. No SQL naming it is run. */
  val DescribeSchema: String = "__describe"

  /** BIND-CALL-SITE: every retrieve read from `__describe.<type in lower case>`, for `describe`. */
  val DescribeData: DataBinding = DataBinding(Some(DataBinding.Rule(DescribeSchema)), Map.empty)

  /** BIND-CALL-SITE: every value set read from `__describe.valueset`, for `describe`. */
  val DescribeTerminology: TerminologyBinding =
    TerminologyBinding(Some(Seq(RetrieveRelation(s"$DescribeSchema.valueset"))), Map.empty)
}
