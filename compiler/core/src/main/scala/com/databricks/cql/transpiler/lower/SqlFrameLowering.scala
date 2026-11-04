package com.databricks.cql.transpiler.lower

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir.{Case => CqlCase, _}
import com.databricks.cql.passes.{ChoiceDemand, Materialize, RetrieveDemand, RetrieveReads, Schedule, SchemaPaths, TypeEncoding}
import com.databricks.cql.sema.{FhirModel, ViewSlots}
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.transpiler.emit.SqlExpr._
import com.databricks.cql.transpiler.emit.{SqlExpr, SqlNames, SqlRel, SqlRender, SqlScript, SqlStmt}

import scala.collection.mutable

/** Library lowering to a SQL SCRIPT (EMIT-SQL-EXECUTE): the SQL twin of `lower.FrameLowering`. Every
  * `define` of a normalized library becomes ONE named statement — a `CREATE OR REPLACE TEMP VIEW`
  * (default) or a `CACHE TABLE` barrier ([[SqlMaterialization]]) — in
  * `Schedule` dependency order. A define is thereby REFERENCED BY NAME (the DAG the share-free Connect
  * proto could not express): a scalar define reads its dependency views, a list define is a
  * grain-first `Relation` view of `(__subject_id, __ordinal, __val)` rows.
  *
  * WHAT IS DELIBERATELY DIFFERENT vs the compiler twin: there is no accumulating WIDE `DataFrame` frame
  * (the proto path's one-column-per-define frame) and NO `collect_list`/`localCheckpoint`/CSE machinery.
  * Each scalar define is its OWN view over the context base + its dependency views; each list define is
  * carried grain-first as a `Relation` view. Sharing is native SQL, so the boxing/hoisting the proto
  * path needed is not a port target.
  *
  * THE RETRIEVE MOUNTS are an EXTERNAL contract (like the `DataProvider`): the runtime makes the relation each
  * retrieve call site binds, and each terminology relation, available BEFORE running this script (`BIND-CALL-SITE`);
  * the transpiler references them by name ([[SqlEvalEnv.retrieveMount]]) and never emits their bodies.
  *
  * SCOPE (Phase 3b): subject-grouped contexts (Patient/Practitioner — the FHIR measures); bare-retrieve
  * and grain-first relation-query list defines (plain `return all` + single-key sort); scalar defines
  * over the base/context and prior scalar defines, with `exists`/`Count` reductions over relation defines
  * lowered as joins. Conditional/union defines, aggregate folds, ungrouped contexts, and heterogeneous
  * unions are rejected loudly (CORE-NO-SILENT-MISLOWER) — later increments. */
object SqlFrameLowering {

  private val SubjectId = SystemColumns.SubjectId
  private val ValueCol  = SystemColumns.Val
  private val BaseView  = "__frame_base"
  /** EMIT-SQL-OBJECT-PREFIX: the base view is a SESSION object like any define view, so it must be
    * namespaced too. It is the one object EVERY library emits under the SAME name, which makes it the
    * FIRST thing two libraries in one session collide on — a bare `__frame_base` from the second compile
    * silently rebinds the subject universe the first one's lazy DataFrames still read through. */
  private def baseView(prefix: String): String = SqlNames.qualify(BaseView, prefix)

  /** The whole lowered library: the ordered [[SqlScript]] plus the metadata the boundary/consumers need
    * (mirrors `LoweredFrame`). `names` is every lowered define in Schedule order (the `defines`
    * metadata); `relationDefines`/`listDefines` name the grain-first `Relation` defines; `decisions` is
    * the per-statement materialization rationale (what was staged as a barrier vs inlined, and why). */
  final case class SqlLoweredLibrary(script: SqlScript,
                                     names: Seq[String] = Nil,
                                     relationDefines: Set[String] = Set.empty,
                                     listDefines: Map[String, Boolean] = Map.empty,
                                     nullDefines: Set[String] = Set.empty,
                                     decisions: Seq[SqlMaterialization.Decision] = Nil,
                                     /** The RAW define names staged as a barrier (`CACHE TABLE`), in
                                       * Schedule order — the boundary set `LibraryDescribe` reports so a
                                       * consumer knows which defines become their own datasets. */
                                     materializedDefines: Set[String] = Set.empty,
                                     /** `DATA-RETRIEVE-BARRIER-OPT`: the FILTERED reads staged as their own
                                       * barrier objects, in emit order, each with the coordinates a
                                       * consumer needs to know which rows the object holds. Empty under
                                       * the default `materialize.retrieves = none`. */
                                     materializedReads: Seq[RetrieveReads.Read] = Nil,
                                     /** EMIT-SQL-OBJECT-PREFIX: the session-object namespace this
                                       * lowering emitted under (empty = bare names, the default). Carried
                                       * on the RESULT because every consumer that names an object —
                                       * `shapesOf`, the pipeline recipe cut — must use the same namespace
                                       * the script actually used; re-deriving it from a config the
                                       * consumer may not hold is how the two drift apart. */
                                     objectPrefix: String = "")

  /** Lower `lib` to a SQL script. `target` names the define the script produces (defaults to the last
    * scheduled define); the runtime `SELECT`s it after running the statements. */
  def lower(lib: Library, env0: SqlEvalEnv, target: Option[String] = None): SqlLoweredLibrary = {
    val tctx   = TypeEncoding.ctxOf(lib)
    val typeOf: Expr => CqlType = e => TypeEncoding.typeOf(e, tctx)
    // DATA-PATH-IN-SCHEMA, DATA-MODEL-SUBSET: an element the bound data's shape leaves out fails here, not when
    // the SQL runs; choice dispatch below reads only the slots the shapes hold.
    SchemaPaths.check(lib, typeOf, env0.shapes)
    ViewSlots.withShapes(env0.shapes.map(_.values))(lowerChecked(lib, env0, target, typeOf))
  }

  private def lowerChecked(lib: Library, env0: SqlEvalEnv, target: Option[String],
                           typeOf: Expr => CqlType): SqlLoweredLibrary = {
    // Thread the pure-IR materialization-policy inputs from the single source (`passes.Materialize`),
    // and the het-`Choice` demand (ARCH-RESOURCE-HANDLE) from `passes.ChoiceDemand` — both Spark-free.
    // DATA-RETRIEVE-BARRIER-OPT: the FILTERED reads to give their own object, under the optional
    // `materialize.retrieves` policy. Empty under the default (`none`), which keeps the script
    // byte-identical for every existing caller.
    val reads = RetrieveReads.selected(lib, env0.config)
    val env = env0.copy(
      reuseCounts    = Materialize.reuseCounts(lib),
      skipUnfiltered = Materialize.skipUnfilteredNames(lib, env0.config),
      choiceDemand   = ChoiceDemand.analyze(lib, typeOf),
      // EMIT-SQL-RETRIEVE-DEMAND-BOUND: the fields navigated anywhere in the linked library (per resource
      // TYPE — the mount is shared), used to bound a demand-pruned mount.
      retrieveDemand = RetrieveDemand.analyze(lib, typeOf),
      // Every node that reads a selected read resolves to that read's object at the retrieve seam.
      materializedReads = reads.flatMap(s => s.nodes.map(_ -> s.obj)).toMap)

    val contextNames = lib.contexts.map(_.name).toSet
    val defines = Schedule
      .order(lib.statements.collect { case e: ExpressionDef => e })
      .filterNot(d => contextNames.contains(d.name))

    val stmts        = mutable.ArrayBuffer.empty[SqlStmt]
    val decisions    = mutable.ArrayBuffer.empty[SqlMaterialization.Decision] // per-statement materialization rationale
    val scalarDefs   = mutable.LinkedHashSet.empty[String] // defines carried as a scalar view (__subject_id, __val)
    val relDefs      = mutable.LinkedHashSet.empty[String] // defines carried as a grain-first Relation view
    val listMeta     = mutable.LinkedHashMap.empty[String, Boolean]
    val nullDefs     = mutable.LinkedHashSet.empty[String] // relation defines with a nullity sidecar view (null_<name>)
    val matDefs      = mutable.LinkedHashSet.empty[String] // RAW define names staged as a barrier (the boundary set describe reports)

    // The base relation `__frame_base`, a named view every scalar define reads for the subject universe:
    //   - subject-grouped context -> the per-subject context resource, carrying the
    //     context struct (`Patient`);
    //   - ungrouped (Unfiltered/Population) -> a single synthetic-subject row (ARCH-SET-EVAL), the
    //     degenerate one-subject case, seeded with no data (`SELECT '*' AS __subject_id FROM (SELECT 1)`).
    val baseRel = SqlQueryLowering.contextRel(env, lib).getOrElse(
      SqlRel.Select(Seq(SqlRel.Proj(str(SystemColumns.UnfilteredSubject), SubjectId)),
        from = SqlRel.AliasRel(SqlRel.OneRow, "__one")))
    stmts += SqlStmt.CreateView(baseView(env.config.objectPrefix), baseRel)
    decisions += SqlMaterialization.Decision(baseView(env.config.objectPrefix), "view", "base")

    // DATA-RETRIEVE-BARRIER-OPT: one barrier per selected FILTERED read, emitted BEFORE any define. A read
    // reads only the retrieve mount and the terminology table — never a define — so the whole set can sit
    // at the top of the script in first-occurrence order, and every define below it reads the object
    // instead of re-inlining the mount call. The body is built with `filteredRetrieveInline` so it does
    // not consult the substitution map and read itself.
    reads.foreach { s =>
      val rel = SqlQueryLowering.filteredRetrieveInline(
        s.nodes.head, s.coords.resourceType, env, lib, typeOf)
      val (stmt, kind) = SqlMaterialization.statement(s.obj, rel, materialize = true)
      stmts += stmt
      decisions += SqlMaterialization.Decision(s.obj, kind, readReason(s))
    }

    // Emit a define's statement with its statement KIND driven by the SEMANTIC classifier
    // (EXEC-MATERIALIZE-FILTER): a define whose body FILTERS ANYWHERE — a scan-filtered retrieve, or a
    // query with a where/with/without — stages as a barrier, as does a measure population; a define that
    // prunes nothing (bare passthru, pushdown-fed, plain scalar) stays a logical view. Records the
    // single-term decision for the report.
    // DATA-MATERIALIZE-POLICY: honor the materialize MODE the SQL path had ignored. `all` forces EVERY
    // define to a cache_table barrier (no logical views) — the diagnostic that rules out cross-view
    // Catalyst re-analysis as a hang/perf cause by materializing each step before the next reads it;
    // `none` forces all views. `costly` (default) keeps the structural [[classify]] decision.
    def withMode(c: (Boolean, String)): (Boolean, String) = env.config.materializeMode.key match {
      case "all"  => if (c._1) c else (true, "mode-all")
      case "none" => (false, "mode-none")
      case _      => c
    }
    def emit(name: String, rel: SqlRel, expr: Expr): Unit = {
      val (mat0, term0) = withMode(classify(name, expr, relDefs.toSet))
      val (mat, term) = sizeGate(rel, mat0, term0)
      val (stmt, kind) = SqlMaterialization.statement(relName(name, env.config.objectPrefix), rel, mat)
      stmts += stmt; decisions += SqlMaterialization.Decision(relName(name, env.config.objectPrefix), kind, term)
      if (mat) matDefs += name
    }

    defines.foreach { d =>
      // ARCH-SQL-CSE: a FRESH hoist-column counter per define so `__h<N>` names stay small/readable and
      // are unique across this define's (possibly nested) projection levels (SqlHoist).
      // PIPE-SQL-DAG-REUSE: in pipeline-cut mode, bind the reference resolvers to the GROWING `matDefs`
      // set so a reference to an already-materialized sibling resolves to its published dataset. `matDefs`
      // is complete for every define this one depends on (Schedule lowers dependencies first), so ONE pass
      // both discovers the boundaries and cuts the references. Inert (byte-identical) off the pipeline path.
      val e = env.copy(currentDefine = Some(d.name),
        hoistCounter = new java.util.concurrent.atomic.AtomicInteger(0),
        cutSiblings = if (env.cutSiblings.isDefined) Some(matDefs) else None,
        // LANG-QUERY-WHERE over a define DAG: the scalar defines lowered so far (grown in dependency order,
        // like `matDefs`), so a relation query in THIS define can resolve a scalar-sibling reference by
        // LEFT-joining its per-subject view ([[SqlQueryLowering.combined]]).
        scalarDefs = scalarDefs)
      // Attribute a rejected node to the OWNING define (a bare UnsupportedNode names only the node family,
      // which is ambiguous in a 40-define measure); this is the single diagnostic that turns "some
      // InValueSet somewhere" into "define X's InValueSet" while porting the Phase-3 cut-points.
      try
      d.expression match {
        // Bare retrieve list define (`define E: [Encounter]`) → a grain-first Relation view.
        case r: Retrieve =>
          emit(d.name, SqlQueryLowering.retrieveRelationFrame(r, e, lib, typeOf), d.expression)
          relDefs += d.name; listMeta += (d.name -> false)

        // List-valued Relation query define → a grain-first Relation view (plain `return all`, or a
        // single-key `sort` via a window re-ordinal). Aggregate / multi-key sort fold on the eager box
        // path (not yet ported), so they are rejected loudly.
        case q: Query if isRelationQuery(q, relDefs.toSet) && typeOf(q).isInstanceOf[CqlType.ListType] =>
          val rel =
            if (q.aggregate.isEmpty && q.sort.isEmpty) SqlQueryLowering.relationFrameDirect(q, e, lib, typeOf, relDefs.toSet)
            else if (q.aggregate.isEmpty && q.sort.sizeIs == 1) SqlQueryLowering.relationFrameSortedDirect(q, e, lib, typeOf, relDefs.toSet)
            else throw UnsupportedNode("Query", "aggregate / multi-key-sort list defines arrive on the eager box path (later increment)")
          emit(d.name, rel, d.expression)
          relDefs += d.name; listMeta += (d.name -> q.returnDistinct)

        // `aggregate` Relation query folds to a per-subject SCALAR (LANG-QUERY-AGGREGATE): the combined
        // rows collect into one ordered element array per subject and the CQL fold runs as a Spark
        // `aggregate` HOF, LEFT-joined onto the base so every subject folds (absent → the seed).
        case q: Query if isRelationQuery(q, relDefs.toSet) && q.aggregate.isDefined =>
          val agg = SqlQueryLowering.relationAggregate(q, SqlRel.TableRef(baseView(env.config.objectPrefix)), e, lib, typeOf, relDefs.toSet)
          // EMIT-SQL-VALUE-COLUMN: the fold already lands in `__val` — project it through UNCHANGED (a
          // scalar define publishes the same value column as a relation define, so no rename here).
          val rel = SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(Col(ValueCol))),
            from = asFrom(agg, "__agg"))
          val (mat0, term0) = withMode(classify(d.name, q, relDefs.toSet))
          val (mat, term) = sizeGate(rel, mat0, term0)
          val (stmt, kind) = SqlMaterialization.statement(defName(d.name, env.config.objectPrefix), rel, mat)
          stmts += stmt; decisions += SqlMaterialization.Decision(defName(d.name, env.config.objectPrefix), kind, term)
          if (mat) matDefs += d.name
          scalarDefs += d.name

        // Conditional-as-union over relations (ARCH-COND-UNION): a LIST-valued `if`/`case` whose leaves
        // are all relation-valued (or absent) and share ONE element type (Phase A) — or are all
        // Choice-encodable SCALARS (Phase B, het-scalar) — lowers to a UNION of the branch relations,
        // each gated by `cqlTrue(condition)` pushed in as a per-subject semi-join, staying grain-first
        // (NO collect_list box). A NULL leaf feeds the per-subject nullity sidecar (`null_<name>`,
        // ARCH-LIST-NULL-SIDECAR) that `X is null` reads. Het-RESOURCE-body branches need the model's
        // Spark DataType for tagging (not Spark-free) and are a later increment — rejected by
        // [[conditionalUnionizable]] (falls through to the loud rejection below).
        case cond @ (_: If | _: CqlCase)
            if e.config.optimizeConditionalUnion &&
              isListLikeConditional(typeOf(cond)) && conditionalUnionizable(cond, relDefs.toSet, typeOf) =>
          val (unionRel, distinct, sidecar) =
            withConditionalUnion(d.name, cond, e, lib, typeOf, relDefs.toSet, scalarDefs.toSet)
          emit(d.name, unionRel, d.expression)
          relDefs += d.name; listMeta += (d.name -> distinct)
          sidecar.foreach { s =>
            stmts += SqlStmt.CreateView(nullName(d.name, env.config.objectPrefix), s)
            decisions += SqlMaterialization.Decision(nullName(d.name, env.config.objectPrefix), "view", "null_sidecar")
            nullDefs += d.name
          }

        // A list-valued `union` DEFINE over branches of DIFFERENT element types (LANG-CHOICE-RESOURCE),
        // e.g. `[Encounter] union [Condition]` typed `List<Choice<Encounter, Condition>>`: union the
        // per-arm relations, each arm's `__val` wrapped in a discriminated Choice struct whose resource
        // member slot is the demand-pruned HANDLE (identity + navigated fields, ARCH-RESOURCE-HANDLE).
        case u @ Union(_, _)
            if containsRetrieve(u) && typeOf(u).isInstanceOf[CqlType.ListType] && isHetChoiceUnion(u, typeOf) =>
          emit(d.name, hetUnionRel(d.name, u, e, lib, typeOf, relDefs.toSet), d.expression)
          relDefs += d.name; listMeta += (d.name -> false)

        case cond @ (_: If | _: CqlCase) if containsRetrieve(cond) =>
          throw UnsupportedNode(cond.elmType, "retrieve-rooted conditional define arrives in a later increment")

        // A SAME-TYPE relation `union` define (LANG-LIST set union) — the het-Choice case matched above, so
        // every arm shares one element type (e.g. two `[USCoreBloodPressureProfile]` BP queries). Realize
        // as the DISTINCT UNION ALL of the arm relations (`branchRelation`'s Union case), set semantics by
        // full-row dedup (each arm's `__ordinal` is the resource identity, so a shared element dedups).
        case u: Union if containsRetrieve(u) && typeOf(u).isInstanceOf[CqlType.ListType] =>
          emit(d.name, branchRelation(u, e, lib, typeOf, relDefs.toSet), d.expression)
          relDefs += d.name; listMeta += (d.name -> true)
        case u: Union if containsRetrieve(u) =>
          throw UnsupportedNode("Union", "non-list / key-only relation union define arrives in a later increment")

        // Scalar define: its own view over the base/context + dependency views, with exists/Count/is-null
        // reductions over relation defines lowered as joins. Subject-grained — a logical view UNLESS it
        // is a measure POPULATION (Denominator/Numerator/…), which always stages as a barrier.
        case scalar =>
          val rel = scalarRel(d.name, scalar, e, lib, typeOf, scalarDefs.toSet, relDefs.toSet, listMeta.toMap, nullDefs.toSet)
          val (mat0, term0) = withMode(classify(d.name, scalar, relDefs.toSet))
          val (mat, term) = sizeGate(rel, mat0, term0)
          val (stmt, kind) = SqlMaterialization.statement(defName(d.name, env.config.objectPrefix), rel, mat)
          stmts += stmt; decisions += SqlMaterialization.Decision(defName(d.name, env.config.objectPrefix), kind, term)
          if (mat) matDefs += d.name
          scalarDefs += d.name
      }
      catch { case u: UnsupportedNode => throw u.copy(detail = s"[define '${d.name}'] ${u.detail}") }
    }

    // SQL SIZE GATE (EMIT-SQL-EXECUTE): reject a script whose WITHIN-DEFINE inlining exploded — a define
    // that re-inlines a temporal/interval/value[x] tower dozens of times because a repeated scalar
    // subexpression inside ONE SELECT is textually duplicated (SQL views share ACROSS defines, not WITHIN
    // one body; the real fix is a within-define CTE CSE — not yet built). Estimated STRUCTURALLY
    // (`SqlRender.sizeOf`, no giant-string materialization), so we fail fast at ~O(nodes) BEFORE a
    // hundreds-of-MB script is rendered or shipped to Spark (CORE-NO-SILENT-MISLOWER). Per-statement
    // limit `-Dcqldatabricks.sqlMaxStmtBytes` (default 4 MB); raise it to inspect a blown-up dump.
    val maxStmtBytes = sys.props.get("cqldatabricks.sqlMaxStmtBytes")
      .flatMap(s => scala.util.Try(s.toLong).toOption).getOrElse(4000000L)
    val oversize = stmts.iterator.map(s => s.name -> SqlRender.sizeOf(s)).filter(_._2 > maxStmtBytes).toSeq
    if (oversize.nonEmpty) {
      val worst = oversize.sortBy(-_._2).take(5).map { case (n, b) => f"$b%,d B  $n" }.mkString("; ")
      throw UnsupportedNode("SqlScript",
        f"emit-size gate: ${oversize.size} statement(s) exceed the ${maxStmtBytes}%,d B per-statement limit " +
          f"(within-define inlining blowup — needs a CTE CSE pass). Largest: $worst")
    }

    val tgt = target.orElse(defines.lastOption.map(_.name))
      .map(n => if (relDefs.contains(n)) relName(n, env.config.objectPrefix) else defName(n, env.config.objectPrefix))
      .getOrElse(baseView(env.config.objectPrefix))
    val script  = SqlScript(stmts.toSeq, tgt)
    SqlLoweredLibrary(script, defines.map(_.name), relDefs.toSet, listMeta.toMap,
      nullDefines = nullDefs.toSet, decisions = decisions.toSeq, materializedDefines = matDefs.toSet,
      materializedReads = reads, objectPrefix = env.config.objectPrefix)
  }

  /** The single-term diagnostics REASON for a materialized read (`DATA-RETRIEVE-BARRIER-OPT`), in the same
    * one-word vocabulary the define decisions use so the report reads uniformly. The `read-` prefix keeps
    * a read's row distinguishable from the define that consumes it, which may carry the same term. */
  private def readReason(s: RetrieveReads.Read): String =
    if (s.coords.codeFilter.isDefined) "read-valueset"
    else if (s.coords.dateFiltered || s.coords.periodWindow.isDefined) "read-date"
    else "read-profile"

  /** A scalar define as a `SELECT __subject_id, <expr> AS <name> FROM __frame_base [LEFT JOIN deps …]`:
    * scalar dependency defines are joined by name, and `exists`/`Count`/`is null` reductions over
    * relation defines become presence/count/nullity join columns (the SQL twin of `FrameLowering`'s
    * scalar `withDefine` path, MINUS the hoist/materialize machinery). */
  private def scalarRel(name: String, body: Expr, env: SqlEvalEnv, lib: Library, typeOf: Expr => CqlType,
                        scalarDefs: Set[String], relDefs: Set[String], listMeta: Map[String, Boolean],
                        nullDefs: Set[String]): SqlRel = {
    val ctxName    = env.context.name
    val san        = name.replaceAll("[^A-Za-z0-9]", "_")
    val joins      = mutable.ArrayBuffer.empty[SqlRel]      // relations to LEFT JOIN USING(__subject_id)
    val resolveEx  = mutable.LinkedHashMap.empty[String, SqlExpr] // synthetic col -> resolved (coalesced) expr
    var k = 0
    def fresh(kind: String): String = { val c = s"__${kind}_${san}_$k"; k += 1; c }

    // A per-subject presence boolean: LEFT JOIN the distinct-subject set of `present` under a flag column,
    // coalescing absent → false. Memoized by `key` so a repeated reduction joins once.
    val memo = mutable.LinkedHashMap.empty[String, String]
    def flagFrom(key: String, present: SqlRel): String = memo.getOrElseUpdate(key, {
      val c = fresh("exists")
      joins += SqlRel.AliasRel(
        SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(bool(true), c)), from = asFrom(present, "__pr")),
        s"__j${joins.size}")
      resolveEx(c) = Call("coalesce", Seq(Col(c), bool(false)))
      c
    })
    def countFrom(defn: String): String = memo.getOrElseUpdate(s"count:$defn", {
      val c = fresh("count")
      // CQL `Count` is the number of NON-NULL elements; a `return distinct` list counts DISTINCT non-null
      // `__val` (listMeta). `count(DISTINCT __val)` is SQL keyword syntax, not a nested function call, so
      // it is emitted as a pre-formed token (the argument column is a safe `__` identifier).
      val agg = if (listMeta.getOrElse(defn, false)) Call("count", Seq(Lit(s"DISTINCT $ValueCol"))) else Call("count", Seq(Col(ValueCol)))
      joins += SqlRel.AliasRel(
        SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(Cast(agg, "INT"), c)),
          from = env.relRef(defn), groupBy = Seq(Col(SubjectId))),
        s"__j${joins.size}")
      resolveEx(c) = Call("coalesce", Seq(Col(c), int(0)))
      c
    })
    def distinctSubjects(defn: String): SqlRel =
      SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId))), from = env.relRef(defn), distinct = true)

    // A relation define consumed as a WHOLE LIST by a scalar op (`'x' in "E"`, `"E" includes …`,
    // DATA-LIST-ENCODING): BOX it to one per-subject ordered array (`collect_list` of `(ordinal, val)`
    // sorted by ordinal → the value; `array_distinct` for a `return distinct` define), absent → empty
    // array. The eager `Array` boundary the grain-first path defers until a whole-list operator forces it.
    // BOX a per-subject grain-first relation (`source`, projecting __subject_id/__ordinal/__val) into one
    // ordered array per subject — the shared body of `boxedListRef` (a relation DEFINE by name) and
    // `boxedInlineQuery` (an inline relation query nested in a scalar expression, e.g. `Last([R] … sort …)`).
    // Each boxed array's ELEMENT list type, so a reference to the box (`ExpressionRef(__box_n)`) — and an
    // enclosing `Last(box)`/singleton-source query over it — can resolve the element's FHIR type for
    // value[x] dispatch (`bodyTypeOf`, below). Boxing to a synthetic ref otherwise loses the type
    // (`Last( [Observation: "…"] … ) L where L.value ~ …`).
    val boxTypes = mutable.LinkedHashMap.empty[String, CqlType]
    def boxRel(key: String, source: SqlRel, distinct: Boolean, boxType: CqlType = CqlType.AnyType): Expr = {
      val nm = memo.getOrElseUpdate(key, {
        val c = fresh("box")
        val ordStruct = Struct(Seq("o" -> Col("__ordinal"), "v" -> Col(ValueCol)))
        val cmp = Lambda(Seq("__l", "__r"), SqlExpr.Case(Seq(
          Infix("<", Field(Col("__l"), "o"), Field(Col("__r"), "o")) -> int(-1),
          Infix(">", Field(Col("__l"), "o"), Field(Col("__r"), "o")) -> int(1)), Some(int(0))))
        val arr0 = Call("transform", Seq(
          Call("array_sort", Seq(Call("collect_list", Seq(ordStruct)), cmp)),
          Lambda(Seq("__e"), Field(Col("__e"), "v"))))
        val arr = if (distinct) Call("array_distinct", Seq(arr0)) else arr0
        joins += SqlRel.AliasRel(
          SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(arr, c)),
            from = asFrom(source, fresh("__boxsrc")), groupBy = Seq(Col(SubjectId))),
          s"__j${joins.size}")
        resolveEx(c) = Call("coalesce", Seq(Col(c), Call("array", Nil)))
        c
      })
      if (boxType != CqlType.AnyType) boxTypes(nm) = boxType
      ExpressionRef(nm, None)
    }

    def boxedListRef(defn: String): Expr =
      boxRel(s"box:$defn", env.relRef(defn), listMeta.getOrElse(defn, false))

    // An INLINE relation query nested in a scalar expression (a retrieve/relation source query used as an
    // operand — e.g. `Last( [Observation] X where … sort by … )`): lower it to a grain-first relation and
    // box it to a per-subject array, so the enclosing First/Last/singleton-source query flows through the
    // Array path. Single-key sort re-ordinals via a window; aggregate / multi-key sort are not yet boxed.
    def boxedInlineQuery(q: Query): Expr = {
      val rel =
        if (q.aggregate.isEmpty && q.sort.isEmpty) SqlQueryLowering.relationFrameDirect(q, env, lib, typeOf, relDefs)
        else if (q.aggregate.isEmpty && q.sort.sizeIs == 1) SqlQueryLowering.relationFrameSortedDirect(q, env, lib, typeOf, relDefs)
        else throw UnsupportedNode("Query", "inline relation query with aggregate / multi-key sort in scalar position is not yet boxed")
      // Carry the query's LIST type so a `Last(box)`/singleton-source over the box resolves the element
      // FHIR type for value[x] dispatch (LANG-CHOICE-MODEL).
      boxRel(s"boxq:${q.toString}", rel, q.returnDistinct, typeOf(q))
    }

    // `X is null` over a relation define (ARCH-LIST-NULL-SIDECAR, LANG-LIST-NULL-STATE): true iff the
    // define's nullity sidecar (`null_<n>`) marks the subject null. An empty list is empty-not-null
    // (false), and a NON-nullable relation define has no sidecar, so it is never null (the literal
    // `false`). The per-subject `__is_null` is left-joined (absent → false) and read back as a column.
    def nullStateRef(defn: String): Expr =
      if (!nullDefs.contains(defn)) Literal(FhirBool, Some("false"))
      else ExpressionRef(memo.getOrElseUpdate(s"isnull:$defn", {
        val c = fresh("isnull")
        joins += SqlRel.AliasRel(
          SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(Col("__is_null"), c)),
            from = SqlRel.TableRef(nullName(defn, env.config.objectPrefix))),
          s"__j${joins.size}")
        resolveEx(c) = Call("coalesce", Seq(Col(c), bool(false)))
        c
      }), None)

    // Rewrite the reductions over relation defines to references to their (joined) columns.
    val rewritten = Expr.coerce(body.transformDown {
      case Exists(ExpressionRef(n, None)) if relDefs.contains(n) =>
        ExpressionRef(flagFrom(s"ref:$n", distinctSubjects(n)), None)
      case ex @ Exists(r: Retrieve) =>
        ExpressionRef(flagFrom(ex.toString, SqlQueryLowering.subjectsPresent(r, env, lib, typeOf)), None)
      case ex @ Exists(q: Query) if isRelationQuery(q, relDefs) =>
        ExpressionRef(flagFrom(ex.toString, SqlQueryLowering.relationExists(q, env, lib, typeOf, relDefs)), None)
      case Count(ExpressionRef(n, None)) if relDefs.contains(n) =>
        ExpressionRef(countFrom(n), None)
      case IsNull(ExpressionRef(n, None)) if relDefs.contains(n) =>
        nullStateRef(n)
      // LANG-AGGREGATE-FUNCTIONS: an aggregate over a relation define reduces its boxed array, and the box carries
      // the define's list type, which picks the aggregate's lowering (a date Max, a quantity Sum).
      case AggregateFunction(op, src @ ExpressionRef(n, None)) if relDefs.contains(n) =>
        AggregateFunction(op, boxRel(s"box:$n", env.relRef(n), listMeta.getOrElse(n, false), typeOf(src)))
      // A bare reference to a RELATION define in scalar position — a whole-list consumer (`in`/`includes`/
      // a list-valued arg): BOX it to a per-subject array (the reductions above matched first, top-down).
      case ExpressionRef(n, None) if relDefs.contains(n) =>
        boxedListRef(n)
      // An INLINE relation query in scalar position (not exists/count, which matched above) — e.g. the
      // source of `Last(…)` or a singleton-source outer query: box it to a per-subject array so the
      // enclosing First/Last/where flows through the Array path (LANG-QUERY / DATA-LIST-ENCODING).
      case q: Query if isRelationQuery(q, relDefs) =>
        boxedInlineQuery(q)
    })

    // Scalar dependency defines the (rewritten) body references — joined by name so `resolveRef` reads
    // their column.
    val refs = mutable.LinkedHashSet.empty[String]
    rewritten.foreach {
      case ExpressionRef(n, None) if scalarDefs.contains(n)  => refs += n
      case _ =>
    }
    refs.foreach(n => joins += scalarDepJoin(env, n))

    val resolveRef: SqlColumnLowering.RefResolver = n =>
      resolveEx.getOrElse(n,
        if (n == ctxName) Col(ctxName)
        else if (scalarDefs.contains(n)) Col(SqlNames.obj(n))
        else SqlColumnLowering.rejectRefs(n))

    val from = joins.foldLeft(SqlRel.TableRef(baseView(env.config.objectPrefix)): SqlRel) { (acc, j) =>
      SqlRel.Join(acc, j, SqlRel.JoinKind.LeftOuter, using = Seq(SubjectId))
    }
    // ARCH-SQL-CSE: hoist the body's duplication-prone temporal/interval operands to `__h<N>` columns over
    // the join frame, so the scalar expression references each ONCE by name rather than re-inlining the
    // tower (the within-define CTE CSE the emit-size gate flags when absent). `*` carries the subject +
    // context + dependency columns the expression reads.
    // A BOXED inline query became a synthetic `ExpressionRef(__box_n)` with no model type; resolve it to
    // the query's recorded LIST type so an enclosing `Last(box)`/singleton-source query (and the value[x]
    // dispatch on its alias) types correctly (LANG-CHOICE-MODEL). Inert when nothing was boxed.
    val bodyTypeOf: Expr => CqlType =
      if (boxTypes.isEmpty) typeOf
      else { case ExpressionRef(n, None) if boxTypes.contains(n) => boxTypes(n); case other => typeOf(other) }
    // LANG-CHOICE-MODEL: a value[x] read off a define reference that holds one resource has no query
    // alias for the query-path rewrites to key on, so dispatch it here on the reference's static type.
    val typed = SqlQueryLowering.rewriteTypedChoiceValueX(rewritten, bodyTypeOf)
    val (fromH, expr) = SqlHoist.hoisted(from, env.hoistCounter)(h =>
      SqlColumnLowering.lower(typed, resolveRef, typeOf = bodyTypeOf, hoist = h, hoistT = h,
        temporalUdf = true))
    // EMIT-SQL-VALUE-COLUMN: a scalar define publishes `(__subject_id, __val)` — the SAME value column a
    // grain-first relation define publishes, so a consumer's read is kind-agnostic. `name` names the OBJECT
    // only; it is never part of the result schema.
    SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(expr, ValueCol)), from = asFrom(fromH, "__h"))
  }

  // ===========================================================================
  // Conditional-as-union (ARCH-COND-UNION) — the SQL twin of `FrameLowering.withConditionalUnion` +
  // its helpers, re-targeted `DataFrame`/`Column` → `SqlRel`/`SqlExpr`. The proto-tree gate/frame
  // accumulation collapses to a single per-subject GATE FRAME (base + condition-presence joins); each
  // present leaf's branch relation is semi-joined to the subjects its gate selects, then UNION ALL'd.
  // ===========================================================================

  private val FhirBool = "{urn:hl7-org:elm-types:r1}Boolean"

  /** Lower a LIST-valued `if`/`case` to a UNION of its relation-valued branches (ARCH-COND-UNION,
    * LANG-3VL). Returns `(unionRel, distinct, sidecar)`: the grain-first relation
    * (`__subject_id`+`__ordinal`+`__val`), the driver-side dedup flag, and — when a `null` leaf exists —
    * the per-subject nullity sidecar (`__subject_id`+`__is_null`) that `X is null` reads.
    *
    * Each condition lowers to a per-subject boolean over a GATE FRAME (the context base left-joined to
    * each `exists`/reduction's presence flag and each scalar-define view). `cqlTrue(C)` (`C <=> true`)
    * and its negation partition every subject, so a subject lands in exactly ONE leaf — the UNION needs
    * NO dedup. Nested `if`/`case` expand to a nested union (bounded by `conditional_union_max_depth`);
    * `case` gates branch i by `(not w_1) AND … AND w_i` (first-match). */
  private def withConditionalUnion(name: String, expr: Expr, env: SqlEvalEnv, lib: Library,
                                   typeOf: Expr => CqlType, relDefs: Set[String],
                                   scalarDefs: Set[String]): (SqlRel, Boolean, Option[SqlRel]) = {
    val ctxName   = env.context.name
    val maxDepth  = env.config.conditionalUnionMaxDepth
    val joins     = mutable.ArrayBuffer.empty[SqlRel]            // presence/def joins onto the base
    val memo      = mutable.LinkedHashMap.empty[String, SqlExpr] // condition -> its resolved per-subject boolean
    val scalarRefs = mutable.LinkedHashSet.empty[String]         // scalar defines a condition references
    var k = 0
    def fresh(): String = { val c = s"__cu_${name.replaceAll("[^A-Za-z0-9]", "_")}_$k"; k += 1; c }

    // A presence flag: LEFT JOIN the distinct-subject set of `present` under a flag column, absent → false.
    def flag(key: String, present: SqlRel): SqlExpr = memo.getOrElseUpdate(key, {
      val c = fresh()
      joins += SqlRel.AliasRel(
        SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(bool(true), c)), from = asFrom(present, "__pr")),
        s"__j${joins.size}")
      Call("coalesce", Seq(Col(c), bool(false)))
    })
    val resolveRef: SqlColumnLowering.RefResolver = n =>
      if (n == ctxName) Col(ctxName)
      else if (scalarDefs.contains(n)) { scalarRefs += n; Col(SqlNames.obj(n)) }
      else SqlColumnLowering.rejectRefs(n)

    // A per-subject CONDITION boolean over the gate frame (raw 3VL; the caller applies cqlTrue).
    def condition(e: Expr): SqlExpr = e match {
      case Exists(r: Retrieve)                          => flag(e.toString, SqlQueryLowering.subjectsPresent(r, env, lib, typeOf))
      case Exists(q: Query) if isRelationQuery(q, relDefs) => flag(e.toString, SqlQueryLowering.relationExists(q, env, lib, typeOf, relDefs))
      case Exists(ExpressionRef(n, None)) if relDefs.contains(n) =>
        flag(s"ref:$n", SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId))), from = env.relRef(n), distinct = true))
      case And(a, b) => Infix("AND", condition(a), condition(b))
      case Or(a, b)  => Infix("OR", condition(a), condition(b))
      case Not(o)    => Prefix("NOT", condition(o))
      case scalar    => SqlColumnLowering.lower(scalar, resolveRef, typeOf = typeOf,
        temporalUdf = true)
    }
    def cqlTrue(c: SqlExpr): SqlExpr = Infix("<=>", c, bool(true))

    // Expand the conditional to (gate, leaf) pairs, threading the gate SqlExpr; presence joins accumulate
    // in `joins` as `condition` is called.
    def leaves(e: Expr, gate: SqlExpr, depth: Int): Seq[(SqlExpr, Expr)] = {
      if (depth > maxDepth)
        throw UnsupportedNode(e.elmType,
          s"conditional-as-union nesting for define '$name' exceeds optimize.conditional_union_max_depth=$maxDepth")
      e match {
        case If(c, t, el) =>
          val ct = cqlTrue(condition(c))
          leaves(t, Infix("AND", gate, ct), depth + 1) ++ leaves(el, Infix("AND", gate, Prefix("NOT", ct)), depth + 1)
        case CqlCase(comparand, items, elseExpr) =>
          val cmp = comparand.map(condition)
          var neg = gate
          val acc = mutable.ArrayBuffer.empty[(SqlExpr, Expr)]
          items.foreach { it =>
            val wc = condition(it.when)
            val whenTrue = cqlTrue(cmp.fold(wc)(c => Infix("=", c, wc)))
            acc ++= leaves(it.thenExpr, Infix("AND", neg, whenTrue), depth + 1)
            neg = Infix("AND", neg, Prefix("NOT", whenTrue))
          }
          acc.toSeq ++ leaves(elseExpr, neg, depth + 1)
        case leaf => Seq((gate, leaf))
      }
    }
    val leafGates = leaves(expr, bool(true), 0)

    // Phase A vs B: the ROW-PRODUCING leaves' element types. If they differ, tag each branch's `__val`
    // with the active Choice member so the per-branch schemas align (scalar members only here — resource
    // bodies are the later increment, excluded by conditionalUnionizable).
    val present  = leafGates.filterNot { case (_, b) => isAbsentLeaf(b) }
    val members  = present.flatMap { case (_, b) => branchElemType(b, typeOf) }.distinct
    val tagged   = members.size > 1

    // The gate frame: the base left-joined to every presence flag and referenced scalar-define view. Each
    // scalar dependency's uniform `__val` is renamed to its object name in the join (EMIT-SQL-VALUE-COLUMN,
    // [[scalarDepJoin]]) so N dependencies stay N distinct columns for `resolveRef`.
    scalarRefs.foreach(n => joins += scalarDepJoin(env, n))
    val gateFrame = joins.foldLeft(SqlRel.TableRef(baseView(env.config.objectPrefix)): SqlRel) { (acc, j) =>
      SqlRel.Join(acc, j, SqlRel.JoinKind.LeftOuter, using = Seq(SubjectId))
    }

    // Realize + gate each present leaf, then UNION ALL. Gating is a LEFT SEMI JOIN to the subjects whose
    // gate is true (evaluated over the gate frame).
    val demand = env.choiceDemand.get(name).map(_.fields).getOrElse(Set.empty)
    val gatedParts = present.map { case (gate, branch) =>
      val br0 = branchRelation(branch, env, lib, typeOf, relDefs)
      val member = branchElemType(branch, typeOf).get
      val br = if (!tagged) br0 else SqlRel.Select(
        Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(Col("__ordinal"), "__ordinal"),
            SqlRel.Proj(SqlColumnLowering.choiceTagArm(Col(ValueCol), member, members, demand, true), ValueCol)),
        from = asFrom(br0, "__b"))
      val keep = SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId))), from = asFrom(gateFrame, "__g"), where = Some(gate))
      SqlRel.Join(asFrom(br, "__leaf"), SqlRel.AliasRel(keep, "__keep"), SqlRel.JoinKind.LeftSemi, using = Seq(SubjectId))
    }
    val distinct = leafGates.exists { case (_, b) => leafDistinct(b) }

    // ARCH-LIST-NULL-SIDECAR: a subject is null iff its (single) selected leaf is a NULL leaf — the OR of
    // the null leaves' gates over the gate frame. Only when a null leaf exists (empty-list is empty-not-null).
    val nullGates = leafGates.collect { case (g, b) if isNullLeaf(b) => g }
    val sidecar = if (nullGates.isEmpty) None else Some(SqlRel.Select(
      Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(nullGates.reduce((a, b) => Infix("OR", a, b)), "__is_null")),
      from = asFrom(gateFrame, "__g")))

    val unionRel = gatedParts match {
      case Seq(one) => one
      case many     => SqlRel.UnionAll(many)
    }
    (unionRel, distinct, sidecar)
  }

  /** Realize a relation-valued leaf branch as a `Relation` frame (`__subject_id`+`__ordinal`+`__val`) —
    * the SQL twin of `FrameLowering.branchRelation`. Unwraps a Phase-B `As(…, choiceTypes)` first. */
  private def branchRelation(expr: Expr, env: SqlEvalEnv, lib: Library, typeOf: Expr => CqlType,
                             relDefs: Set[String]): SqlRel = branchInner(expr) match {
    case r: Retrieve                                   => SqlQueryLowering.retrieveRelationFrame(r, env, lib, typeOf)
    case ExpressionRef(n, None) if relDefs.contains(n) => env.relRef(n)
    case q: Query                                      => SqlQueryLowering.relationFrameDirect(q, env, lib, typeOf, relDefs)
    // A CONSTANT list branch (`else { -1 }` / `then { 'has' }`): one row per (subject, element) via a
    // LATERAL VIEW posexplode of the literal array across the subject universe — a cheap constant explode,
    // NOT a per-subject collect box. `__ordinal` is the zero-padded position (matching a sibling
    // retrieve/query arm's resource-id `__ordinal` so the union aligns).
    case ListExpr(els) =>
      val arr = Call("array", els.map(e => SqlColumnLowering.lower(e, typeOf = typeOf,
        temporalUdf = true)))
      SqlRel.Select(
        projections = Seq(
          SqlRel.Proj(Col(SubjectId)),
          SqlRel.Proj(Call("format_string", Seq(str("%04d"), Col("__pos"))), "__ordinal"),
          SqlRel.Proj(Col(ValueCol), ValueCol)),
        from = SqlRel.LateralView(SqlRel.TableRef(baseView(env.config.objectPrefix)), Call("posexplode", Seq(arr)), "__cl",
          Seq("__pos", ValueCol)))
    case Union(a, b) =>
      SqlRel.Select(projections = Nil, distinct = true,
        from = asFrom(SqlRel.UnionAll(Seq(
          branchRelation(a, env, lib, typeOf, relDefs), branchRelation(b, env, lib, typeOf, relDefs))), "__u"))
    case other => throw UnsupportedNode(other.elmType, "ARCH-COND-UNION branch must be relation-valued")
  }

  // --- heterogeneous-resource union DEFINE (LANG-CHOICE-RESOURCE) ---

  private def flattenUnion(e: Expr): Seq[Expr] = e match {
    case Union(a, b) => flattenUnion(a) ++ flattenUnion(b)
    case other       => Seq(other)
  }
  private def flatResourceMembers(t: CqlType): Seq[CqlType] = t match {
    case CqlType.ListType(el)   => flatResourceMembers(el)
    case CqlType.ChoiceType(ms) => ms.flatMap(flatResourceMembers)
    case n: CqlType.NamedType   => Seq(n)
    case other                  => Seq(other)
  }
  /** The distinct flat Choice member types across a union's arms, deduped BY choiceTag so a degenerate
    * `Choice<Condition, Condition>` (two profiles of one FHIR resource) collapses to one member — the
    * homogeneous slot layout every arm's `choiceTagArm` must agree on (SqlColumnLowering.dedupByTag). */
  private def unionMembers(u: Union, typeOf: Expr => CqlType): Seq[CqlType] =
    SqlColumnLowering.dedupByTag(flattenUnion(u).flatMap(a => flatResourceMembers(typeOf(branchInner(a)))))
  /** A union whose arms carry ≥2 distinct element types — realized as a Choice-tagged relation. */
  private def isHetChoiceUnion(u: Union, typeOf: Expr => CqlType): Boolean =
    unionMembers(u, typeOf).sizeIs > 1

  /** A list-valued `union` DEFINE over DIFFERENT element types (`[Encounter] union [Condition]`): union
    * the per-arm relations, each arm's `__val` wrapped in a discriminated Choice struct (resource member
    * = the demand-pruned identity HANDLE). Arm order is LEFT-then-RIGHT (the arm index prefixes each
    * `__ordinal` so the driver-side assembly yields CQL `union` order). The SQL twin of the compiler's
    * het-`Choice` union-define path (`FrameLowering`, `tagChoiceArms`/`tagChoiceHandles`). */
  private def hetUnionRel(name: String, u: Union, env: SqlEvalEnv, lib: Library, typeOf: Expr => CqlType,
                          relDefs: Set[String]): SqlRel = {
    val members = unionMembers(u, typeOf)
    val demand  = env.choiceDemand.get(name).map(_.fields).getOrElse(Set.empty)
    val armRels = flattenUnion(u).zipWithIndex.map { case (arm, i) =>
      // collapseChoice: a `Choice<Condition, Condition>` arm (two profiles of one resource) tags as its
      // single representative `Condition`, matching the deduped `members` slot — NOT the degenerate
      // `Choice<Condition, Condition>` tag that matches no slot (SqlColumnLowering.collapseChoice).
      val member = branchElemType(arm, typeOf).map(SqlColumnLowering.collapseChoice).getOrElse(
        throw UnsupportedNode("Union", s"union arm of define '$name' is not list-typed"))
      val br = branchRelation(arm, env, lib, typeOf, relDefs)
      SqlRel.Select(
        projections = Seq(
          SqlRel.Proj(Col(SubjectId)),
          SqlRel.Proj(Call("concat", Seq(str(f"$i%04d|"), Cast(Col("__ordinal"), "STRING"))), "__ordinal"),
          SqlRel.Proj(SqlColumnLowering.choiceTagArm(Col(ValueCol), member, members, demand, true), ValueCol)),
        from = asFrom(br, "__arm"))
    }
    SqlRel.UnionAll(armRels)
  }

  // --- conditional-union predicates (mirror FrameLowering) ---

  private def isListLikeConditional(t: CqlType): Boolean = t match {
    case _: CqlType.ListType    => true
    case CqlType.ChoiceType(ms) => ms.nonEmpty && ms.forall(_.isInstanceOf[CqlType.ListType])
    case _                      => false
  }

  private def branchInner(e: Expr): Expr = e match {
    case As(inner, _, _, cts) if cts.nonEmpty => inner
    case other                                => other
  }
  private def branchElemType(e: Expr, typeOf: Expr => CqlType): Option[CqlType] =
    typeOf(branchInner(e)) match { case CqlType.ListType(el) => Some(el); case _ => None }

  private def conditionalLeaves(e: Expr): Seq[Expr] = e match {
    case If(_, t, el)          => conditionalLeaves(t) ++ conditionalLeaves(el)
    case CqlCase(_, items, el) => items.flatMap(i => conditionalLeaves(i.thenExpr)) ++ conditionalLeaves(el)
    case other                 => Seq(other)
  }

  /** A scalar Choice member the RELATION-level tagging can carry (primitive/temporal/quantity/interval +
    * `AnyType`, the string-backed FHIR primitive). */
  private def choiceEncodableScalar(t: CqlType): Boolean = t match {
    case CqlType.BooleanType | CqlType.IntegerType | CqlType.LongType | CqlType.DecimalType
       | CqlType.StringType | CqlType.RatioType | CqlType.AnyType => true
    case _: CqlType.DateType | _: CqlType.DateTimeType | _: CqlType.TimeType
       | _: CqlType.QuantityType | _: CqlType.IntervalType => true
    case _ => false
  }

  /** A Choice member the SQL union tagging can carry: any [[choiceEncodableScalar]] PLUS a FHIR resource
    * BODY (`NamedType`, carried as the demand-pruned identity+fields HANDLE) or a `Tuple`. A resource
    * member never spells its full struct — it is the handle, so no model Spark DataType is needed
    * (SQL UNION ALL coerces the sibling arms' null slots); a nested `Choice`/`List` member is still
    * rejected (falls through to the loud rejection). */
  private def choiceUnionEncodable(t: CqlType): Boolean = t match {
    case _: CqlType.NamedType | _: CqlType.TupleType => true
    case other                                       => choiceEncodableScalar(other)
  }

  private def relationLeaf(e: Expr, relDefs: Set[String]): Boolean = branchInner(e) match {
    case _: Retrieve            => true
    case ExpressionRef(n, None) => relDefs.contains(n)
    case Union(a, b)            => relationLeaf(a, relDefs) && relationLeaf(b, relDefs)
    case q: Query               => isRelationQuery(q, relDefs) && q.aggregate.isEmpty && q.sort.isEmpty
    case ListExpr(els)          => els.nonEmpty && !els.exists(containsRetrieve)
    case _                      => false
  }
  private def leafDistinct(e: Expr): Boolean = branchInner(e) match {
    case q: Query => q.returnDistinct
    case _        => false
  }
  private def isNullLeaf(e: Expr): Boolean = branchInner(e) match {
    case NullExpr()         => true
    case Literal(_, None)   => true
    case As(inner, _, _, _) => isNullLeaf(inner)
    case _                  => false
  }
  private def isEmptyListLeaf(e: Expr): Boolean = branchInner(e) match {
    case ListExpr(els) => els.isEmpty
    case _             => false
  }
  private def isAbsentLeaf(e: Expr): Boolean = isNullLeaf(e) || isEmptyListLeaf(e)

  /** Can this list-like `if`/`case` lower as a relation UNION (ARCH-COND-UNION)? Every row-producing leaf
    * relation-valued + list-typed, and either ONE shared element type (Phase A) or all element types
    * Choice-encodable SCALARS (Phase B). Het-RESOURCE-body branches are excluded here (the later
    * increment) so they fall through to the loud rejection rather than mis-lowering. */
  private def conditionalUnionizable(cond: Expr, relDefs: Set[String], typeOf: Expr => CqlType): Boolean = {
    val present = conditionalLeaves(cond).filterNot(isAbsentLeaf)
    present.nonEmpty && present.forall(relationLeaf(_, relDefs)) && {
      val elems = present.map(branchElemType(_, typeOf))
      elems.forall(_.isDefined) && {
        val es             = elems.flatten.distinct
        val distinctsAgree = present.map(leafDistinct).distinct.size == 1
        (es.size == 1 && distinctsAgree) ||
          (es.size > 1 && es.forall(choiceUnionEncodable))
      }
    }
  }

  /** The PLAN-SIZE barrier threshold (`SqlRender.sizeOf` units), `-Dcqldatabricks.sqlBarrierBytes` (default
    * 12,000). A define whose lowered SQL exceeds it is staged even if its semantic class says `view`. */
  private def barrierBytes: Long =
    sys.props.get("cqldatabricks.sqlBarrierBytes").flatMap(s => scala.util.Try(s.toLong).toOption).getOrElse(12000L)

  /** OR the SEMANTIC materialization decision with a PLAN-SIZE gate (EMIT-SQL-EXECUTE). A define whose
    * lowered SQL exceeds [[barrierBytes]] materializes as a `CACHE TABLE` regardless of its class: a big
    * boolean/scalar helper (an OR-of-temporal-`exists` exclusion) left as a
    * `CREATE TEMP VIEW` re-inlines its WHOLE plan into every consumer — Catalyst re-analyzes the view text
    * per reference (a temp view is NOT a barrier) — and the combined analyzer plan is what stalls the
    * server. Staging it cuts that lineage: the define is analyzed + executed ONCE and read downstream as a
    * cached relation. The reason term becomes `size` when the gate (not the semantic class) forces it — the
    * SQL twin of the proto path's structural-cost materialization the semantic classifier had dropped. */
  private def sizeGate(rel: SqlRel, mat: Boolean, term: String): (Boolean, String) =
    if (mat) (true, term)
    else if (SqlRender.sizeOf(rel) > barrierBytes) (true, "size")
    else (false, term)

  // --- the SEMANTIC materialization classifier (EXEC-MATERIALIZE-FILTER) ---

  /** `(materialize?, single-term reason)` for a define. The rule is deliberately DEAD SIMPLE
    * (`EXEC-MATERIALIZE-FILTER`): **if it filters, it materializes** — plus measure populations, which
    * are the requested outputs and always stage.
    *
    * The predecessor classifier matched only the define body's TOP node (a bare `Retrieve`, or a `Query`
    * whose OWN clauses it inspected), so a filter one level down was invisible: `exists (<filtered
    * query>)`, `First(<filtered query>)`, `A or B` over filtered `exists`, and a `union` of value-set
    * retrieves all classified `scalar` → a logical view. Across a four-measure corpus that missed 16 of
    * 59 defines; 12 were staged anyway by the PLAN-SIZE gate ([[sizeGate]]) and 4 were not — which also
    * left the boundary set `describe` reports dependent on `-Dcqldatabricks.sqlBarrierBytes`, a tuning knob it
    * has no business being coupled to. Scanning the WHOLE body removes both problems: a define's barrier
    * is decided by what it DOES, not by how big it happens to render.
    *
    * "Filters" means a construct that PRUNES ROWS, which is exactly two things (see [[filterReason]]): a
    * `Retrieve` carrying a scan filter (value set / code, folded date range, period scan window, or a
    * `LANG-RETRIEVE-PROFILE` constraint), or a `Query` with a `where` / `with` / `without`. What sits
    * INSIDE the `where` (a temporal Allen relation, an `InValueSet`, an arbitrary predicate) needs no
    * separate test — the `where` IS the filter. A bare scalar comparison outside any query prunes no rows
    * and stays a view.
    *
    * A reference to a sibling define is a LEAF here (`ExpressionRef` carries no filter inside it), so a
    * define that merely passes an already-filtered upstream through keeps its `pushdown` view: the filter
    * is not this define's to own, and re-staging the pruned rows is pure waste (`DATA-SKIP-UNFILTERED`).
    *
    * Reason terms, in priority order: `population`, `valueset`, `date`, `profile`, `filter` (staged);
    * `passthru`, `pushdown`, `scalar` (view). The term is diagnostic only — the boolean is what emits. */
  private def classify(name: String, expr: Expr, relDefs: Set[String]): (Boolean, String) =
    if (isPopulation(name)) (true, "population")
    else filterReason(expr) match {
      case Some(term) => (true, term)
      case None       => (false, unfilteredTerm(expr, relDefs))
    }

  /** The STRONGEST row-pruning construct anywhere in a define body, or `None` when the body prunes
    * nothing. Priority orders the REASON only (any hit materializes), so the decision report names the
    * most informative cause when a define carries several. */
  private def filterReason(expr: Expr): Option[String] = {
    var vs, date, profile, filter = false
    expr.foreach {
      case r: Retrieve =>
        if (r.codes.isDefined) vs = true
        if (r.dateRange.isDefined || r.periodScanFilter.isDefined) date = true
        if (r.profileConstraint.isDefined) profile = true
      case q: Query =>
        if (q.where.isDefined || q.relationships.nonEmpty) filter = true
      case _ => ()
    }
    if (vs) Some("valueset")
    else if (date) Some("date")
    else if (profile) Some("profile")
    else if (filter) Some("filter")
    else None
  }

  /** The diagnostic term for a define that prunes NOTHING: `passthru` (a bare unfiltered retrieve,
    * DATA-SKIP-UNFILTERED), `pushdown` (a query reading an already-staged sibling define), or `scalar`
    * (a subject-grained scalar/reduction). All three stay logical views. */
  private def unfilteredTerm(expr: Expr, relDefs: Set[String]): String =
    if (retrieveOf(expr).isDefined) "passthru"
    else expr match {
      case q: Query if q.sources.exists(s => s.expr match {
            case ExpressionRef(n, None) => relDefs.contains(n)
            case _                      => false
          }) => "pushdown"
      case _ => "scalar"
    }

  /** A measure population by name (the CQL population defines) — always materialized. */
  private def isPopulation(name: String): Boolean = {
    val n = name.toLowerCase
    Seq("initial population", "denominator", "numerator", "measure population", "eligible").exists(n.contains)
  }

  /** The retrieve a define body scans, seeing through a Choice-construct `As` wrapper; `None` if the body
    * is not a (possibly-wrapped) bare retrieve. */
  private def retrieveOf(e: Expr): Option[Retrieve] = e match {
    case r: Retrieve                          => Some(r)
    case As(inner, _, _, cts) if cts.nonEmpty => retrieveOf(inner)
    case _                                    => None
  }

  // --- small local predicates (mirror FrameLowering) ---

  private def isRelationQuery(q: Query, relDefs: Set[String]): Boolean =
    q.sources.nonEmpty && q.sources.forall(s => SqlQueryLowering.isRelationSource(s.expr, relDefs))

  private def containsRetrieve(expr: Expr): Boolean = expr.exists { case _: Retrieve => true; case _ => false }

  private def asFrom(rel: SqlRel, alias: String): SqlRel = rel match {
    case t: SqlRel.TableRef => t
    case other              => SqlRel.AliasRel(other, alias)
  }

  /** The view/table name of a scalar define — its CQL name SANITIZED to a plain identifier (`SqlNames`,
    * EMIT-SQL-EXECUTE: one stable object namespace for the by-name DAG and the client name contract). The
    * value column it projects is the uniform `__val` (EMIT-SQL-VALUE-COLUMN), not the define name. */
  private def defName(name: String, prefix: String): String = SqlNames.obj(name, prefix)

  /** EMIT-SQL-VALUE-COLUMN — a scalar dependency define presented as a JOIN atom that RENAMES its uniform
    * `__val` to that define's object name:
    *
    * {{{ LEFT JOIN (SELECT __subject_id, __val AS <obj(n)> FROM <obj(n)>) AS __scdef_<n> USING (__subject_id) }}}
    *
    * WHY THE RENAME LIVES HERE. Every define publishes its payload as `__val`, so a frame that reads N
    * dependency defines would otherwise carry N columns all named `__val` — ambiguous the moment the body
    * references one. Renaming INSIDE each join subquery makes the names distinct BEFORE anything above can
    * see them, which keeps the resolver a plain `Col(SqlNames.obj(n))` and the join-frame namespace exactly
    * what it was when the define name was the published column.
    *
    * WHY NOT ALIAS THE JOINED RELATION instead (`LEFT JOIN <obj(n)> AS __sc0` + `__sc0.__val`): a define's
    * value projection sits OUTSIDE a derived-table wrap ([[asFrom]] at the end of [[scalarRel]]), and
    * [[SqlHoist]] re-projects the frame through `*` when it hoists — both of which flatten join-level
    * aliases out of scope and leave the duplicate `__val` names behind. This is the same reason the
    * `__is_null` sidecar is read through a renaming join rather than a qualified reference. */
  private def scalarDepJoin(env: SqlEvalEnv, n: String): SqlRel =
    SqlRel.AliasRel(
      SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(Col(ValueCol), SqlNames.obj(n))),
        from = env.scalarRef(n)),
      s"__scdef_${SqlNames.sanitize(n)}")
  /** The view/table name of a grain-first Relation define (sanitized, same namespace as scalar
    * defines — CQL names are unique so no collision; EMIT-SQL-DROP-REL-PREFIX). */
  private def relName(name: String, prefix: String): String = SqlNames.rel(name, prefix)
  /** The view name of a define's per-subject nullity sidecar (`null_<sanitized>`, ARCH-LIST-NULL-SIDECAR). */
  private def nullName(name: String, prefix: String): String = SqlNames.nullView(name, prefix)
}
