package com.databricks.cql.service

import com.databricks.cql.env.{Config, Context, DataBinding, RetrieveRelation, TerminologyBinding}
import com.databricks.cql.ir.{Expr, ExpressionDef, Library}
import com.databricks.cql.meta.{LibraryDescribe, LibraryGraph}
import com.databricks.cql.passes.{BuildInfo, LibraryLinker, Optimize, ParameterScope, Pipeline, RetrieveCatalog, RetrieveReads}
import com.databricks.cql.retrieve.TerminologyProvider
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.translate.CqlToElmCli
import com.databricks.cql.transpiler.emit.{SqlNames, SqlRel, SqlRender, SqlStmt}
import com.databricks.cql.transpiler.lower.{CallSite, SqlEvalEnv, SqlFrameLowering}

import scala.collection.mutable

/** A compile request (`BUILD-COMPILE-SERVICE`) — DATA-FREE: the CQL library
  * as ELM JSON, the target define to build, the fixed materialization boundary set (`RUNTIME` compiler
  * mode — [[Config.materializeBoundaries]]), the CQL context, and any config overrides. It carries NO
  * data and NO session — the host wires those; the compiler touches no data (governance stays at the
  * executor).
  *
  * @param elmJson      the ROOT library as ELM JSON (the interchange form, `EMIT-INTERCHANGE`). Empty when
  *                     `cql` is supplied instead (the service translates CQL → ELM, `BUILD-CLIENT-SESSION`).
  * @param cql          raw CQL TEXT to translate to ELM in-process (`CqlToElmCli`) — the alternative to
  *                     `elmJson`; exactly one is non-empty.
  * @param deps         the ROOT's dependency libraries as ELM JSON — the caller-supplied include closure
  *                     for a multi-library eCQM (`BUILD-COMPILE-LINK`). The service is a DUMB compile
  *                     endpoint: it does NO library-source RESOLUTION (discovering which libraries are
  *                     needed, fetching them) — the caller resolves and ships every dependency ELM here.
  *                     The service only MERGES what it is handed (`LibraryLinker.link`/`prune`, a pure IR
  *                     transform) into one self-contained library before lowering. Empty = single-library
  *                     input (today's behavior, unchanged). `FHIRHelpers` may be included harmlessly (it is
  *                     lowered directly, never merged). An include whose ELM is not supplied fails loudly
  *                     (`CORE-NO-SILENT-MISLOWER`) rather than silently dropping the dependency.
  * @param target       the define to compile to a plan; `None` builds the whole assembled frame.
  * @param boundaries   the fixed barrier set — `Some` selects RUNTIME (cost-free) lowering; `None`
  *                     GENERATION (cost-model) lowering.
  * @param context      the CQL context (`Patient` / `Unfiltered` / `Population` / …), or `None` when the
  *                     caller specified none — then it is resolved per `LANG-CONTEXT-DEFAULT`
  *                     ([[CompileService.resolveContext]]): the library's declared `context`, else the
  *                     model default, else `Unfiltered`. The client never imposes `Patient`.
  * @param overrides    namespaced config overrides (`Config.resolve`).
  * @param parameters   CQL parameter-VALUE overrides: name → CQL expression text the compiler inlines as
  *                     a literal (`BUILD-CLIENT-SESSION`, the engine-supplied override seam of
  *                     `ParameterResolver`). Naming an undeclared parameter fails loudly. */
final case class CompileRequest(
    elmJson: String = "",
    target: Option[String] = None,
    boundaries: Option[Set[String]] = None,
    context: Option[String] = None,
    overrides: Map[String, String] = Map.empty,
    grainFirst: Boolean = false,
    cql: Option[String] = None,
    parameters: Map[String, String] = Map.empty,
    /** BUILD-CLIENT-SESSION-PARAMETERS: parameter values a client set for its whole session, applied only to
      * the parameters THIS library declares, and only where [[parameters]] names none. A session period
      * then binds every measure that declares one and leaves a library that does not untouched. */
    sessionParameters: Map[String, String] = Map.empty,
    deps: Seq[String] = Seq.empty,
    /** BUILD-COMPILE-LINK: dependency libraries supplied as raw CQL TEXT rather than ELM — the closure a
      * caller with no translator resolves and ships (`BUILD-LIBRARY-PROVIDER`). These are registered as
      * translation SOURCES for the root so its `include`s resolve. SVC-COMPILE-BATCH retains the ELM
      * closure from that translation for `LibraryLinker.link`, without retranslating each include.
      * An ELM root with CQL-only dependencies still needs to translate those dependency sources. */
    depCql: Seq[String] = Seq.empty,
    /** EMIT-UDF-QUALIFY: optional schema qualifier for emitted `cql_*` UDF calls (e.g.
      * `"cql_databricks.runtime"`). Parsed from `runtime.schema` in the wire JSON. When set, every `cql_*` function
      * call in the emitted SQL is qualified as `schema.cql_fn(…)` — so a pipeline whose session search path does not
      * include the UDF registration schema resolves the functions. When absent or empty, bare names are emitted. */
    udfSchema: Option[String] = None,
    /** EMIT-SQL-OBJECT-PREFIX: optional namespace for emitted SESSION object names — every `obj`/`rel`/
      * null-sidecar/retrieve-read name becomes `<prefix>__<name>`. DEFAULT OFF (`None` ⇒ bare names, the
      * long-standing emitted form). A caller lowering more than one library into ONE Spark session sets it
      * so two libraries' identically-named defines (`Numerator`) cannot rebind each other's temp views. */
    objectPrefix: Option[String] = None,
    durable: Boolean = false,
    /** SVC-COMPILE-BATCH — raw bounds are typed against the root's ELM on the service. */
    measurementPeriod: Option[(String, String)] = None,
    /** BIND-CALL-SITE: how the request binds each retrieve call site, by rule, per site, or both. `None` binds
      * nothing, which only CQL that retrieves nothing compiles with. */
    data: Option[DataBinding] = None,
    /** BIND-CALL-SITE: how the request binds each value-set site; each list in precedence order, a set read
      * from the first relation that holds it. Parsed from the wire's `terminology`. */
    terminology: Option[TerminologyBinding] = None)

object CompileRequest {
  /** Parse the wire JSON `{elm, cql?, knowledge?, deps?, target?, boundaries?, context?, overrides?,
    * grainFirst?, parameters?, data?, terminology?, runtime?}`. `knowledge` (or `deps`) is the include closure
    * (`BUILD-COMPILE-LINK`); `data` and `terminology` bind the call sites (`BIND-CALL-SITE`); `runtime.schema`
    * qualifies the `cql_*` calls (EMIT-UDF-QUALIFY). A `libraries` field, and `sources` or `dataSource` (the per-type
    * bindings `BIND-CALL-SITE` replaced), is refused, naming it. */
  def fromJson(json: String): CompileRequest = {
    val o = ujson.read(json)
    // BIND-CALL-SITE: one field per role. A request carrying them never also carries the per-type `deps` they replace.
    val present = (key: String) => o.obj.get(key).exists(v => !v.isNull)
    // SVC-PROTOCOL: a retired key is refused, naming it. Each root compiles on its own, with its dependencies
    // in `knowledge` (BUILD-COMPILE-LINK).
    require(!o.obj.contains("libraries"), "a compile request has no libraries: compile each root with its " +
      "dependencies in knowledge (BUILD-COMPILE-LINK)")
    val retired = Seq("sources", "dataSource").filter(o.obj.contains)
    require(retired.isEmpty, s"a compile request has no ${retired.mkString(" or ")}: bind each role with data and " +
      "terminology (BIND-CALL-SITE)")
    val roles = Seq("data", "terminology", "knowledge", "runtime").filter(present)
    val replaced = Seq("deps").filter(present)
    require(roles.isEmpty || replaced.isEmpty,
      s"a request with ${roles.mkString(", ")} has no ${replaced.mkString(", ")} (BIND-CALL-SITE)")
    // BUILD-COMPILE-LINK: `knowledge` is the include closure, each entry as a `deps` entry is.
    val closure = o.obj.get("knowledge").filterNot(_.isNull).orElse(o.obj.get("deps").filterNot(_.isNull))
    CompileRequest(
      elmJson = o.obj.get("elm").filterNot(_.isNull).map(_.str).getOrElse(""),
      cql = o.obj.get("cql").flatMap(v => if (v.isNull) None else Some(v.str)),
      // BUILD-COMPILE-LINK: a `deps` entry is EITHER an ELM JSON string (the long-standing form) or a
      // `{ "cql": "…" }` object (a caller-resolved CQL closure). Split by JSON type so both may be mixed
      // in one array and an old caller's all-string array parses exactly as before.
      deps = closure
        .map(_.arr.iterator.filter(_.isInstanceOf[ujson.Str]).map(_.str).toSeq).getOrElse(Seq.empty),
      depCql = closure
        .map(_.arr.iterator.collect { case v: ujson.Obj => v }
          .flatMap(_.obj.get("cql").filterNot(_.isNull).map(_.str)).toSeq).getOrElse(Seq.empty),
      target = o.obj.get("target").flatMap(v => if (v.isNull) None else Some(v.str)),
      boundaries = o.obj.get("boundaries").filterNot(_.isNull).map(_.arr.map(_.str).toSet),
      context = o.obj.get("context").filterNot(_.isNull).map(_.str),
      overrides = o.obj.get("overrides").filterNot(_.isNull)
        .map(_.obj.map { case (k, v) => k -> v.str }.toMap).getOrElse(Map.empty),
      grainFirst = o.obj.get("grainFirst").filterNot(_.isNull).exists(_.bool),
      durable = o.obj.get("durable").filterNot(_.isNull).exists(_.bool),
      measurementPeriod = o.obj.get("measurementPeriod").filterNot(_.isNull).map { p =>
        require(p.arr.size == 2, "measurementPeriod requires start and end")
        (p(0).str, p(1).str)
      },
      parameters = o.obj.get("parameters").filterNot(_.isNull)
        .map(_.obj.map { case (k, v) => k -> v.str }.toMap).getOrElse(Map.empty),
      sessionParameters = o.obj.get("sessionParameters").filterNot(_.isNull)
        .map(_.obj.map { case (k, v) => k -> v.str }.toMap).getOrElse(Map.empty),
      // EMIT-UDF-QUALIFY: runtime.schema → schema qualifier for cql_* UDF calls.
      udfSchema = o.obj.get("runtime").filterNot(_.isNull)
        .flatMap(_.obj.get("schema")).filterNot(_.isNull).map(_.str).filter(_.nonEmpty),
      data = o.obj.get("data").filterNot(_.isNull).map(DataBinding.fromJson),
      terminology = o.obj.get("terminology").filterNot(_.isNull).map(TerminologyBinding.fromJson),
      // EMIT-SQL-OBJECT-PREFIX: absent/empty ⇒ None ⇒ bare object names (the default).
      objectPrefix = o.obj.get("objectPrefix").filterNot(_.isNull).map(_.str).filter(_.nonEmpty))
  }
}

/** One statement of a compiled SQL script (EMIT-SQL-EXECUTE): the object it defines, its KIND
  * (`view` | `cache_table` | `table`), and the rendered single-statement SQL (no trailing `;`). */
final case class SqlStatementOut(name: String, kind: String, sql: String)

/** The READ SHAPE of one define's result (`EMIT-SQL-VALUE-COLUMN`) — everything a consumer needs to read
  * it without re-deriving a naming convention or probing the schema at runtime (`ARCH-NO-RUNTIME-SCHEMA`).
  *
  * Every define publishes its payload under the SAME `valueColumn`, so the define's own name never appears
  * in its result schema. What differs between defines is only the ROW shape, and that is stated here as
  * metadata rather than left to be inferred from the presence of `__ordinal`.
  *
  * @param define      the RAW `ExpressionDef.name` (`"Initial Population"` — spaces and casing preserved),
  *                    so a caller correlates back to the source without knowing `SqlNames.sanitize`.
  * @param obj         the object the script produces for it (`SqlNames.obj` / `SqlNames.rel`).
  * @param kind        `"scalar"` — `(__subject_id, <valueColumn>)`, ONE row per subject; or `"relation"` —
  *                    grain-first `(__subject_id, __ordinal, <valueColumn>)`, one row per list ELEMENT in
  *                    `__ordinal` order. A list carried as a per-subject ARRAY is `"scalar"` with an
  *                    array-typed value, so `kind` describes the ROW shape and nothing else.
  * @param valueColumn the payload column — always [[SystemColumns.Val]]. PUBLISHED rather than assumed so a
  *                    consumer reads it off the contract instead of hard-coding the convention. */
final case class DefineShape(define: String, obj: String, kind: String,
                             valueColumn: String = SystemColumns.Val)

object DefineShape {
  val Scalar   = "scalar"
  val Relation = "relation"
  /** `DATA-RETRIEVE-BARRIER-OPT`: a materialized READ's row shape — `(__subject_id, __resource)`, one row
    * per matching resource, payload in [[SystemColumns.Resource]]. A third value rather than a reuse of
    * `relation` because it genuinely IS a third row shape: a read is the mount's rows post-filter, before
    * any grain-first projection, so it carries no `__ordinal` and its payload is the resource struct
    * rather than `__val`. A consumer that hard-coded "kind implies `__val`" reads `valueColumn` instead. */
  val Resource = "resource"
}

/** `DATA-RETRIEVE-BARRIER-OPT` — what a materialized READ dataset holds, in the terms a consumer can act
  * on. This is the provenance vehicle for a dataset that has no define to name it: a read is identified by
  * an opaque content-addressed name (`SqlNames`/`RetrieveReads.objName`), and everything a human or a
  * report needs to say about it comes off these fields — exactly as a define recipe is correlated by
  * `(library, libraryVersion, define)` rather than by parsing the object name.
  *
  * @param resourceType        the FHIR type read (`"Condition"`).
  * @param profile             the SPECIALISED profile canonical constrained to (`LANG-RETRIEVE-PROFILE`).
  * @param codeProperty        the element the code filter tests (`"code"`, `"type"`, `"medication"`).
  * @param codeFilterKind      `"valueset"` | `"codes"` | `"other"` | `"none"` — what KIND of code filter
  *                            applies, so a consumer never mistakes an unresolvable bracket for no filter
  *                            (`CORE-NO-SILENT-MISLOWER`).
  * @param valueSetId          the CANONICAL value-set url/oid when `codeFilterKind = "valueset"` — the key
  *                            the runtime's `valuesets` table is keyed by (`DATA-ENVIRONMENT-LAYOUT`).
  * @param codes               literal `(system, code)` pairs when `codeFilterKind = "codes"`.
  * @param dateProperty        the element a folded date filter applies to.
  * @param dateFiltered        whether a date range folded onto the read. The range itself is NOT published:
  *                            after pushdown it is an arbitrary expression (commonly measurement-period
  *                            derived), so only its presence and property are statically known.
  * @param periodProperty      the coarse period pre-filter's property, when one folded.
  * @param periodLowInclusive  the period window's inclusive low bound (a literal ISO string).
  * @param periodHighExclusive the period window's exclusive high bound.
  * @param consumedBy          the defines whose bodies read this read, in library order.
  * @param callSites           how many call sites read it — the reuse figure `materialize.retrieves =
  *                            costly` thresholds. */
final case class ReadCoordinates(resourceType: String,
                                 profile: Option[String] = None,
                                 codeProperty: Option[String] = None,
                                 codeFilterKind: String = "none",
                                 valueSetId: Option[String] = None,
                                 codes: Seq[(String, String)] = Nil,
                                 dateProperty: Option[String] = None,
                                 dateFiltered: Boolean = false,
                                 periodProperty: Option[String] = None,
                                 periodLowInclusive: Option[String] = None,
                                 periodHighExclusive: Option[String] = None,
                                 consumedBy: Seq[String] = Nil,
                                 callSites: Int = 0)

/** The compiled SQL result (EMIT-SQL-EXECUTE): the ordered statements the client runs via `spark.sql`
  * in turn, the `target` object to `SELECT` afterward, and the define names as metadata. The SQL is a
  * small, auditable DAG (view/CTE by name) — the honest alternative to the share-free proto.
  *
  * `shapes` is the per-define READ CONTRACT (`EMIT-SQL-VALUE-COLUMN`, one [[DefineShape]] per entry of
  * `defines`, same order): the object, the row kind, and the value column. A consumer reads a result
  * through it rather than re-deriving `SqlNames.sanitize` or inspecting the produced schema. */
final case class SqlCompileResult(statements: Seq[SqlStatementOut], target: String, defines: Seq[String],
                                  shapes: Seq[DefineShape] = Nil) {
  /** The whole script as one text blob (statements `;`-terminated), for dumping / auditing. */
  def script: String = statements.map(_.sql).mkString("", ";\n\n", ";\n")
}

/** One dataset RECIPE of a compiled [[PipelinePlan]] (`PIPE-SQL-DAG-REUSE`): the self-contained sub-script
  * that produces ONE materialized define, cut out of the single whole-library lowering.
  *
  * @param name       the PUBLISHED dataset name ([[SqlNames.datasetName]] =
  *                   `{library}_{version}[_{param_hash}]__{define}`) — the SDP
  *                   `@dp.materialized_view(name=…)` / `CREATE OR REFRESH MATERIALIZED VIEW` this recipe
  *                   becomes AND the name a dependent recipe reads it by. The single naming source of truth
  *                   (a consumer never re-derives). The library+version prefix keeps the published namespace
  *                   disjoint from internal object names and unique across multi-measure pipelines.
  * @param target     the LOCAL object the recipe's own statements produce (a scalar or relation define's
  *                   sanitized `<define>` view) — what the pipeline node `SELECT`s / returns after running
  *                   the statements. Distinct from `name` (the library-qualified prefix), so the node never
  *                   creates a session object that clashes with the dataset it publishes.
  * @param reads      the published names ([[SqlNames.datasetName]]) of the upstream materialized SIBLINGS
  *                   this recipe depends on — the DECLARED dataset reads that are the SDP dependency edges.
  *                   The recipe's `statements` reference each sibling by this name; the node body must make
  *                   the name resolve as a declared read (`dp.read`/`spark.read.table`).
  * @param statements the sub-script (in dependency order): this define's producer plus every NON-materialized
  *                   dependency view it needs, re-inlined. References to a materialized sibling are already
  *                   `FROM cql_<sibling>` (resolved at lowering, not re-derived). The DDL form the
  *                   interactive `cql()` client / `spark.sql` path runs (it allows `CREATE VIEW`/`CACHE
  *                   TABLE`); a Spark Declarative Pipeline body cannot run DDL commands, so it uses `query`.
  * @param query      the SAME sub-script collapsed to ONE declarative `WITH … SELECT * FROM <target>`
  *                   query (no DDL): what a Spark Declarative Pipeline dataset body returns
  *                   (`spark.sql(query)`), since SDP rejects `CREATE VIEW`/`CACHE TABLE` commands in a body
  *                   (`UNSUPPORTED_PIPELINE_SPARK_SQL_COMMAND`). The materialized-sibling reads appear as
  *                   `FROM cql_<sibling>` in the query, so SDP traces them as the dependency edges.
  * @param library        the ELM library name that owns this define (e.g. `"MyMeasure"`). For single-library
  *                       plans this is the root library's `identifier.id`; for multi-library
  *                       it is the originating root's id, recovered by splitting the
  *                       qualified define name at `__`.
  * @param libraryVersion the ELM library version (e.g. `"1.2.000"`), empty when the source declares none.
  * @param define         the RAW `ExpressionDef.name` from the ELM (e.g. `"Initial Population"` — spaces and
  *                       casing preserved, NOT the sanitized form). A client can correlate a recipe to its
  *                       source `ExpressionDef` by `(library, libraryVersion, define)` without knowing the
  *                       `SqlNames.sanitize` convention.
  * @param sqlFile        the deploy-ready SDP SQL statement (`PIPE-EMIT-SQL`): `CREATE OR REFRESH MATERIALIZED VIEW`
  *                       for materialized defines, `CREATE OR REFRESH VIEW` for non-materialized. The SQL
  *                       needs no runtime compilation and no client library. Empty when the service does not
  *                       emit it.
  * @param kind           the published dataset's ROW shape (`EMIT-SQL-VALUE-COLUMN`, [[DefineShape.Scalar]] |
  *                       [[DefineShape.Relation]]): `scalar` is one row per subject, `relation` is grain-first
  *                       one row per list element in `__ordinal` order. Stated here so a consumer reading the
  *                       published dataset never probes its schema (`ARCH-NO-RUNTIME-SCHEMA`).
  * @param valueColumn    the payload column of the published dataset — always [[SystemColumns.Val]], the SAME
  *                       column whatever `kind` is, so a generic reader is kind-agnostic. */
final case class DatasetRecipe(name: String, target: String, reads: Seq[String],
                               statements: Seq[SqlStatementOut], query: String,
                               library: String, libraryVersion: String, define: String,
                               sqlFile: String = "",
                               kind: String = DefineShape.Scalar,
                               valueColumn: String = SystemColumns.Val,
                               /** `DATA-RETRIEVE-BARRIER-OPT`: present ONLY on a materialized retrieve-READ
                                 * recipe — the coordinates identifying which rows it holds. `None` on a
                                 * define recipe (whose provenance is `library`/`libraryVersion`/`define`), so
                                 * the field also distinguishes the two kinds of recipe without parsing a
                                 * name. Named `retrieveRead` rather than `read` so it cannot be confused with
                                 * the sibling-dependency `reads` above. */
                               retrieveRead: Option[ReadCoordinates] = None,
                               durable: Option[DurableIdentity] = None)

/** The compile-once PIPELINE PLAN (`PIPE-SQL-DAG-REUSE`, `PIPE-RUNTIME-COMPILE`, `MATERIALIZE-SINK`): the
  * whole measure lowered ONCE, then cut at each materialized define into one [[DatasetRecipe]] per
  * materialized define. The reference pipeline turns each recipe into one `@dp.materialized_view` — with NO
  * per-node `cql()` call — so a materialized sibling is computed once, as its own dataset, and dependents
  * read it back by name (the cross-dataset DAG cut the SQL back end previously lacked). Results are
  * IDENTICAL to the fully re-inlined baseline (`CORE-NO-SILENT-MISLOWER`): only the structure differs. */
final case class PipelinePlan(datasets: Seq[DatasetRecipe], compilerVersion: String = BuildInfo.version)

/** The compile-service LOGIC (`BUILD-COMPILE-SERVICE`): lower CQL+ELM to a Spark SQL SCRIPT
  * (`compileSql`, EXEC-SQL-BACKEND) and DESCRIBE the neutral graph an external pipeline consumes
  * (`describe`). Both are SPARK-FREE (pure IR → text / metadata), so the service holds NO `SparkSession`
  * and touches no data — a strict strengthening of ARCH-PURITY. The only injected dependency is whether a
  * terminology expansion is mounted at runtime (`terminology.isDefined` → the `valueset_<id>` seam); the
  * relations each retrieve call site binds are an external contract the request supplies (`BIND-CALL-SITE`). */
final class CompileService(terminology: Option[TerminologyProvider] = None) {

  /** SQL COMPILE (EMIT-SQL-EXECUTE): lower the optimized IR to a Spark SQL SCRIPT — one
    * `CREATE OR REPLACE TEMP VIEW` per define (cost-chosen barriers as `CACHE TABLE`), a small auditable
    * DAG the client runs via `spark.sql`. SESSION-FREE. */
  def compileSql(req: CompileRequest): SqlCompileResult =
    CompileService.compileSql(req)

  /** PIPELINE PLAN (`PIPE-SQL-DAG-REUSE`): compile the whole library ONCE and cut the resulting DAG at each
    * materialized define into one dataset recipe per materialized define — the reference pipeline's
    * `@dp.materialized_view` bodies, with NO per-node `cql()`. SESSION-FREE. */
  def compilePipeline(req: CompileRequest): PipelinePlan =
    CompileService.compilePipeline(req)

  /** DESCRIBE (`BUILD-COMPILE-SERVICE`): the neutral, pipeline-agnostic graph metadata an external
    * pipeline reads to build its own dataset graph (`LibraryDescribe`). When the request carries a
    * GENERATION mode — the SQL classifier chooses the boundary set. Spark-free: nothing executes. */
  def describe(req: CompileRequest): LibraryGraph = {
    val loaded = CompileService.normalizedInput(req) // root + linked/pruned dependency closure (BUILD-COMPILE-LINK)
    LibraryDescribe.describe(loaded, CompileService.resolveContext(req, loaded),
      overrides = req.overrides, grainFirst = req.grainFirst,
      terminologyVersion = None)
  }
}

object CompileService {
  /** The SESSION-FREE SQL compile (EMIT-SQL-EXECUTE): the front end (`normalize` + `Optimize`) then
    * [[SqlFrameLowering]] to a script rendered by [[SqlRender]]. Pure
    * (no Spark), so it is directly unit-testable. The request's `data` and `terminology` bind every call site
    * (`BIND-CALL-SITE`). */
  def compileSql(request: CompileRequest): SqlCompileResult = {
    val req = prepareRequest(request)
    val base   = Config.resolve(req.overrides).copy(grainFirst = req.grainFirst,
      // EMIT-SQL-OBJECT-PREFIX: the request field is the explicit tier over the
      // `emit.object_prefix` override; absent leaves the config default (empty = bare names).
      objectPrefix = req.objectPrefix.getOrElse(Config.resolve(req.overrides).objectPrefix))
    val config = req.boundaries.map(b => base.copy(materializeBoundaries = Some(b))).getOrElse(base)
    val loaded = normalizedInput(req)
    val optimized = Optimize.run(Pipeline().normalize(loaded), config)
    val env = SqlEvalEnv(resolveContext(req, loaded), config, data = req.data, terminologyBinding = req.terminology,
      defineParameters = ParameterScope.of(loaded))
    val lowered = SqlFrameLowering.lower(optimized, env, req.target)
    // EMIT-UDF-QUALIFY: scope the UDF schema qualifier so every cql_* call renders qualified.
    SqlRender.withUdfSchema(req.udfSchema) {
      val statements = lowered.script.statements.map(statementOut)
      SqlCompileResult(statements, lowered.script.target, lowered.names, shapesOf(lowered))
    }
  }

  /** EMIT-SQL-VALUE-COLUMN: the per-define read contract, one entry per lowered define in Schedule order
    * (the same order as `SqlCompileResult.defines`). `SqlNames.obj` and `SqlNames.rel` are the same
    * sanitized identifier (EMIT-SQL-DROP-REL-PREFIX), so one call names either kind's object. */
  private def shapesOf(lowered: SqlFrameLowering.SqlLoweredLibrary): Seq[DefineShape] =
    lowered.names.map(n => DefineShape(
      define = n,
      // EMIT-SQL-OBJECT-PREFIX: name it in the SAME namespace the script emitted under. This is the
      // value the client reads back to find a define's object, so it must never be re-derived bare.
      obj    = SqlNames.obj(n, lowered.objectPrefix),
      kind   = if (lowered.relationDefines.contains(n)) DefineShape.Relation else DefineShape.Scalar))

  /** Render one statement. */
  private def statementOut(s: SqlStmt): SqlStatementOut =
    SqlStatementOut(s.name, kindOf(s), SqlRender.render(s))

  private def kindOf(s: SqlStmt): String = s match {
    case _: SqlStmt.CreateView     => "view"
    case _: SqlStmt.CacheTable     => "cache_table"
  }

  /** The compile-once PIPELINE PLAN (`PIPE-SQL-DAG-REUSE`): lower the WHOLE library ONCE in pipeline-cut
    * mode (`SqlEvalEnv.cutSiblings` on — every reference to a materialized sibling lowers straight to
    * `FROM cql_<sibling>`), then partition the one resulting DAG into a [[DatasetRecipe]] per materialized
    * define. The cut set is exactly the materialized boundaries the SINGLE lowering discovered
    * (`lowered.materializedDefines`). Pure (no
    * Spark), like [[compileSql]] / [[LibraryDescribe]]; GENERATION mode by default (the classifier chooses
    * the boundaries), or RUNTIME when the caller pins `req.boundaries`. */
  def compilePipeline(request: CompileRequest): PipelinePlan = {
    val req = prepareRequest(request)
    // PIPE-DURABLE-IDENTITY: stable per-definition seams even when a later definition adds a reader.
    require(!req.durable || req.boundaries.isEmpty, "durable plans use all definition boundaries")
    // EMIT-ROOT-INDEPENDENT: a durable plan cuts at every define, and never at a shared retrieve read, whose
    // selection counts call sites across the whole closure being compiled.
    val overrides =
      if (req.durable) req.overrides ++ Map("materialize.mode" -> "all", "materialize.retrieves" -> "none")
      else req.overrides
    val base   = Config.resolve(overrides).copy(grainFirst = req.grainFirst,
      // EMIT-SQL-OBJECT-PREFIX: the request field is the explicit tier over the
      // `emit.object_prefix` override; absent leaves the config default (empty = bare names).
      objectPrefix = req.objectPrefix.getOrElse(Config.resolve(req.overrides).objectPrefix))
    // EMIT-ROOT-INDEPENDENT: every define is named by the library that declares it, the root's own included,
    // so a define's recipe is the same whichever library is being compiled.
    val linked = normalizedInput(req)
    val loaded = LibraryLinker.ownerQualified(linked)
    val ownName: Map[String, String] = loaded.owners.collect {
      case (merged, owner) if owner.library == loaded.id => owner.name -> merged
    }
    val config = req.boundaries.map(b => base.copy(materializeBoundaries = Some(b.map(n => ownName.getOrElse(n, n)))))
      .getOrElse(base)
    val optimized = Optimize.run(Pipeline().normalize(loaded), config)
    // Build the dataset naming context (PIPE-EMIT-SQL): library identity + optional parameter hash.
    val dsCtx = SqlNames.DatasetNameContext(loaded.id, loaded.version.getOrElse(""),
      if (req.durable) "" else SqlNames.paramHash(req.parameters), loaded.owners)
    // Pipeline-cut mode ON: the empty set is only the SIGNAL — `SqlFrameLowering.lower` rebinds the
    // resolvers to its own GROWING `matDefs`, so ONE pass both discovers the boundaries and cuts references.
    val env = SqlEvalEnv(resolveContext(req, loaded), config, cutSiblings = Some(mutable.Set.empty[String]),
      datasetNameCtx = Some(dsCtx), data = req.data, terminologyBinding = req.terminology,
      defineParameters = ParameterScope.of(loaded))
    // EMIT-UDF-QUALIFY: scope the UDF schema qualifier so every cql_* call renders qualified.
    SqlRender.withUdfSchema(req.udfSchema) {
      val plan = cutPlan(SqlFrameLowering.lower(optimized, env), loaded.id, loaded.version.getOrElse(""), dsCtx)
      if (!req.durable) plan
      else {
        val volatileKinds = Set("Now", "Today", "TimeOfDay", "Random")
        val volatileDefinitions = optimized.statements.collect {
          case definition: ExpressionDef if definition.expression.exists {
            case expression: Expr => volatileKinds(expression.elmType)
            case _ => false
          } => definition.name
        }.toSet
        val dependencies = com.databricks.cql.verify.DefineGraph.dependencies(optimized)
        val volatility = mutable.Map.empty[String, Boolean]
        def isVolatile(name: String): Boolean = volatility.getOrElseUpdate(name,
          volatileDefinitions(name) || dependencies.getOrElse(name, Set.empty).exists(isVolatile))
        val nonReusable = dependencies.keys.filter(isVolatile).map(SqlNames.datasetName(_, dsCtx)).toSet
        // CLIENT-RESOLVER-IDENTITY: a kept result is identified by the identities of the resolvers whose relations
        // this compile binds. CQL that retrieves nothing binds no data, and lowering already refused a retrieve the
        // request does not bind (BIND-CALL-SITE).
        val identities = (relations: Seq[RetrieveRelation]) => relations.map(r => r.identity.getOrElse(
          throw new IllegalArgumentException(s"a durable compile's relation ${r.table} has no identity " +
            "(CLIENT-RESOLVER-IDENTITY)"))).distinct.sorted
        // DATA-MODEL-SUBSET: the shapes of the types this compile retrieves, not of every type the request sends.
        val retrieved = env.callSites.values.filter(_.kind == CallSite.Retrieve).map(_.request("fhirType").str).toSet
        val bound = DurablePlan.Providers(identities(req.data.map(_.relations).getOrElse(Nil)),
          identities(req.terminology.map(_.relations).getOrElse(Nil)),
          DurablePlan.shapeIdentities(req.data.map(_.shapes).getOrElse(Map.empty).filter(s => retrieved(s._1))))
        DurablePlan.annotate(plan, nonReusable, DurablePlan.parameterClaims(loaded, dsCtx), bound,
          DurablePlan.valueSetClaims(loaded, dsCtx))
      }
    }
  }

  /** Partition ONE pipeline-cut lowering into per-materialized-define recipes (`PIPE-SQL-DAG-REUSE`). For
    * each materialized define D: walk its producer statement's `TableRef` dependencies, INCLUDING every
    * non-materialized dependency view (re-inlined) and STOPPING at a materialized sibling's published
    * dataset (`cql_<sibling>` — recorded as a declared `read`, never descended into). The base view is
    * shared, so it is re-inlined into every recipe that needs it. */
  /** Resolve the ELM origin coordinates (library, version, raw define name) for a define name: the library
    * that declares `d` and its name there, from `owners` (EMIT-ROOT-INDEPENDENT). */
  private def resolveOrigin(d: String, rootLibrary: String, rootVersion: String,
                            owners: Map[String, com.databricks.cql.ir.StatementOwner]): (String, String, String) =
    owners.get(d).map(o => (o.library, o.version, o.name)).getOrElse((rootLibrary, rootVersion, d))

  def cutPlan(lowered: SqlFrameLowering.SqlLoweredLibrary,
              rootLibrary: String = "", rootVersion: String = "",
              dsCtx: SqlNames.DatasetNameContext = SqlNames.DatasetNameContext("", "")): PipelinePlan = {
    val matDefs  = lowered.materializedDefines
    val relDefs  = lowered.relationDefines
    def producerName(d: String): String =
      if (relDefs.contains(d)) SqlNames.rel(d, lowered.objectPrefix)
      else SqlNames.obj(d, lowered.objectPrefix)

    val publishedToDefine: Map[String, String] = matDefs.iterator.map(d => SqlNames.datasetName(d, dsCtx) -> d).toMap
    // DATA-RETRIEVE-BARRIER-OPT: materialized READS are published datasets too, so a consuming recipe must
    // CUT at them exactly as it cuts at a materialized define — the read's published name becomes a
    // declared `read` (the SDP dependency edge) instead of the walk descending into the read's own body.
    val readPublished: Map[String, String] = // local object -> published dataset name
      lowered.materializedReads.iterator.map(s => s.obj -> SqlNames.datasetName(s.obj, dsCtx)).toMap
    val publishedNames: Set[String] = publishedToDefine.keySet ++ readPublished.values
    val stmtByName: Map[String, SqlStmt] = lowered.script.statements.iterator.map(s => s.name -> s).toMap
    val stmtOrder: Seq[String] = lowered.script.statements.map(_.name) // a valid topological order
    // EMIT-ROOT-INDEPENDENT: the compiled library's own defines are published by their authored names, an
    // included library's by their merged `<library>.<define>` names, as before owner naming.
    def authored(d: String): String = dsCtx.owners.get(d).filter(_.library == rootLibrary).map(_.name).getOrElse(d)

    def recipe(d: String): DatasetRecipe = {
      val included = mutable.LinkedHashSet.empty[String] // local statements in D's sub-DAG (topo-ordered below)
      val reads    = mutable.LinkedHashSet.empty[String] // published sibling datasets D reads (the SDP edges)
      def visit(name: String): Unit =
        if (publishedNames.contains(name)) reads += name              // cut: a materialized sibling / read → declared read
        else if (stmtByName.contains(name) && !included.contains(name)) {
          included += name                                            // local: a non-materialized view / the base / D's producer
          SqlRender.tableRefs(stmtByName(name)).foreach(visit)
        }
        // else: an external relation (a bound retrieve or terminology read) the runtime provides — left as-is.
      visit(producerName(d))

      val ordered = stmtOrder.filter(included.contains).map(stmtByName)
      // The DECLARATIVE form for a Spark Declarative Pipeline body: collapse the recipe's view/cache
      // statements into ONE `WITH <name> AS (<rel>), … SELECT * FROM <target>` query (no DDL). The
      // statements are already in dependency order, so each CTE references only earlier CTEs + external
      // reads (`<sibling>` / a bound retrieve relation) — a valid sequential `WITH`.
      val ctes = ordered.collect {
        case SqlStmt.CreateView(n, r) => n -> r
        case SqlStmt.CacheTable(n, r) => n -> r
      }
      val query = SqlRender.renderRel(
        SqlRel.With(ctes, SqlRel.Select(Seq(SqlRel.Proj.star), from = SqlRel.TableRef(producerName(d)))), 0)
      val (lib, libVer, rawDefine) = resolveOrigin(d, rootLibrary, rootVersion, dsCtx.owners)
      val dsName = SqlNames.datasetName(d, dsCtx)
      // PIPE-EMIT-SQL: the deploy-ready SDP SQL statement — CREATE OR REFRESH MATERIALIZED VIEW for
      // materialized defines (all recipes in the current cut are materialized; non-materialized defines
      // are re-inlined into their consumers and do not appear as recipes).
      val originComment = if (lib.nonEmpty) {
        val ver = if (libVer.nonEmpty) s" v$libVer" else ""
        s"-- Library: $lib$ver | Define: $rawDefine\n"
      } else ""
      val sqlFile = s"${originComment}CREATE OR REFRESH MATERIALIZED VIEW $dsName\nAS\n$query"
      DatasetRecipe(
        name           = dsName,
        target         = producerName(d),
        // PIPE-READS-SORTED: a set of dependency edges, emitted sorted. The walk's first-seen order follows the
        // SQL's shape, which a folded parameter (the period) changes; the edges do not.
        reads          = reads.toSeq.sorted,
        statements     = ordered.map(statementOut),
        query          = query,
        library        = lib,
        libraryVersion = libVer,
        define         = rawDefine,
        sqlFile         = sqlFile,
        // EMIT-SQL-VALUE-COLUMN: publish the row shape + payload column of the dataset this recipe creates.
        kind           = if (relDefs.contains(d)) DefineShape.Relation else DefineShape.Scalar,
        valueColumn    = SystemColumns.Val)
    }

    /** DATA-RETRIEVE-BARRIER-OPT: one recipe per materialized READ. Structurally the same cut as a define's
      * — walk the statement's dependencies, stop at anything published — but a read reads only the retrieve
      * mount and the terminology table, both EXTERNAL, so its `reads` is empty and it is a source node of
      * the dataset graph. That is precisely what makes it shareable: N measures that issue the same read
      * declare the same source dataset, and a scheduler deduplicates it into one task. */
    def readRecipe(s: RetrieveReads.Read): DatasetRecipe = {
      val included = mutable.LinkedHashSet.empty[String]
      val reads    = mutable.LinkedHashSet.empty[String]
      def visit(name: String): Unit =
        if (publishedNames.contains(name)) reads += name
        else if (stmtByName.contains(name) && !included.contains(name)) {
          included += name
          SqlRender.tableRefs(stmtByName(name)).foreach(visit)
        }
      visit(s.obj)

      val ordered = stmtOrder.filter(included.contains).map(stmtByName)
      val ctes = ordered.collect {
        case SqlStmt.CreateView(n, r) => n -> r
        case SqlStmt.CacheTable(n, r) => n -> r
      }
      val query = SqlRender.renderRel(
        SqlRel.With(ctes, SqlRel.Select(Seq(SqlRel.Proj.star), from = SqlRel.TableRef(s.obj))), 0)
      val dsName = SqlNames.datasetName(s.obj, dsCtx)
      val c = s.coords
      val comment = s"-- Read: ${c.resourceType}${c.codeProperty.map(p => s" [$p]").getOrElse("")}" +
        s" | consumed by: ${s.defines.map(authored).mkString(", ")}\n"
      DatasetRecipe(
        name           = dsName,
        target         = s.obj,
        reads          = reads.toSeq.sorted, // PIPE-READS-SORTED
        statements     = ordered.map(statementOut),
        query          = query,
        library        = rootLibrary,
        libraryVersion = rootVersion,
        // A read is not a define and has no define name to claim. Its provenance is `retrieveRead` below;
        // leaving this empty keeps `(library, version, define)` unambiguous rather than inventing a
        // synthetic name a consumer might correlate against a real `ExpressionDef`.
        define         = "",
        sqlFile        = s"${comment}CREATE OR REFRESH MATERIALIZED VIEW $dsName\nAS\n$query",
        kind           = DefineShape.Resource,
        valueColumn    = SystemColumns.Resource,
        retrieveRead   = Some(readCoordinates(s, authored)))
    }

    // Reads first: they are the source nodes every consuming define reads, so declaring them ahead keeps
    // the dataset list in dependency order. Then one recipe per materialized define, in schedule order (a
    // stable, dependency-respecting dataset order for the pipeline to declare).
    PipelinePlan(lowered.materializedReads.map(readRecipe) ++ lowered.names.filter(matDefs.contains).map(recipe))
  }

  /** Project a lowering-side read onto the wire [[ReadCoordinates]] (`DATA-RETRIEVE-BARRIER-OPT`). The
    * code filter is flattened to a KIND plus its payload so a consumer never has to case over an IR type,
    * and an unresolvable bracket surfaces as `"other"` rather than as no filter at all. */
  private def readCoordinates(s: RetrieveReads.Read, authored: String => String): ReadCoordinates = {
    val c = s.coords
    val (kind, vsId, codes) = c.codeFilter match {
      case Some(RetrieveCatalog.ValueSet(id, _)) => ("valueset", Some(id), Nil)
      case Some(RetrieveCatalog.Codes(cs, _))    => ("codes", None, cs)
      case Some(RetrieveCatalog.Other(_))        => ("other", None, Nil)
      case None                                  => ("none", None, Nil)
    }
    ReadCoordinates(
      resourceType        = c.resourceType,
      profile             = c.profile,
      codeProperty        = c.codeProperty,
      codeFilterKind      = kind,
      valueSetId          = vsId,
      codes               = codes,
      dateProperty        = c.dateProperty,
      dateFiltered        = c.dateFiltered,
      periodProperty      = c.periodWindow.map(_._1),
      periodLowInclusive  = c.periodWindow.map(_._2),
      periodHighExclusive = c.periodWindow.map(_._3),
      consumedBy          = s.defines.map(authored),
      callSites           = s.callSites)
  }

  /** Resolve the EFFECTIVE CQL context (`LANG-CONTEXT-DEFAULT`) by precedence: the EXPLICIT request context
    * (`req.context`), else the library's own DECLARED `context` (`lib.contexts.head` — the ELM context
    * statement), else `Unfiltered` (the CQL Author's Guide spec fallback). A context-free `define` thus
    * lowers to the data-free one-subject frame, never a forced `Patient` retrieve; a library that DECLARES
    * `context Patient` still compiles Patient-scoped when the request omits one; an explicit request context
    * always wins. (The model-default tier is deferred — no model declares one to the SQL back end today.) */
  def resolveContext(req: CompileRequest, lib: Library): Context =
    Context.named(req.context
      .orElse(lib.contexts.headOption.map(_.name))
      .getOrElse(Context.Unfiltered.name))

  /** The ELM for a request: `elm` verbatim, or `cql` TEXT translated in-process (`CqlToElmCli`,
    * `TECH-ELM-INPUT`). A translation failure is loud (`CORE-NO-SILENT-MISLOWER`).
    *
    * `depCql` (`BUILD-COMPILE-LINK`) is registered as EXTRA translation sources, so a root whose
    * `include`s the caller resolved translates against that closure instead of failing on "Could not load
    * source for library …". This is what lets a pure-Python caller compile a multi-library measure: it
    * resolves the file set (`BUILD-LIBRARY-PROVIDER`), FHIRHelpers among it when the root includes it
    * (`LANG-FHIRHELPERS-FROM-CONTENT`), and the service still owns translation.
    *
    * Translation runs ONCE: the previous form called `translateText` a second time just to build the
    * error message, doubling the cost of the failure path for a large measure. */
  def elmOf(req: CompileRequest): String = req.cql match {
    case Some(text) =>
      val result = CqlToElmCli.translateText(text, req.depCql)
      result.elmJson.getOrElse(throw new IllegalArgumentException(
        s"CQL translation produced errors: ${result.errors.mkString("; ")}"))
    case None => req.elmJson
  }

  /** The LOADED library a compile normalizes: the ROOT from `elm`/`cql`, FLATTENED with any caller-supplied
    * dependency libraries (`BUILD-COMPILE-LINK`), then with any parameter-VALUE overrides applied
    * (`BUILD-CLIENT-SESSION`). Overrides are pre-seeded as parameter DEFAULTS so the existing `ParameterResolver` substitutes them —
    * the engine-supplied override seam it left open. */
  def normalizedInput(request: CompileRequest): Library = {
    val req = prepareRequest(request)
    applyParameterOverrides(linkedRoot(req), req.parameters, req.sessionParameters)
  }

  /** SVC-COMPILE-BATCH — translate once, retain the closure, and type inputs before lowering. */
  private def prepareRequest(request: CompileRequest): CompileRequest = {
    val translated = if (request.cql.isEmpty) request else {
      val result = CqlToElmCli.translateClosure(request.cql.get, request.depCql)
      val elm = result.root.elmJson.getOrElse(throw new IllegalArgumentException(
        s"CQL translation produced errors: ${result.root.errors.mkString("; ")}"))
      // A root with no caller-supplied closure keeps the existing no-link/no-prune behavior.
      val dependencies = if (request.depCql.isEmpty) Seq.empty else result.dependencies
      request.copy(cql = None, elmJson = elm, deps = request.deps ++ dependencies, depCql = Seq.empty)
    }
    BatchInputs.prepare(translated)
  }

  /** The ROOT library flattened with its caller-supplied dependency closure (`BUILD-COMPILE-LINK`,
    * `CONF-MEASURE-LINK`). When `deps` is empty this is just the single loaded library (unchanged behavior).
    * Otherwise the service MERGES the provided dependency ELMs into one self-contained library via
    * [[LibraryLinker.link]] (keyed by each dependency's own library id — the value an [[ir.IncludeDef]]
    * `path` names), then PRUNES to the requested slice ([[pruneRoots]]: the explicit `target`, else the
    * default population slice). Pruning drops every define not reached from the roots — so a dependency
    * define an unused branch never touches (with, say, an unmodeled node) never trips lowering. The service
    * does NO source resolution — a `link` over a closure the caller under-supplied fails loudly
    * (`missing dependency library …`, `CORE-NO-SILENT-MISLOWER`).
    */
  def linkedRoot(request: CompileRequest): Library = {
    val req = prepareRequest(request)
    val root = Pipeline().load(elmOf(req))
    // FHIRHelpers arrives in the closure like any included library (LANG-FHIRHELPERS-FROM-CONTENT), but it
    // is never merged: its calls are lowered directly. A closure of only FHIRHelpers has nothing to link.
    val depsById = (req.deps ++ translatedDepCql(req)).iterator.map(Pipeline().load)
      .filterNot(_.id == LibraryLinker.FhirHelpers).map(d => d.id -> d).toMap
    if (depsById.isEmpty) root
    else LibraryLinker.prune(LibraryLinker.link(root, depsById), pruneRoots(req, root))
  }

  /** The `depCql` closure translated to ELM so it can be LINKED (`BUILD-COMPILE-LINK`).
    *
    * Registering the closure as translation `extraSources` is necessary but NOT sufficient: the front end
    * emits a root that still carries an `IncludeDef` and an unresolved cross-library `ExpressionRef` — it
    * references the dependency, it does not inline it. Skipping this step compiled the root "successfully"
    * and then died in lowering with `unresolved reference` (`CORE-NO-SILENT-MISLOWER` catching it, but at
    * the wrong layer). So each dependency is translated in its own right — against the WHOLE closure, so
    * a dependency that includes another dependency also resolves — and handed to `LibraryLinker.link`
    * exactly as a caller-supplied ELM dep would be.
    *
    * A dependency that fails to translate is DROPPED here rather than raised: the root's own translation
    * already failed loudly on it if it mattered, and a closure may legitimately carry a library the root
    * never includes (`FHIRHelpers` is lowered directly, never merged). A dependency that is genuinely
    * needed and missing still fails loudly in `LibraryLinker.link` as `missing dependency library …`. */
  private def translatedDepCql(req: CompileRequest): Seq[String] =
    if (req.depCql.isEmpty) Seq.empty
    else req.depCql.flatMap(d => CqlToElmCli.translateText(d, req.depCql).elmJson)

  /** The prune roots for a linked measure: the explicit `target` when the caller names one, else the
    * measure's DEFAULT slice — the well-known population defines present in the root library
    * (`BUILD-CLIENT-POPULATIONS`), falling back to the root library's whole define surface for an ad-hoc
    * library that declares none. This mirrors the client's `populations() or defines()` default and the
    * conformance harness (which prunes to the population roots): a whole-measure `describe`/compile lowers
    * the population sub-graph, so supplemental-data-element defines outside it (e.g., a demographic attribute, a bare
    * `CodeRef` the SQL back end does not yet lower) do not block the populations a caller actually asked
    * for. Naming such a define as an explicit `target` still lowers it — and still fails loudly on the
    * unmodeled node — so the default narrows scope without hiding the gap. */
  private def pruneRoots(req: CompileRequest, root: Library): Set[String] = {
    val rootDefines = root.statements.collect { case e: ExpressionDef => e.name }.toSet
    req.target.map(Set(_)).getOrElse {
      val populations = PopulationDefineNames.filter(rootDefines.contains)
      if (populations.nonEmpty) populations.toSet else rootDefines
    }
  }

  /** The well-known eCQM population define names (`BUILD-CLIENT-POPULATIONS`) — the default whole-measure
    * slice, kept in sync with the client's `POPULATION_DEFINE_NAMES`. */
  private val PopulationDefineNames: Seq[String] = Seq(
    "Initial Population", "Denominator", "Denominator Exclusions", "Denominator Exceptions",
    "Numerator", "Numerator Exclusions", "Measure Population", "Measure Population Exclusions",
    "Measure Observation")

  /** Replace the DEFAULT of each overridden parameter with the client-supplied value (`BUILD-CLIENT-SESSION`,
    * `LANG-PARAMETER`). Naming an undeclared parameter fails loudly (`CORE-NO-SILENT-MISLOWER`) rather than
    * silently doing nothing.
    *
    * `sessionParameters` (`BUILD-CLIENT-SESSION-PARAMETERS`) are the client's session-wide values: the ones
    * this library declares are applied, beneath `parameters`; the rest are not this library's and are
    * dropped. That is a scope, not a silent miss: a session value names no particular library. */
  def applyParameterOverrides(lib: Library, parameters: Map[String, String],
                              sessionParameters: Map[String, String] = Map.empty): Library = {
    val declared = lib.parameters.map(_.name).toSet
    val effective = sessionParameters.filter { case (k, _) => declared.contains(k) } ++ parameters
    applyExplicitOverrides(lib, effective)
  }

  private def applyExplicitOverrides(lib: Library, parameters: Map[String, String]): Library = {
    if (parameters.isEmpty) lib
    else {
      val declared = lib.parameters.map(_.name).toSet
      val unknown = parameters.keySet -- declared
      if (unknown.nonEmpty)
        throw new IllegalArgumentException(
          s"parameter override(s) name undeclared parameter(s): ${unknown.toSeq.sorted.mkString(", ")} " +
            s"(declared: ${declared.toSeq.sorted.mkString(", ")})")
      val overrides = overrideExprs(parameters)
      lib.copy(parameters = lib.parameters.map(p =>
        overrides.get(p.name).map(e => p.copy(default = Some(e))).getOrElse(p)))
    }
  }

  /** Translate each `name -> CQL-expression-text` override into a LOADED IR [[Expr]] (the form parameter
    * defaults hold) by translating a synthetic one-define-per-parameter library and extracting each body.
    * Reuses the front end, so every CQL literal type (numbers, dates, intervals, quantities, codes) is
    * handled without hand-building IR literals. */
  private def overrideExprs(parameters: Map[String, String]): Map[String, Expr] = {
    val names = parameters.keys.toSeq
    val defines = names.zipWithIndex
      .map { case (n, i) => s"""define "__p$i": ${parameters(n)}""" }.mkString("\n")
    val cql = s"library __CqlParamOverrides version '1.0.0'\n\n$defines\n"
    val elm = CqlToElmCli.translateText(cql).elmJson.getOrElse(
      throw new IllegalArgumentException(
        s"could not translate parameter override value(s): " +
          CqlToElmCli.translateText(cql).errors.mkString("; ")))
    val byDefine = Pipeline().load(elm).statements.collect {
      case e: ExpressionDef => e.name -> e.expression
    }.toMap
    names.zipWithIndex.map { case (n, i) => n -> byDefine(s"__p$i") }.toMap
  }
}
