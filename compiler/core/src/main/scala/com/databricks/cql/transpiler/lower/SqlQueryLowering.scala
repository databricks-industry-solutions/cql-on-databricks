package com.databricks.cql.transpiler.lower

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir.{Case => CqlCase, _}
import com.databricks.cql.passes.RetrieveReads
import com.databricks.cql.sema.{FhirModel, ViewSlots}
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.transpiler.emit.{SqlExpr, SqlNames, SqlRel}
import com.databricks.cql.transpiler.emit.SqlExpr._

/** SQL-target query lowering (EMIT-SQL-EXECUTE): the SQL twin of the compiler's `QueryLowering`.
  *
  * INCREMENT 1 — the `Array` realization ([[array]], result is a [[SqlExpr]]): a query over an
  * ARRAY-encoded source (a System list, a singleton value) lowers to Spark higher-order functions
  * (`transform`/`filter`/`aggregate`) over the array — exactly `QueryLowering.array`, re-targeted from
  * `Column` to `SqlExpr`. Each source alias (and the aggregate accumulator) binds to a lambda VARIABLE
  * (a fresh SQL identifier), so `alias.field` in the body resolves to `<lambdaVar>.field`. This is the
  * path `SqlColumnLowering`'s `case q: Query` dispatches to.
  *
  * The `Relation` realization (a FHIR retrieve → a `SqlRel` of joins/filters/aggregates, and the
  * define-view spine of `SqlFrameLowering`) is the Phase-3 relational core, tracked separately — it is
  * coupled to the retrieve/materializer seams and is the larger remaining port. */
object SqlQueryLowering {

  private def hof(fn: String, args: SqlExpr*): SqlExpr = Call(fn, args)

  /** The `Array` realization: source(s) → `where` → (`aggregate` | `return [distinct]`) → `sort`, over
    * Spark higher-order functions. Mirrors `QueryLowering.array`. */
  def array(q0: Query,
            resolveRef: SqlColumnLowering.RefResolver,
            resolveAlias: SqlColumnLowering.RefResolver,
            typeOf: Expr => CqlType,
            temporalUdf: Boolean = false): SqlExpr = {
    if (q0.relationships.nonEmpty)
      throw UnsupportedNode("Query", "with/without over Array-encoded lists is not realized (FHIR retrieve relationships only)")
    // Resolve FHIR value[x] navigations (As/Is/ToValue/ToQuantity/subfield on a `[x]` element) against
    // the static model BEFORE lowering — the SAME pure IR->IR rewrites `combined` runs on the relation
    // path, applied here so a singleton/array query over a resource (a fluent function's `Request R`
    // inlined to an outer alias) resolves value[x] too (LANG-CHOICE-MODEL). Keyed on the source alias's
    // resource type; a non-resource source resolves to no slots, so the rewrites are a no-op.
    // Resolve each source alias's resource type with a SCOPE-AWARE typeOf so a source that navigates an
    // OUTER alias (`O.component`, an inner query over an Observation's components) types through the
    // outer alias's element type (context-free `typeOf` gives a bare `Property(outerAlias, …)` → Any).
    val stype0 = scopeTypeResolver(q0, typeOf)
    val aliasTypes = q0.sources.flatMap(s => aliasResourceType(s.expr, stype0).map(s.alias -> _)).toMap
    val hetSources = q0.sources.flatMap(s => hetAliasMembers(s.expr, typeOf).map(s.alias -> _)).toMap
    val qHet = if (hetSources.nonEmpty) rewriteHetUnionNav(q0, hetSources, flat = true) else q0
    val qSub = rewriteChoiceSubfieldValueX(qHet, aliasTypes)
    val qStat = rewriteExtensionValueX(if (hasToValueExtractor(qSub)) rewriteChoiceValueXStatic(qSub, aliasTypes) else qSub)
    // NESTED value[x] (LANG-CHOICE-MODEL): a choice reached through a `let`/alias chain (e.g.
    // `timing.repeat.bounds`) has no alias root the by-name rewrites key on — resolve it via the query scope.
    val q1   = rewriteNestedChoiceValueX(qStat, typeOf)
    val q  = q1.copy(where = q1.where.map(bindScopes), returnExpr = q1.returnExpr.map(bindScopes),
      lets = q1.lets.map(l => l.copy(expr = bindScopes(l.expr))))

    // Fresh, unique lambda-variable names — a lambda param is a SQL identifier, and nested sources must
    // not shadow. Bind alias -> Col(freshVar) in the body's scope. The base is SANITIZED to a bare SQL
    // identifier: a CQL alias like `$this` (the extension-iteration alias) would otherwise emit a lambda
    // PARAMETER `$this_1 -> …`, which Spark cannot parse (a `$` is not a valid unquoted identifier and a
    // lambda param cannot be backtick-quoted) — so strip everything but word chars.
    var counter = 0
    def fresh(base: String): String = { counter += 1; s"${base.replaceAll("[^A-Za-z0-9_]", "_")}_$counter" }

    // SCOPE-AWARE typing for the body (LANG-CHOICE-MODEL / LANG-QUERY-LET): the body references this
    // query's source aliases + `let`s (a medication-request period helper has ~15 interdependent lets), and the
    // OPERATOR DISPATCH in SqlColumnLowering keys on operand types — `startDate + Quantity(…)` is DATE
    // arithmetic only if `startDate` (a `let` = `date from …`) types as a Date, else it mis-lowers as
    // numeric/quantity `.value` addition. The context-free `typeOf` gives an alias/let-ref `Any`, so augment
    // it with the query scope (source aliases + folded let types).
    val stypeOf: Expr => CqlType = scopeTypeResolver(q, typeOf)

    // Force temporalUdf = false: query bodies lower inside higher-order function lambdas
    // (transform/filter/aggregate), and Databricks forbids external catalog UDFs inside lambda
    // expressions. The native inline form (named_struct) works everywhere.
    def lowerWith(body: Expr, binds: Map[String, SqlExpr]): SqlExpr =
      SqlColumnLowering.lower(body, resolveRef, alias => binds.getOrElse(alias, resolveAlias(alias)), stypeOf,
        temporalUdf = false)
    def srcOf(s: QuerySource, binds: Map[String, SqlExpr]): SqlExpr =
      SqlColumnLowering.lower(s.expr, resolveRef, alias => binds.getOrElse(alias, resolveAlias(alias)), typeOf,
        temporalUdf = temporalUdf)

    def projectElem(binds: Map[String, SqlExpr], aliases: Seq[String], ret: Option[Expr]): SqlExpr =
      ret match {
        case Some(r)                   => lowerWith(r, binds)
        case None if aliases.size == 1 => binds(aliases.head)
        case None                      => Struct(aliases.map(a => a -> binds(a)))
      }

    // Fold the query's `let` bindings into `binds` in dependency order (LANG-QUERY-LET): each let is
    // lowered against the source aliases + the lets already bound, then added under its own name so a
    // later let / `where` / `return` / `sort` resolves it (via QueryLetRef -> resolveAlias). Applied at
    // the innermost source scope (all aliases bound). Inlined as a sub-expression — a let referenced N
    // times expands N copies (correct; SqlHoist/CSE dedups downstream).
    def withLets(binds: Map[String, SqlExpr]): Map[String, SqlExpr] =
      q.lets.foldLeft(binds) { (b, l) => b + (l.identifier -> lowerWith(l.expr, b)) }

    // EMIT-SQL-LET-CARRIER (ARCH-SQL-CSE / LANG-QUERY-LET) — bind `let`s ONCE instead of inlining each let's expression TREE at
    // every downstream use. `withLets` above substitutes a let's whole lowered subtree wherever the let is
    // referenced; a CQL let-DAG (a medication-request period helper: `dosage → timing → boundsPeriod`, each
    // referenced by later lets and the `return`) then grows COMBINATORIALLY — `singleton from
    // R.dosageInstruction` alone re-inlined 4,542× (554 KB) in one medication define. The
    // array path has no relation to hang `SqlHoist`'s `__hN` columns on, so this is its twin: the scalar-let
    // idiom Spark SQL lacks natively — carry the bindings through a chain of `transform`s over the element
    // array, one per let. Each step CARRIES FORWARD the prior fields as cheap `Col.field` refs and APPENDS
    // the new let, so every later reference reads a struct FIELD, not a re-inlined subtree. Growth drops
    // from exponential to O(nLets²) trivial field refs. `baseArr`'s elements must already be carrier structs
    // holding `seedFields`; `outer` carries any enclosing-scope aliases through unchanged. Returns the
    // carrier array plus `mkBinds(p)` — the full name→reader map for a lambda var `p` over that array.
    // The LIVE lets — those transitively referenced from `where`/`return`/`sort`/relationship/aggregate
    // roots through other live lets. Inlining (`withLets`) drops a DEAD let for free: it is computed into
    // the binds map but, being unreferenced, never appears in the output expression Spark analyzes. The
    // carrier must reproduce that — a dead let materialized as a struct field IS analyzed, so a let whose
    // only uses were rewritten away (a let `r: ToValue(doseAndRate.dose)` whose `As(r, …)` value[x] usages
    // get folded to a direct `doseAndRate.<slot>` by `rewriteNestedChoiceValueX`, leaving the raw
    // `doseAndRate.dose` def with an UNRESOLVED `.dose` choice element) must NOT be emitted. Runs on the
    // already-rewritten `q`, so it sees the post-value[x] reference set.
    val liveLetIds: Set[String] = {
      val letMap = q.lets.map(l => l.identifier -> l.expr).toMap
      val seeds  = q.where.toSeq ++ q.returnExpr.toSeq ++ q.sort.flatMap(_.expr) ++
        q.relationships.flatMap(r => Seq(r.expr, r.suchThat)) ++
        q.aggregate.toSeq.flatMap(a => Seq(a.starting, a.body))
      val live = scala.collection.mutable.Set.empty[String]
      def visit(e: Expr): Unit =
        e.collect { case QueryLetRef(n) if letMap.contains(n) => n }.foreach(n => if (live.add(n)) visit(letMap(n)))
      seeds.foreach(visit)
      live.toSet
    }

    def letCarrier(baseArr: SqlExpr, seedFields: Seq[String],
                   outer: Map[String, SqlExpr]): (SqlExpr, String => Map[String, SqlExpr]) = {
      def mk(p: String, fs: Seq[String]): Map[String, SqlExpr] =
        outer ++ fs.map(f => f -> (Field(Col(p), f): SqlExpr))
      var arr    = baseArr
      var fields = seedFields
      q.lets.filter(l => liveLetIds(l.identifier)).foreach { l =>
        val p       = fresh("__let")
        val letExpr = lowerWith(l.expr, mk(p, fields))
        val carried = fields.map(f => f -> (Field(Col(p), f): SqlExpr)) :+ (l.identifier -> letExpr)
        arr    = hof("transform", arr, Lambda(Seq(p), Struct(carried)))
        fields = fields :+ l.identifier
      }
      (arr, (p: String) => mk(p, fields))
    }

    // The Cartesian product as one array of result elements — outer sources flatten(transform(...)),
    // the innermost filters by `where` then transforms to the result shape.
    def combine(sources: List[QuerySource], binds: Map[String, SqlExpr], aliases: Seq[String]): SqlExpr =
      sources match {
        case s :: Nil =>
          val src = srcOf(s, binds)
          val filtered = q.where.fold(src) { p =>
            val v = fresh(s.alias); hof("filter", src, Lambda(Seq(v), lowerWith(p, withLets(binds + (s.alias -> Col(v))))))
          }
          val v = fresh(s.alias)
          hof("transform", filtered, Lambda(Seq(v), projectElem(withLets(binds + (s.alias -> Col(v))), aliases, q.returnExpr)))
        case s :: rest =>
          val v = fresh(s.alias)
          hof("flatten", hof("transform", srcOf(s, binds), Lambda(Seq(v), combine(rest, binds + (s.alias -> Col(v)), aliases))))
        case Nil => throw UnsupportedNode("Query", "a query must have at least one source")
      }

    q.aggregate match {
      case Some(a) =>
        if (q.sources.size != 1)
          throw UnsupportedNode("Query.aggregate", "aggregate over a multi-source query arrives in a later increment")
        val s = q.sources.head
        val src = srcOf(s, Map.empty)
        val filtered = q.where.fold(src) { p =>
          val v = fresh(s.alias); hof("filter", src, Lambda(Seq(v), lowerWith(p, withLets(Map(s.alias -> Col(v))))))
        }
        val init = SqlColumnLowering.lower(a.starting, resolveRef, resolveAlias, typeOf, temporalUdf = temporalUdf)
        val acc = fresh(a.identifier); val x = fresh(s.alias)
        val fix = foldType(a.starting, typeOf)
        hof("aggregate", filtered, fix(init),
          Lambda(Seq(acc, x), fix(lowerWith(a.body, withLets(Map(a.identifier -> Col(acc), s.alias -> Col(x)))))))
      case None =>
        q.sources match {
          case Seq(s) if isSingletonSourceExpr(s.expr, typeOf) =>
            val v = srcOf(s, Map.empty)
            if (q.lets.isEmpty) {
              val binds = Map(s.alias -> v)
              val result = projectElem(binds, Seq(s.alias), q.returnExpr)
              q.where.fold(result)(p => SqlExpr.Case(Seq(lowerWith(p, binds) -> result), Some(Null)))
            } else {
              // The let-DAG carrier (see `letCarrier`): seed a ONE-element array holding the source, thread
              // the lets through it, then evaluate `where`/`return` over the fully-bound carrier and extract
              // the single element. Each let reference reads a field of the carrier, never a re-inlined tree.
              val seed = Call("array", Seq(Struct(Seq(s.alias -> v))))
              val (arr, mkBinds) = letCarrier(seed, Seq(s.alias), Map.empty)
              val p = fresh("__let")
              val binds = mkBinds(p)
              val result = projectElem(binds, Seq(s.alias), q.returnExpr)
              val body = q.where.fold(result)(pr => SqlExpr.Case(Seq(lowerWith(pr, binds) -> result), Some(Null)))
              Call("element_at", Seq(hof("transform", arr, Lambda(Seq(p), body)), int(1)))
            }
          case _ =>
            val aliases = q.sources.map(_.alias)
            val product = combine(q.sources.toList, Map.empty, aliases)
            val distinct = if (q.returnDistinct) hof("array_distinct", product) else product
            sorted(distinct, q.sort)
        }
    }
  }

  private def isSingletonSource(t: CqlType): Boolean = t match {
    case _: CqlType.ListType => false
    case CqlType.AnyType     => false
    case _                   => true
  }

  /** A query SOURCE that yields a SINGLE element, so the query is a scalar-source (`Case(where, elem)`)
    * not a list `filter`/`transform`. Beyond the type-based [[isSingletonSource]], a `First`/`Last`/
    * `singleton from`/`Indexer` operand is STRUCTURALLY single regardless of its (often `Any`, e.g. a
    * boxed synthetic ref) inferred type — `Last( [...] where … sort … ) L where
    * L.value ~ …`. */
  private def isSingletonSourceExpr(e: Expr, typeOf: Expr => CqlType): Boolean = e match {
    case _: First | _: Last | _: SingletonFrom | _: Indexer => true
    case As(inner, _, _, _)                                 => isSingletonSourceExpr(inner, typeOf)
    case _                                                  => isSingletonSource(typeOf(e))
  }

  private def sorted(arr: SqlExpr, sort: Seq[SortItem]): SqlExpr = sort match {
    case Nil => arr
    case Seq(SortItem(direction, None, None)) => Call("sort_array", Seq(arr, bool(direction != "desc")))
    case _ => throw UnsupportedNode("Query.sort",
      "only single ByDirection sort over Array lists is supported yet (by-expression / multi-key arrive later)")
  }

  // --- pure IR->IR rewrites copied verbatim from QueryLowering (no Column/Spark). ---

  private def bindScopes(e: Expr): Expr =
    Expr.coerce(e.transformDown { case Property(None, Some(sc), p) => Property(Some(AliasRef(sc)), None, p) })

  /** Typed-slot dispatch for a bare `extension(url).value` (e.g. a `Patient.sex` extension): resolve `.value`
    * to the model-declared typed slot for the profiled URL (us-core-sex → `valueCode`). Copied verbatim
    * from QueryLowering (pure IR→IR) so the array path never silently mislowers the extension case. */
  private def rewriteExtensionValueX(q: Query): Query = {
    def aliasOf(p: Property): Option[String] = p.scope.orElse(p.source match {
      case Some(AliasRef(a)) => Some(a)
      case _                 => None
    })
    val slots: Map[String, String] = q.where.toSeq.flatMap { w =>
      def urlAlias(x: Expr): Option[String] = x match {
        case p @ Property(_, _, "url")                                => aliasOf(p)
        case FunctionRef("ToString", Some("FHIRHelpers"), Seq(inner)) => urlAlias(inner)
        case _                                                        => None
      }
      def urlConst(x: Expr): Option[String] = x match {
        case Literal(t, Some(v)) if t.endsWith("String") => Some(v)
        case _                                           => None
      }
      def pair(l: Expr, r: Expr): Option[(String, String)] =
        for { a <- urlAlias(l); u <- urlConst(r); s <- FhirModel.profiledExtensionSlot(u) } yield a -> s
      w.collect {
        case Equal(l, r)      => pair(l, r).orElse(pair(r, l))
        case Equivalent(l, r) => pair(l, r).orElse(pair(r, l))
      }.flatten
    }.toMap
    if (slots.isEmpty) q
    else {
      def rw(e: Expr): Expr = Expr.coerce(e.transformDown {
        case p @ Property(src, scope, path)
            if aliasOf(p).exists(slots.contains) && (path == "value" || path.startsWith("value.")) =>
          Property(src, scope, slots(aliasOf(p).get) + path.drop("value".length))
      })
      q.copy(where = q.where.map(rw), returnExpr = q.returnExpr.map(rw),
        relationships = q.relationships.map(r => r.copy(suchThat = rw(r.suchThat))),
        lets = q.lets.map(l => l.copy(expr = rw(l.expr))),
        sort = q.sort.map(si => si.copy(expr = si.expr.map(rw))))
    }
  }

  // ===========================================================================
  // Relation realization (EMIT-SQL-EXECUTE): the SQL twin of QueryLowering's `Relation` half — a
  // query over FHIR retrieves, set-at-a-time per subject, lowered to a `SqlRel` of joins/filters
  // rather than a Spark `DataFrame`. A Relation source is a retrieve MOUNT ([[SqlEvalEnv.retrieveMount]]):
  // rows keyed by `__subject_id`, the resource in one STRUCT column (`__resource`). Multiple sources
  // inner-join on `__subject_id`; `where` is a correlated 3VL filter; `with`/`without` are semi-/anti-
  // joins whose `such that` may reference both sides.
  //
  // WHAT IS DELIBERATELY DROPPED vs the compiler twin: the whole `materializeCSE`/`hoistWhereQueries`/
  // `filterHoisted`/staging-barrier apparatus. Those exist ONLY because the Spark Connect proto is a
  // SHARE-FREE TREE that re-serializes a duplicated operand per placement. SQL is a DAG — a shared
  // subexpression is a CTE/column referenced by name — so that machinery is not a port target; the
  // sharing is native ([[SqlFrameLowering]] names each define a view, local CSE a `WITH`).
  // ===========================================================================

  private val SubjectId  = SystemColumns.SubjectId
  private val ValueCol   = SystemColumns.Val
  private val Resource   = SystemColumns.Resource

  /** DATA-EXPORT: a resource's identity, read from its own `id`; a resource carries no lineage field. */
  private def resourceIdOf(resource: SqlExpr): SqlExpr = Cast(Field(resource, "id"), "STRING")

  /** `{http://hl7.org/fhir}Encounter` -> `Encounter`. The single home for FHIR type-name localization
    * (mirrors `QueryLowering.localName`). */
  private[lower] def localName(dataType: String): String = dataType.substring(dataType.lastIndexOf('}') + 1)
  private def ordName(alias: String): String = s"__ord_$alias"

  /** Navigate a dotted FHIR path off a base struct: `__resource` + `code.coding` -> `__resource.code.coding`. */
  private def navPath(base: SqlExpr, path: String): SqlExpr =
    path.split("\\.").filter(_.nonEmpty).foldLeft(base)((b, seg) => Field(b, seg))

  /** EMIT-SQL-RETRIEVE-DEMAND-BOUND: the physical top-level fields (`id` + demanded slots) a
    * demand-bounded MOUNT of `rt` must carry — the union of every field navigated on this type in the
    * linked library, a `value[x]` base already expanded by `RetrieveDemand` to its typed slots. This is
    * the CONTRACT a narrow mount (the customer's typed subset, or a demand-pruned provider view) is built
    * to; the SQL back end then reads the narrow `__resource` DIRECTLY (a bare passthrough Catalyst prunes),
    * never RECONSTRUCTING a struct downstream — an explicit `named_struct` at the scan edge defeats
    * Catalyst's nested-column pruning and, materialized across a barrier, blows the plan up (a measured
    * heap regression). `None` when the type escalates to `Full` (a whole-body / het-Choice use). */
  private[lower] def demandedFields(rt: String, env: SqlEvalEnv): Option[Seq[String]] =
    env.retrieveDemand.get(rt) match {
      case Some(com.databricks.cql.passes.RetrieveDemand.Narrow(fields)) =>
        Some(fields.toSeq.sorted.flatMap { f =>
          val expanded = ViewSlots.choiceSlots(rt, f)
          if (expanded.nonEmpty) expanded else Seq(f)
        }.distinct)
      case _ => None
    }

  /** The realized combined query: the relation (rows keyed by `__subject_id`, one struct column per
    * source alias, plus `__ord_<alias>`), the per-alias bindings, the value[x]-rewritten query, and the
    * `resolveRef` for a context `ExpressionRef` reached by a clause. The SQL twin of `QueryLowering.Combined`. */
  private[lower] case class Combined(rel: SqlRel, binds: Map[String, SqlExpr], query: Query,
                                     resolveRef: SqlColumnLowering.RefResolver, typeOf: Expr => CqlType)

  /** A query source expression that resolves to a Relation frame (mirrors `QueryLowering.isRelationSource`):
    * a FHIR retrieve, a Relation-encoded list define, a Choice-construct over relation branches, a
    * same-type `union` of such, or a relation-valued sub-query. */
  private[lower] def isRelationSource(e: Expr, relDefs: Set[String]): Boolean = e match {
    case _: Retrieve                          => true
    case ExpressionRef(n, None)               => relDefs.contains(n)
    case As(inner, _, _, cts) if cts.nonEmpty => isRelationSource(inner, relDefs)
    case Union(a, b)                          => isRelationSource(a, relDefs) && isRelationSource(b, relDefs)
    case q: Query                             => q.sources.nonEmpty && q.sources.forall(s => isRelationSource(s.expr, relDefs))
    case _                                    => false
  }

  private def flattenUnion(e: Expr): Seq[Expr] = e match {
    case Union(a, b) => flattenUnion(a) ++ flattenUnion(b)
    case other       => Seq(other)
  }

  /** The (localized) FHIR resource type a source alias iterates, derived analytically (mirrors
    * `QueryLowering.aliasResourceType`), or `None` for a non-FHIR source. */
  private def aliasResourceType(e: Expr, typeOf: Expr => CqlType): Option[String] = {
    def viaType(x: Expr): Option[String] = typeOf(x) match {
      case CqlType.ListType(CqlType.NamedType(n)) => Some(FhirModel.localName(n))
      // A SINGLETON resource source (an alias bound to one resource, e.g. a fluent function's
      // `Request R` operand inlined to an outer alias) — needed to key the value[x] rewrites on the
      // Array path. A non-resource NamedType is harmless (its `choiceSlots` are empty → rewrites no-op).
      case CqlType.NamedType(n)                   => Some(FhirModel.localName(n))
      case _                                      => None
    }
    e match {
      case r: Retrieve                          => Some(localName(r.dataType))
      case As(inner, _, _, cts) if cts.nonEmpty => aliasResourceType(inner, typeOf)
      case Union(a, _)                          => aliasResourceType(a, typeOf)
      case q: Query                             => q.sources.headOption.flatMap(s => aliasResourceType(s.expr, typeOf))
      // A single-element PICK over a resource list (`Last( [Observation] … ) alias`)
      // iterates one element of the operand's type — see through to it (the operand may be a boxed synthetic
      // ref whose type is only carried by `typeOf`/`viaType`, so try the structural operand first).
      case First(o)                             => aliasResourceType(o, typeOf).orElse(viaType(e))
      case Last(o)                              => aliasResourceType(o, typeOf).orElse(viaType(e))
      case SingletonFrom(o)                     => aliasResourceType(o, typeOf).orElse(viaType(e))
      case Indexer(o, _)                        => aliasResourceType(o, typeOf).orElse(viaType(e))
      case _                                    => viaType(e)
    }
  }

  /** Resolve a `ValueSetRef` name to the value set's canonical id declared in the library (mirrors
    * `QueryLowering.valueSetId`). */
  private[lower] def valueSetId(lib: Library, name: String): String =
    lib.valueSets.find(_.name == name).map(_.id).getOrElse(
      throw UnsupportedNode("ValueSetRef", s"unresolved value set '$name'"))

  private[lower] def codePair(lib: Library, name: String): (String, String) = {
    val cd = lib.codes.find(_.name == name).getOrElse(throw UnsupportedNode("CodeRef", s"unresolved code '$name'"))
    (cd.codeSystem.flatMap(cs => lib.codeSystems.find(_.name == cs).map(_.id)).getOrElse(""), cd.id)
  }

  /** BIND-CALL-SITE: the mount for one retrieve call site. The context's own retrieve (`r` is `None`) belongs to
    * no define, so its site names no library; every other site names the define being lowered and its library
    * ([[SqlEvalEnv.siteOwner]]). */
  private[lower] def retrieveMount(r: Option[Retrieve], rt: String, lib: Library, env: SqlEvalEnv): SqlRel = {
    val call = retrieveCall(r, rt, lib, env)
    env.retrieveMount(call, CallSite.retrieve(call, if (r.isEmpty) None else env.siteOwner(lib)))
  }

  /** BIND-CALL-SITE: the value-set site for a membership test on `vsId` in the define being lowered. */
  private[lower] def valueSetMount(vsId: String, lib: Library, env: SqlEvalEnv): SqlRel =
    env.valueSetMount(vsId, CallSite.valueSet(vsId, lib.valueSets.find(_.id == vsId).flatMap(_.version),
      env.siteOwner(lib)))

  /** BIND-RETRIEVE-REQUEST: what one retrieve call site asks its data binding for, as [[RetrieveCall]] holds it.
    * A relation takes no arguments. The compiled plan re-applies every filter above it (BIND-CAPABILITY), so a
    * binding that ignores the request still returns a correct superset. An absent value is NULL or an empty array
    * or map, never a guessed literal (CORE-NO-SILENT-MISLOWER).
    *
    * EMIT-ROOT-INDEPENDENT: no request field names a library. The retrieve's rows may depend only on its request
    * and its data source, so a define's SQL, and its stored result, is the same whichever library is being
    * compiled (`DEC-RESULT-IDENTITY`).
    *
    * `r` is `None` for the CONTEXT BASE read (the per-subject `Patient` scan, [[contextRel]]): it has no
    * predicates and belongs to no define, so its `parameters` map is empty. */
  private[lower] def retrieveCall(r: Option[Retrieve], rt: String, lib: Library, env: SqlEvalEnv): RetrieveCall = {
    val valueSet = r.flatMap(_.codes).collect {
      case ValueSetRef(name, _)         => valueSetId(lib, name)
      case ToList(ValueSetRef(name, _)) => valueSetId(lib, name)
    }
    val codes = r.flatMap(_.codes).toSeq.collect {
      case CodeRef(name, _)         => codePair(lib, name)
      case ToList(CodeRef(name, _)) => codePair(lib, name)
    }
    val (dateLow, dateHigh) = r.flatMap(_.dateRange) match {
      case Some(IntervalExpr(Some(lo), Some(hi), true, true)) => (literalString(lo), literalString(hi))
      case _                                                  => (None, None)
    }
    RetrieveCall(
      fhirType = rt,
      valueSet = valueSet,
      codes = codes,
      dateProperty = r.flatMap(_.dateProperty),
      dateLow = dateLow,
      dateHigh = dateHigh,
      parameters = parameterValues(env.currentDefine.flatMap(env.defineParameters.get).getOrElse(Seq.empty)))
  }

  /** The request's `parameters` (BIND-RETRIEVE-REQUEST): every parameter whose resolved default is a
    * compile-time literal, a literal INTERVAL with its closure flags, a scalar as its string. A parameter with
    * no literal default is OMITTED (the relation reads NULL for its key) rather than guessed at
    * (CORE-NO-SILENT-MISLOWER).
    *
    * The CLOSURE flags are carried, not assumed: the standard Measurement Period is
    * `Interval[@2026-01-01T00:00:00.0, @2027-01-01T00:00:00.0)` — HALF-OPEN — so a relation that read a bare
    * `high` as inclusive would over-select a whole instant at the boundary. */
  private def parameterValues(scope: Seq[(String, ParameterDef)]): Seq[(String, RetrieveCall.Value)] =
    scope.flatMap { case (key, p) =>
      p.default.flatMap {
        case IntervalExpr(Some(lo), Some(hi), lowClosed, highClosed) =>
          (literalString(lo), literalString(hi)) match {
            case (Some(l), Some(h)) => Some(key -> RetrieveCall.Interval(l, lowClosed, h, highClosed))
            case _                  => None
          }
        case other => literalString(other).map(v => key -> RetrieveCall.Scalar(v))
      }
    }

  /** The ISO/scalar string value of a compile-time-literal expression (a resolved parameter default, a
    * folded date-range bound), unwrapping the `As` cast and `To*` conversion the front end leaves around
    * a literal; `None` for any non-literal (so the argument is left ABSENT rather than guessed at,
    * CORE-NO-SILENT-MISLOWER).
    *
    * A `Date`/`DateTime` BOUND is an ELM CONSTRUCTOR node, not a string literal — authored
    * `@2023-06-01` reaches the IR as `DateTimeExpr(Literal(2023), Literal(6), Literal(1))` — so it is
    * rendered from its components ([[isoTemporal]]). Missing that case is why the retired
    * `context.measurementPeriod` / `parameters` envelope fields were always absent in the real corpus
    * even though every eCQM declares a Measurement Period. */
  private def literalString(e: Expr): Option[String] = e match {
    case Literal(_, v)                                  => v
    case As(inner, _, _, _)                             => literalString(inner)
    case FunctionRef(fn, _, Seq(inner)) if fn.startsWith("To") => literalString(inner)
    case DateExpr(parts)                                => isoTemporal(parts, time = false)
    case DateTimeExpr(parts)                            => isoTemporal(parts, time = true)
    case _                                              => None
  }

  /** An ELM `Date`/`DateTime` constructor's literal components as an ISO-8601 string, at exactly the
    * PRECISION the components carry (`[2023]` → `"2023"`, `[2023, 6, 1]` → `"2023-06-01"`,
    * `[2023, 6, 1, 12, 30]` → `"2023-06-01T12:30"`). A PARTIAL value stays partial — padding it to a
    * full timestamp would widen the window the binding prunes on (CORE-NO-SILENT-MISLOWER). `None`
    * unless every present component is an integer literal. */
  private def isoTemporal(parts: Seq[Expr], time: Boolean): Option[String] = {
    val nums = parts.map(literalString).takeWhile(_.isDefined).flatten
    if (nums.isEmpty || nums.size != parts.size) None
    else {
      val date = nums.take(3).zipWithIndex.map { case (v, i) =>
        if (i == 0) f"${v.toInt}%04d" else f"${v.toInt}%02d"
      }.mkString("-")
      if (!time || nums.size <= 3) Some(date)
      else {
        val hms = nums.slice(3, 6).map(v => f"${v.toInt}%02d").mkString(":")
        val ms  = nums.drop(6).headOption.map(v => f".${v.toInt}%03d").getOrElse("")
        Some(s"${date}T$hms$ms")
      }
    }
  }

  /** A value[x]-CHOICE code property (`MedicationRequest.medication`) dispatches to the code-bearing
    * CodeableConcept slot for membership; a monomorphic property is returned unchanged (mirrors
    * `QueryLowering.resolveCodeSlot`). */
  private[lower] def resolveCodeSlot(rt: String, cp: String): String =
    if (FhirModel.isChoiceElement(cp)) FhirModel.choiceSlotForSubfield(rt, cp, "coding").getOrElse(cp) else cp

  /** Keep rows of `resources` whose `<codeProperty>` CodeableConcept has ANY coding in the value-set
    * expansion — the ROW-LEVEL semi-join (LANG-TERMINOLOGY), the SQL twin of `QueryLowering.membershipFilter`.
    *
    * PERF (EMIT-SQL-MEMBERSHIP-EQUIJOIN): the join keys `(system, code)` are EXPLODED to rows and matched by
    * an EQUI-join, not compared inside an `exists(codings, x -> x.system = vs.system AND x.code = vs.code)`
    * lambda. A predicate that buries the equality inside a higher-order function gives Catalyst no hash key,
    * so it falls back to a broadcast-nested-loop join — `O(resource_rows × valueset_codes)`, the cartesian
    * a multi-arm value-set exclusion produced. Instead (mirroring the compiler): explode each resource's
    * codings to `(id, system, code)` rows, EQUI-semi-join the expansion `USING (system, code)` (a hash
    * join), take the DISTINCT matching resource ids, then equi-semi-join `resources` back on the id — two
    * hash joins that scale linearly. `<codeProperty>.coding` is `array<coding>`; a 0..* code property
    * flattens the extra list level (cardinality read STATICALLY from the model, ARCH-NO-RUNTIME-SCHEMA).
    * `resourceExpr` navigates the coding/id off the row (a mount navigates `__resource`). */
  private def membershipSemiJoin(resources: SqlRel, resourceExpr: SqlExpr, codeProperty: String,
                                 vsId: String, env: SqlEvalEnv, codeIsList: Boolean, lib: Library): SqlRel = {
    val codings0 = navPath(resourceExpr, s"$codeProperty.coding")
    val codings  = if (codeIsList) Call("flatten", Seq(codings0)) else codings0
    resources match {
      // The RETRIEVE-scan path: `resources` is the shared retrieve MOUNT (a bare `TableRef`), cheap to
      // reference twice, so use the equi-join rewrite (mirrors the compiler `QueryLowering.membershipFilter`):
      // explode each resource's codings to `(id, system, code)` rows, EQUI-semi-join the expansion
      // `USING (system, code)` (a hash join, not a nested loop), DISTINCT the matching ids, then equi-semi-join
      // the mount back on the id — two hash joins that scale linearly instead of the `exists`-lambda cartesian.
      case mount: SqlRel.TableRef =>
        val idExpr = resourceIdOf(resourceExpr) // stable resource identity
        val exploded = SqlRel.Select(
          projections = Seq(
            SqlRel.Proj(idExpr, "__mid"),
            SqlRel.Proj(Field(Col("__coding"), "system"), "system"),
            SqlRel.Proj(Field(Col("__coding"), "code"), "code")),
          from = SqlRel.LateralView(mount, Call("explode", Seq(codings)), "__cx", Seq("__coding")))
        val matched = SqlRel.Select(
          projections = Seq(SqlRel.Proj(Col("__mid"))), distinct = true,
          from = SqlRel.Join(asFrom(exploded, "__ex"), SqlRel.AliasRel(valueSetMount(vsId, lib, env), "__vs"),
            SqlRel.JoinKind.LeftSemi, using = Seq("system", "code")))
        SqlRel.Join(mount, SqlRel.AliasRel(matched, "__mids"), SqlRel.JoinKind.LeftSemi,
          on = Some(Infix("=", idExpr, Col("__mid"))))

      // The query-WHERE membership path: `resources` is a combined FRAME (not a table), referenced ONCE to
      // avoid duplicating that subtree. Keep the single-reference `exists`-lambda semi-join (the value-set is
      // small and the frame already narrowed by the query's other clauses).
      case frame =>
        val vs = "__vs"
        val cond = Call("exists", Seq(codings, Lambda(Seq("x"),
          Infix("AND", Infix("=", Field(Col("x"), "system"), Field(Col(vs), "system")),
                       Infix("=", Field(Col("x"), "code"),   Field(Col(vs), "code"))))))
        SqlRel.Join(frame, SqlRel.AliasRel(valueSetMount(vsId, lib, env), vs), SqlRel.JoinKind.LeftSemi, on = Some(cond))
    }
  }

  /** Keep rows whose `codeProperty` has a coding matching a SINGLE declared `(system, code)` — the code
    * analog of [[membershipSemiJoin]] (mirrors `QueryLowering.codeFilter`), a `WHERE exists(...)` filter. */
  private def codeFilter(resources: SqlRel, resourceExpr: SqlExpr, codeProperty: String,
                         system: String, code: String, codeIsList: Boolean): SqlRel = {
    val codings0 = navPath(resourceExpr, s"$codeProperty.coding")
    val codings  = if (codeIsList) Call("flatten", Seq(codings0)) else codings0
    val pred = Call("exists", Seq(codings, Lambda(Seq("c"),
      Infix("AND", Infix("=", Field(Col("c"), "system"), str(system)),
                   Infix("=", Field(Col("c"), "code"),   str(code))))))
    wrapWhere(resources, pred)
  }

  /** Wrap a relation in `SELECT * FROM (<rel>) WHERE <pred>`, aliasing a non-atomic source so the
    * predicate can navigate its columns. */
  private def wrapWhere(rel: SqlRel, pred: SqlExpr): SqlRel =
    SqlRel.Select(projections = Nil, from = asFrom(rel, "__f"), where = Some(pred))

  /** Present a relation as a FROM atom: a bare table stays a `TableRef`; anything else is aliased as a
    * derived table so it can be referenced / joined `USING`. */
  private def asFrom(rel: SqlRel, alias: String): SqlRel = rel match {
    case t: SqlRel.TableRef => t
    case other              => SqlRel.AliasRel(other, alias)
  }

  /** Scan a retrieve's resources applying its scan-level filters: the value-set/single-code terminology
    * membership and the date-range / period pre-filter (the SQL twin of `QueryLowering.filteredRetrieve`).
    * Returns a relation exposing the mount's `(__subject_id, __resource)`. Navigates the code/date
    * property THROUGH the `__resource` struct of the mount (BIND-CONTRACT). */
  private[lower] def filteredRetrieve(r: Retrieve, rt: String, env: SqlEvalEnv, lib: Library,
                                      typeOf: Expr => CqlType): SqlRel =
    // DATA-RETRIEVE-BARRIER-OPT: when this read was materialized as its own object, READ it instead of
    // re-inlining the mount call and its predicates. This is the ONE seam every filtered retrieve flows
    // through, and the object carries the identical `(__subject_id, __resource)` shape the inlined form
    // produces — so every call site (`retrieveRelationFrame`, `aliasedSource`, `subjectsPresent`) is
    // substituted without a change of its own. Empty map (the default) → the inline form, byte-identical.
    env.materializedReads.get(r)
      .map(obj => env.readRef(obj))
      .getOrElse(filteredRetrieveInline(r, rt, env, lib, typeOf))

  /** The INLINE realization of a filtered retrieve — the mount call with the scan predicates re-applied
    * above it. Split out of [[filteredRetrieve]] so the barrier emit (`SqlFrameLowering`) can build a
    * read's BODY without recursing into the substitution that would make it read itself. */
  private[lower] def filteredRetrieveInline(r: Retrieve, rt: String, env: SqlEvalEnv, lib: Library,
                                            typeOf: Expr => CqlType): SqlRel = {
    val mount = retrieveMount(Some(r), rt, lib, env)
    val resourceCol = Col(Resource)
    def codeProp: String = resolveCodeSlot(rt, r.codeProperty.getOrElse(throw UnsupportedNode("Retrieve",
      s"code-filtered retrieve [$rt] has no codeProperty to test membership on")))
    def isList: Boolean = FhirModel.isListElement(rt, codeProp)
    val codeFiltered = r.codes match {
      case None                                 => mount
      case Some(ValueSetRef(name, _))           => membershipSemiJoin(mount, resourceCol, codeProp, valueSetId(lib, name), env, isList, lib)
      case Some(ToList(ValueSetRef(name, _)))   => membershipSemiJoin(mount, resourceCol, codeProp, valueSetId(lib, name), env, isList, lib)
      case Some(CodeRef(name, _))               => val (s, c) = codePair(lib, name); codeFilter(mount, resourceCol, codeProp, s, c, isList)
      case Some(ToList(CodeRef(name, _)))       => val (s, c) = codePair(lib, name); codeFilter(mount, resourceCol, codeProp, s, c, isList)
      case Some(other)                          => throw UnsupportedNode(other.elmType, s"retrieve [$rt] code filter must be a ValueSetRef or a code")
    }
    // Date-range scan filter (ARCH-PUSHDOWN): keep resources whose `<dateProperty>` point is in the
    // range — the SAME `point in range` predicate the unoptimized `where` lowers, navigated through the
    // `__resource` struct so the optimized scan and the `where` are identical.
    val dateFiltered = (r.dateProperty, r.dateRange) match {
      case (Some(dp), Some(range)) =>
        val conv = r.datePointConverter.getOrElse("ToDateTime")
        val point = FunctionRef(conv, Some("FHIRHelpers"), Seq(Property(Some(AliasRef("__r")), None, dp)))
        wrapWhere(codeFiltered, resourceScanPredicate(In(point, range), typeOf))
      case _ => codeFiltered
    }
    // Period-property COARSE scan pre-filter (ARCH-PUSHDOWN): a half-open year window on the Period's raw
    // ISO strings — a plain string comparison so partial dates prune correctly, a non-lossy superset of
    // the precise `during` (which stays in the `where`).
    val periodFiltered = r.periodScanFilter match {
      case Some((path, lowIncl, highExcl)) =>
        wrapWhere(dateFiltered,
          Infix("AND", Infix(">=", navPath(resourceCol, s"$path.end"), str(lowIncl)),
                       Infix("<",  navPath(resourceCol, s"$path.start"), str(highExcl))))
      case None => dateFiltered
    }
    profileScanFilter(r).fold(periodFiltered)(wrapWhere(periodFiltered, _))
  }

  /** LANG-RETRIEVE-PROFILE: keep rows conforming to the retrieve's SPECIALISED profile
    * (`Retrieve.profileConstraint`), tested against `meta.profile`.
    *
    * Why a retrieve needs this at all: a PROFILE-ONLY retrieve — `[USCoreObservationOccupationProfile]`,
    * no code filter — is otherwise indistinguishable from `[Observation]`, so it returns every Observation
    * for the subject. A measure that does exactly that sends the over-matched rows into
    * `O.value in "<VS>"` where another profile's observation `value[x]` is a `Quantity`.
    *
    * A resource that claims NO profile is KEPT: `meta.profile` is a claim, not a guarantee, and real
    * (especially customer) FHIR data is frequently untagged — excluding untagged rows would silently
    * empty every QI-Core retrieve against such data. So the predicate is "if you declare profiles, you
    * must declare THIS one". Identical to the reference's rule
    * (`InMemoryFhirRetrieveProvider.profileMatches`) so the differential compares like with like.
    *
    * `size(...) < 1` covers the empty array in either mode (a NULL array is caught by the `IS NULL` arm
    * first, so `size(NULL)`'s mode-dependent −1-vs-NULL result is never reached). */
  private[lower] def profileScanFilter(r: Retrieve): Option[SqlExpr] =
    r.profileConstraint.map { profile =>
      val profiles = navPath(Col(Resource), "meta.profile")
      Infix("OR",
        Infix("OR", Postfix(profiles, "IS NULL"), Infix("<", Call("size", Seq(profiles)), Lit("1"))),
        Call("array_contains", Seq(profiles, str(profile))))
    }

  /** Lower a resource-rooted scan predicate over a retrieve mount, navigating every alias/scope property
    * THROUGH the `__resource` struct (mirrors `QueryLowering.resourceScanPredicate`): `bindScopes`
    * rewrites scope-rooted navigation and every alias binds to `__resource`.
    *
    * EMIT-SQL-TEMPORAL-UDF-OPAQUE: fold the precision-aware temporal-compare tower to `cql_temporal_cmp` here too.
    * This is the pushed date-range scan filter (`In(point, range)` folded by `DateRangePushdown` and
    * re-emitted at [[filteredRetrieve]]) — a runtime `__resource.<date>` operand, so it is exactly the
    * operator tower the UDF collapses (a literal-only predicate still const-folds via the `aC/bC` guard in
    * `SqlColumnLowering`). Aligns the pushdown-ON scan predicate with the pushdown-OFF residual `where`
    * (which `applyWhere` already lowers UDF-first), preserving the emit-identical self-diff
    * while cutting the analyzer's per-site cost. The coarse Period pre-filter (raw string `>=`/`<`) stays
    * native by construction (it is emitted directly, not through this path). */
  private[lower] def resourceScanPredicate(pred: Expr, typeOf: Expr => CqlType = _ => CqlType.AnyType): SqlExpr =
    SqlColumnLowering.lower(bindScopes(pred), resolveAlias = _ => Col(Resource), typeOf = typeOf,
      temporalUdf = true)

  /** ARCH-PROJECT-FUSE: a retrieve with NO scan-level filter, so its [[filteredRetrieve]] is just the
    * bare mount (mirrors `QueryLowering.isUnfilteredScan`).
    *
    * `profileConstraint` counts as a scan filter (LANG-RETRIEVE-PROFILE) — a profile-only retrieve has no
    * codes/dates at all, so omitting it here would fuse straight to the bare mount and silently drop the
    * profile predicate, which is precisely the over-match this filter exists to stop. */
  private[lower] def isUnfilteredScan(r: Retrieve): Boolean = !RetrieveReads.isFiltered(r)

  /** The `Relation` realization of a BARE retrieve define (`define E: [Encounter]`): the resources as
    * ROWS — `__subject_id`, the resource id as `__ordinal`, and the `__resource` struct as `__val`
    * (the SQL twin of `QueryLowering.retrieveRelationFrame`). */
  def retrieveRelationFrame(r: Retrieve, env: SqlEvalEnv, lib: Library, typeOf: Expr => CqlType): SqlRel = {
    val rt = localName(r.dataType)
    SqlRel.Select(
      projections = Seq(
        SqlRel.Proj(Col(SubjectId)),
        SqlRel.Proj(resourceIdOf(Col(Resource)), "__ordinal"),
        SqlRel.Proj(Col(Resource), ValueCol)),
      from = asFrom(filteredRetrieve(r, rt, env, lib, typeOf), "__ret"))
  }

  /** Lower a Relation source (`alias` bound to a retrieve / relation define / union) to a per-subject
    * relation exposing `__subject_id`, one STRUCT column named by the alias, and `__ord_<alias>` (the SQL
    * twin of `QueryLowering.aliasedSource`). A HETEROGENEOUS-resource union realizes into ONE discriminated
    * Choice struct ([[hetUnionAliasSource]]); `demand` is the field set navigated on the alias
    * (ARCH-RESOURCE-HANDLE), so each member slot carries only the pruned handle. */
  private def aliasedSource(alias: String, expr: Expr, env: SqlEvalEnv, lib: Library,
                            typeOf: Expr => CqlType, relDefs: Set[String],
                            demand: Set[String] = Set.empty): SqlRel = expr match {
    case As(inner, _, _, cts) if cts.nonEmpty =>
      aliasedSource(alias, inner, env, lib, typeOf, relDefs, demand)
    case r: Retrieve =>
      val rt = localName(r.dataType)
      SqlRel.Select(
        projections = Seq(
          SqlRel.Proj(Col(SubjectId)),
          SqlRel.Proj(Col(Resource), alias),
          SqlRel.Proj(resourceIdOf(Col(Resource)), ordName(alias))),
        from = asFrom(filteredRetrieve(r, rt, env, lib, typeOf), "__ret"))
    case ExpressionRef(name, None) if relDefs.contains(name) =>
      SqlRel.Select(
        projections = Seq(
          SqlRel.Proj(Col(SubjectId)),
          SqlRel.Proj(Col(ValueCol), alias),
          SqlRel.Proj(Col("__ordinal"), ordName(alias))),
        from = env.relRef(name))
    case u @ Union(_, _) if isHetResourceUnion(u, typeOf) =>
      hetUnionAliasSource(alias, u, env, lib, typeOf, relDefs, demand)
    case u @ Union(_, _) =>
      // Same-type `union` of relation sources (LANG-LIST set union): DISTINCT-union the per-source relations
      // (same alias shape). CQL `union` has SET semantics — dedup by element identity — and each arm's
      // `__ord_<alias>` IS the resource identity, so a resource matching two profiles (e.g.
      // `[ConditionProblemsHealthConcerns: vs] union [ConditionEncounterDiagnosis: vs]`, both → Condition)
      // collapses to ONE row rather than duplicating. Full-row dedup, mirroring the union DEFINE path
      // (`SqlFrameLowering.branchRelation`); a bare `UNION ALL` here yielded `[X, X]` vs the reference `[X]`.
      SqlRel.Select(projections = Nil, distinct = true,
        from = asFrom(SqlRel.UnionAll(flattenUnion(u).map(p =>
          aliasedSource(alias, p, env, lib, typeOf, relDefs, demand))), "__u"))
    case q: Query =>
      // A relation-valued SUB-QUERY source (a nested query, e.g. an inlined QI-Core fluent filter): realize
      // the inner query's combined rows and reshape to this alias's `(subject, struct-as-alias, ord)` — the
      // SQL twin of `QueryLowering.aliasedSource`'s sub-query case.
      val c = combined(q, env, lib, typeOf, relDefs)
      // ARCH-SQL-CSE: hoist the reshaped element's duplication-prone operands onto the inner combined rows.
      val (fromH, elem) = SqlHoist.hoisted(c.rel, env.hoistCounter)(h =>
        elemProjection(c.query, c.binds, c.typeOf, c.resolveRef, h, true))
      val ord: SqlExpr =
        if (c.query.sources.sizeIs == 1) Col(ordName(c.query.sources.head.alias))
        else Struct(c.query.sources.map(s => s.alias -> Col(ordName(s.alias))))
      SqlRel.Select(
        projections = Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(elem, alias), SqlRel.Proj(ord, ordName(alias))),
        from = asFrom(fromH, "__sq"))
    case other =>
      throw UnsupportedNode(other.elmType,
        "a Relation query source must be a FHIR retrieve, a Relation-encoded list define, a union, or a sub-query")
  }

  /** Realize a HETEROGENEOUS-resource `union` as a query SOURCE into ONE discriminated Choice struct
    * (the SQL twin of `QueryLowering.hetUnionAlias`): each arm realizes to its alias struct, wrapped via
    * [[SqlColumnLowering.choiceTagArm]] (resource member = the demand-pruned identity+fields HANDLE), and
    * `__ord_<alias>` is arm-index-prefixed so the driver-side assembly yields LEFT-then-RIGHT `union`
    * order. Arms `UNION ALL`-align (SQL coerces the sibling arms' null member slots). Navigation on the
    * alias is dispatched per member by [[rewriteHetUnionNav]] (run before this in `combined`). */
  private def hetUnionAliasSource(alias: String, u: Union, env: SqlEvalEnv, lib: Library,
                                  typeOf: Expr => CqlType, relDefs: Set[String], demand: Set[String]): SqlRel = {
    val members = unionMembers(u, typeOf)
    SqlRel.UnionAll(flattenUnion(u).zipWithIndex.map { case (arm, i) =>
      val armMember = flatResourceMembers(typeOf(unionArmInner(arm))).headOption.getOrElse(
        throw UnsupportedNode("Union", "heterogeneous union arm is not a resource type"))
      val armRel = aliasedSource(alias, arm, env, lib, typeOf, relDefs, demand)
      SqlRel.Select(
        projections = Seq(
          SqlRel.Proj(Col(SubjectId)),
          SqlRel.Proj(SqlColumnLowering.choiceTagArm(Col(alias), armMember, members, demand, true), alias),
          SqlRel.Proj(Call("concat", Seq(str(f"$i%04d|"), Cast(Col(ordName(alias)), "STRING"))), ordName(alias))),
        from = asFrom(armRel, "__ha"))
    })
  }

  /** The top-level fields navigated on `alias` anywhere in `q` — the demand set an inline het-union source
    * prunes its handle to (mirrors `QueryLowering.aliasNavFields`). */
  private def aliasNavFields(q: Query, alias: String): Set[String] = {
    val acc = scala.collection.mutable.Set.empty[String]
    def firstSeg(path: String): Unit = path.split('.').headOption.filter(_.nonEmpty).foreach(acc += _)
    def walk(n: IrNode): Unit = {
      n match {
        // EMIT-SQL-FLAT-CHOICE: the het-union navigation is a plain typed `Property` (no typeless marker),
        // so demand is counted uniformly here off the top-level segment.
        case Property(None, Some(`alias`), path)           => firstSeg(path)
        case Property(Some(AliasRef(`alias`)), None, path) => firstSeg(path)
        case _                                             =>
      }
      n.children.foreach(walk)
    }
    walk(q)
    acc.toSet
  }

  /** The named view/table of a Relation-encoded list define — referenced by its stable, SANITIZED
    * object name (`SqlNames`, matching [[SqlFrameLowering.relName]]). Takes the `EMIT-SQL-OBJECT-PREFIX`
    * namespace explicitly (no default) so it cannot silently produce an UNPREFIXED name that misses the
    * view an object-prefixed lowering actually emitted. */
  private def relDefName(name: String, prefix: String): String = SqlNames.rel(name, prefix)

  /** Lower a clause expression in the Relation context: bind every alias to its struct column and rewrite
    * scope navigation to project through that binding (the SQL twin of `QueryLowering.lowerClause`). */
  private def lowerClause(e: Expr, binds: Map[String, SqlExpr], typeOf: Expr => CqlType,
                          resolveRef: SqlColumnLowering.RefResolver,
                          hoist: SqlColumnLowering.Hoister = SqlColumnLowering.noHoist,
                          temporalUdf: Boolean = false,
                          hoistT: SqlColumnLowering.Hoister = null): SqlExpr =
    SqlColumnLowering.lower(bindScopes(e), resolveRef,
      a => binds.getOrElse(a, throw UnsupportedNode("AliasRef", s"unbound query alias '$a'")), typeOf,
      hoist = hoist, hoistT = if (hoistT == null) hoist else hoistT, temporalUdf = temporalUdf)

  /** True iff any clause of `q` extracts a polymorphic FHIR element through `FHIRHelpers.ToValue`/`ToQuantity`
    * (mirrors `QueryLowering.hasToValueExtractor`). */
  private def hasToValueExtractor(q: Query): Boolean = {
    def hit(e: Expr): Boolean = e.exists {
      case FunctionRef("ToValue", Some("FHIRHelpers"), _)    => true
      case FunctionRef("ToQuantity", Some("FHIRHelpers"), _) => true
      case _                                                 => false
    }
    (q.where ++ q.returnExpr).exists(hit) || q.relationships.exists(r => hit(r.suchThat))
  }

  private def referencesContext(q: Query, ctxName: String): Boolean = {
    def hits(e: Expr): Boolean = e.exists { case ExpressionRef(`ctxName`, _) => true; case _ => false }
    (q.where ++ q.returnExpr).exists(hits) || q.relationships.exists(r => hits(r.suchThat))
  }

  /** The per-subject context resource as a relation exposing `__subject_id` + a struct column named for
    * the context (`Patient`) — the SQL twin of `FrameLowering.contextFrame`, over the context retrieve
    * mount. `None` for an ungrouped context.
    *
    * The context base has NO retrieve predicates, but it is still passed the compilation-global
    * arguments (`library`, `parameters`) — those do not depend on a retrieve, and a binding for the
    * context type deserves the same evaluation metadata as any other, so a `Patient`
    * binding sees the library and parameters. */
  private[lower] def contextRel(env: SqlEvalEnv, lib: Library): Option[SqlRel] =
    if (env.context.hasSubjectGrouping)
      Some(SqlRel.Select(
        projections = Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(Col(Resource), env.context.name)),
        from = retrieveMount(None, env.context.name, lib, env)))
    else None

  /** The Cartesian combination of all sources (inner-joined on `__subject_id`), with the value[x] static
    * rewrites applied, then `where` (residual 3VL filter + value-set-membership semi-joins) and each
    * `with`/`without` (semi-/anti-join). The SQL twin of `QueryLowering.combined`, MINUS the proto-tree
    * CSE/hoist/staging machinery (SQL shares natively). */
  private[lower] def combined(q0: Query, env: SqlEvalEnv, lib: Library, typeOf: Expr => CqlType,
                              relDefs: Set[String]): Combined = {
    // `let` on a RELATION-realized query is not yet lowered (only the Array path binds lets, [[array]]).
    // Fail loudly rather than silently drop the bindings (CORE-NO-SILENT-MISLOWER).
    if (q0.lets.nonEmpty)
      throw UnsupportedNode("Query.let", "let over a relation-realized query is not yet lowered (Array-path only)")
    val aliasTypes = (q0.sources.map(s => s.alias -> s.expr) ++ q0.relationships.map(r => r.alias -> r.expr))
      .flatMap { case (a, e) => aliasResourceType(e, typeOf).map(a -> _) }.toMap
    // Navigation on a HETEROGENEOUS-resource union alias (an inline union, or a relation define typed
    // `List<Choice<resources>>`) dispatches per member over the discriminated Choice struct — resolve
    // BEFORE the single-type value[x] rewrites. The alias's DEMAND (the top-level fields it navigates) is
    // computed off `q0` here (rewriteHetUnionNav rewrites those navigations away), so the source
    // realization ([[hetUnionAliasSource]]) can prune each member handle to just those fields.
    val hetSources = (q0.sources.map(s => s.alias -> s.expr) ++ q0.relationships.map(r => r.alias -> r.expr))
      .flatMap { case (a, e) => hetAliasMembers(e, typeOf).map(a -> _) }.toMap
    val demandOf = hetSources.keys.map(a => a -> aliasNavFields(q0, a)).toMap
    val qHet = if (hetSources.nonEmpty) rewriteHetUnionNav(q0, hetSources, flat = true) else q0
    val qs = rewriteChoiceSubfieldValueX(qHet, aliasTypes)
    val qStat = rewriteExtensionValueX(
      if (hasToValueExtractor(qs)) rewriteChoiceValueXStatic(qs, aliasTypes) else qs)
    // NESTED value[x] (LANG-CHOICE-MODEL): a choice reached through a `let`/alias chain — resolve via scope.
    val qr = rewriteNestedChoiceValueX(qStat, typeOf)

    val srcRels = qr.sources.map(s => s.alias -> asFrom(
      aliasedSource(s.alias, s.expr, env, lib, typeOf, relDefs, demandOf.getOrElse(s.alias, Set.empty)), s"__src_${s.alias}"))
    val joined0 = srcRels.map(_._2).reduce((l, r) => SqlRel.Join(l, r, SqlRel.JoinKind.Inner, using = Seq(SubjectId)))

    val ctxName = env.context.name
    val withCtx: SqlRel =
      if (referencesContext(qr, ctxName)) contextRel(env, lib)
        .map(cf => SqlRel.Join(joined0, SqlRel.AliasRel(cf, "__ctx"), SqlRel.JoinKind.LeftOuter, using = Seq(SubjectId)))
        .getOrElse(joined0)
      else joined0
    val hasCtx = referencesContext(qr, ctxName) && contextRel(env, lib).isDefined

    // LANG-QUERY-DEFREF: a clause may reference a per-subject SCALAR sibling define (e.g.
    // `… same day as "Most Recent Reading Day"`). LEFT JOIN each referenced scalar define's per-subject
    // view (keyed by subject — a scalar broadcasts across the source's rows) so `resolveRef` reads its value
    // column. Only names in `env.scalarDefs` (lowered before this define, `SqlFrameLowering`) are joinable;
    // any other reference still fails loudly.
    val clauseExprs0: Seq[Expr] =
      qr.where.toSeq ++ qr.returnExpr.toSeq ++ qr.sort.flatMap(_.expr) ++ qr.relationships.map(_.suchThat)
    val scalarRefs: Seq[String] =
      clauseExprs0.flatMap(_.collect { case ExpressionRef(n, None) if env.scalarDefs.contains(n) => n }).distinct
    val joined: SqlRel = scalarRefs.foldLeft(withCtx) { (acc, n) =>
      // EMIT-SQL-VALUE-COLUMN: rename the dependency's uniform `__val` to its object name INSIDE the join
      // subquery, so several referenced scalars stay distinct columns on the combined frame (the twin of
      // `SqlFrameLowering.scalarDepJoin`).
      // Deliberately UNPREFIXED (`EMIT-SQL-OBJECT-PREFIX` namespaces OBJECTS, not columns): this is a
      // COLUMN label inside a derived table, and the resolver below reads it back by the same bare name.
      // The `from` relation IS prefix-aware — it goes through `env.scalarRefName`.
      val view = SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(Col(SystemColumns.Val), SqlNames.obj(n))),
        from = env.scalarRef(n))
      SqlRel.Join(acc, SqlRel.AliasRel(view, s"__scdef_${SqlNames.sanitize(n)}"), SqlRel.JoinKind.LeftOuter, using = Seq(SubjectId))
    }
    val resolveRef: SqlColumnLowering.RefResolver = name =>
      if (name == ctxName && hasCtx) Col(ctxName)
      else if (scalarRefs.contains(name)) Col(SqlNames.obj(name))
      else SqlColumnLowering.rejectRefs(name)

    // Scope-aware type function for the CLAUSE lowering (LANG-CHOICE-MODEL): `typeOf` is a context-free `Expr => CqlType`, so a
    // reference to one of THIS query's source/relationship aliases (or, in a nested inlined sub-query, an
    // OUTER alias) resolves to `AnyType` — which makes a singleton-resource source (a fluent function's
    // `Request R` inlined to an outer alias, as in a medication-request period helper) look like a LIST and lower
    // through the Array path's `transform` (a datatype mismatch on the resource struct). Augment `typeOf`
    // with each alias's ELEMENT type so a source-alias reference types correctly wherever a clause (and its
    // nested queries) navigates it. A superset of `typeOf` — falls back to it for every non-alias node.
    def elemType(t: CqlType): CqlType = t match { case CqlType.ListType(el) => el; case other => other }
    val aliasElemTypes: Map[String, CqlType] =
      (qr.sources.map(s => s.alias -> s.expr) ++ qr.relationships.map(r => r.alias -> r.expr))
        .map { case (a, e) => a -> elemType(typeOf(e)) }.toMap
    val augTypeOf: Expr => CqlType = {
      case AliasRef(a) if aliasElemTypes.get(a).exists(_ != CqlType.AnyType) => aliasElemTypes(a)
      case other => typeOf(other)
    }

    val binds0 = qr.sources.map(s => s.alias -> (Col(s.alias): SqlExpr)).toMap
    // LANG-REFERENCE-FOLLOW-VALUE: a `singleton from ([Type] X where X.id = <outer>.<ref>)` used for its
    // VALUE — the `(O.encounter.encounterFor()).class.code` shape — cannot lower on the
    // Array path (its source is a FHIR retrieve, not an array). Realize each such follow as a correlated
    // LEFT JOIN that PROJECTS the followed resource per outer row (the value cousin of the with-join above),
    // then rewrite the sub-query node to a reference to the projected struct column so the enclosing
    // navigation (`.class.code`) reads a plain field.
    val (joinedRF, bindsRF, qrRF) = realizeRefFollows(joined, binds0, qr, env, lib, augTypeOf, resolveRef, relDefs)
    val whereFiltered = applyWhere(joinedRF, qrRF, env, lib, bindsRF, augTypeOf, relDefs, resolveRef)
    val withRels = qrRF.relationships.foldLeft(whereFiltered) { (acc, rel) =>
      // LANG-REFERENCE-FOLLOW: a CONTEXT-LESS related source (a resource with no patient subject — e.g.
      // `with [Medication] M such that M.id = <ref>`) carries a NULL `__subject_id`, so joining on
      // the subject key would drop every row. Correlate on the `such that` ALONE; the patient context is
      // carried by the outer (patient-context) source the semi-/anti-join keeps. A patient-context
      // relationship keeps the subject-key equality (the correlated two-sided join). Mirrors QueryLowering.
      val ctxLess = aliasResourceType(rel.expr, augTypeOf)
        .exists(rt => FhirModel.isModeled(rt) && !FhirModel.isPatientContext(rt))
      // A value-set-membership conjunct on the RELATED alias (`M.code in vs`, a medication reference
      // follow) is not lowerable as a plain join column (`InValueSet` is a semi-join). PUSH it into the
      // related retrieve as a code filter ([[filteredRetrieve]]) so only the correlated reference equality
      // remains as the join predicate — SCOPED to the context-less path over a codeless retrieve; anything
      // else keeps its `such that` whole and fails loudly if unhandled (CORE-NO-SILENT-MISLOWER).
      val (relExpr, joinPred) =
        if (ctxLess) pushRelMembership(rel.expr, rel.alias, rel.suchThat) else (rel.expr, Some(rel.suchThat))
      val relBase  = aliasedSource(rel.alias, relExpr, env, lib, augTypeOf, relDefs, demandOf.getOrElse(rel.alias, Set.empty))
      val relBinds = bindsRF + (rel.alias -> Col(rel.alias))
      // ARCH-SQL-CSE for the `such that`: the temporal towers (`onsetSpan()`, a value[x]
      // interval helper) duplicate per `.low`/`.high`/precision read EXACTLY as in a `where`, but the join
      // condition references BOTH the source side (`acc`) AND the related side (`relBase`), so a single-
      // frame hoist (SqlHoist) is not enough — ROUTE each hoisted operand to the side it references
      // (source-only → acc, related-only → relBase; a genuinely two-sided operand stays inline). Without
      // this a `with … such that onsetSpan() overlaps/starts …` emits 50–130 MB and trips the
      // emit-size gate (the condition-overlap and intervention blowups, and the buried-exists
      // synth relationships [[liftMembershipFlags]] routes here too).
      val srcAliases = qrRF.sources.map(_.alias).toSet ++ (if (referencesContext(qrRF, ctxName)) Set(ctxName) else Set.empty[String])
      val accCols = scala.collection.mutable.ArrayBuffer.empty[(String, SqlExpr)]
      val relCols = scala.collection.mutable.ArrayBuffer.empty[(String, SqlExpr)]
      val accBy   = scala.collection.mutable.HashMap.empty[SqlExpr, SqlExpr]
      val relBy   = scala.collection.mutable.HashMap.empty[SqlExpr, SqlExpr]
      val rightHoist = scala.collection.mutable.Set.empty[String] // __hN bound on the related side
      // EMIT-SQL-LET-CARRIER (ARCH-SQL-CSE): a two-sided operand (references BOTH join inputs — e.g. a
      // helper's `onsetSpan()`, whose age arms read the inner alias' onset/abatement AND the outer
      // `Patient.birthDate`) can be projected onto NEITHER frame, so `route` USED to leave it fully inline —
      // and a `such that … onsetSpan() starts before … and not … ends before …` then re-spelled the
      // whole onset/abatement choice ladder per `.low`/`.high`/comparison (one measure's intervention
      // defines: 30× → four ~40 KB defines, ~155 KB / 380 temporal ops of the 289 KB measure). Bind it ONCE via
      // the scalar-let carrier idiom instead (the `element_at(transform(array(struct(…)))` chain
      // [[letCarrier]] uses): `bothCols` collects each such operand as `__b<N>` (dedup'd structurally by
      // `bothBy`, in DEPENDENCY order — an inner interval is bound before an outer one that reads it), and
      // the carrier wraps the lowered predicate so every use reads a struct field. An operand that spans the
      // two REAL frames OR references any already-carried `__b<N>` routes here.
      val bothCols   = scala.collection.mutable.ArrayBuffer.empty[(String, SqlExpr)]
      val bothBy     = scala.collection.mutable.HashMap.empty[SqlExpr, SqlExpr]
      val bothHoist  = scala.collection.mutable.Set.empty[String] // __bN bound in the carrier (neither frame)
      // Route a hoisted operand onto the single join input it references; `onBoth` decides a genuinely
      // two-sided operand (or one reading a carried `__bN`). Shared by both hoisters — they differ ONLY in
      // the two-sided case.
      def routeOne(e: SqlExpr, onBoth: SqlExpr => SqlExpr): SqlExpr = {
        val refs      = freeColRefs(e)
        val toRight   = refs.exists(r => r == rel.alias || rightHoist.contains(r))
        val toLeft    = refs.exists(r => r != rel.alias && !rightHoist.contains(r) && !bothHoist.contains(r))
        val refsBound = refs.exists(bothHoist.contains)
        if ((toRight && toLeft) || refsBound) onBoth(e)
        else if (toRight) relBy.getOrElseUpdate(e, { val n = s"__h${env.hoistCounter.getAndIncrement()}"; relCols += (n -> e); rightHoist += n; Col(n) })
        else              accBy.getOrElseUpdate(e, { val n = s"__h${env.hoistCounter.getAndIncrement()}"; accCols += (n -> e); Col(n) })
      }
      // Fine temporal towers (`hoistT` — `cql_date_add`/`cql_temporal_step`/boundary): a two-sided one stays
      // INLINE (the pre-carrier behavior). These live INSIDE the interval's onset/abatement choice `CASE`
      // arms (a birthDate+age arm), so pulling one into the eager carrier struct would evaluate it
      // UNCONDITIONALLY and a Period's date string would fail the age arm's `CAST(… AS DECIMAL)` — the CASE
      // guard must keep it lazy. They ride along inside the whole-interval operand the `routeIv` carrier binds.
      val routeT: SqlColumnLowering.Hoister = e => routeOne(e, identity)
      // Interval-level operands (`hoist` — `ivOperand`/`ivGuard`/`In`-point): a two-sided one IS carried. The
      // whole interval struct `named_struct('low', CASE …, 'high', CASE …)` is safe to bind once — its arms
      // stay guarded by their CASEs — and binding it collapses the per-`.low`/`.high`/comparison re-inlining.
      val routeIv: SqlColumnLowering.Hoister = e => routeOne(e, x =>
        bothBy.getOrElseUpdate(x, { val n = s"__b${env.hoistCounter.getAndIncrement()}"; bothCols += (n -> x); bothHoist += n; Col(n) }))
      // Force temporalUdf = false: this predicate becomes the ON clause of a LEFT ANTI/SEMI JOIN,
      // and Databricks forbids external catalog UDFs (cql_to_interval etc.) there.
      val suchThat0 = joinPred.map(lowerClause(_, relBinds, augTypeOf, resolveRef, routeIv, false, routeT))
        .getOrElse(bool(true))
      // Wrap the lowered predicate in the carrier for the two-sided operands (a no-op when `bothCols` empty,
      // so a single-frame `such that` is byte-identical). Chain one `transform` per `__bN` in dependency
      // order, each carrying prior fields forward and substituting `Col(__bK)` → `p.__bK` in the new operand
      // and finally the predicate body. The carrier reads its inputs (frame `__hN` columns and aliases) as
      // correlated outer refs from inside the HOF lambda — valid on the DBR 18 / Spark 3.4+ floor.
      val suchThat =
        if (bothCols.isEmpty) suchThat0
        else {
          val p0 = s"__c${env.hoistCounter.getAndIncrement()}"
          var arr: SqlExpr    = Call("array", Seq(Struct(Seq(bothCols.head._1 -> bothCols.head._2))))
          var carried         = Seq(bothCols.head._1)
          bothCols.toSeq.tail.foreach { case (n, e) =>
            val p    = s"__c${env.hoistCounter.getAndIncrement()}"
            val body = carried.map(f => f -> (Field(Col(p), f): SqlExpr)) :+ (n -> substCols(e, carried.toSet, p))
            arr      = hof("transform", arr, Lambda(Seq(p), Struct(body)))
            carried  = carried :+ n
          }
          Call("element_at", Seq(hof("transform", arr, Lambda(Seq(p0), substCols(suchThat0, carried.toSet, p0))), int(1)))
        }
      def withCols(r: SqlRel, cols: Seq[(String, SqlExpr)], a: String): SqlRel =
        if (cols.isEmpty) r
        else SqlRel.Select(SqlRel.Proj.star +: cols.map { case (n, e) => SqlRel.Proj(e, n) }, from = SqlRel.AliasRel(r, a))
      val relFrame  = SqlRel.AliasRel(withCols(relBase, relCols.toSeq, "__rbh"), s"__rel_${rel.alias}")
      val accFrame  = SqlRel.AliasRel(withCols(acc, accCols.toSeq, "__ah"), subjLeftAlias(acc))
      val subjectEq = Infix("=", Field(Col(subjLeftAlias(acc)), SubjectId), Field(Col(s"__rel_${rel.alias}"), SubjectId))
      val cond = if (ctxLess) suchThat else Infix("AND", subjectEq, suchThat)
      SqlRel.Join(accFrame, relFrame,
        if (rel.negated) SqlRel.JoinKind.LeftAnti else SqlRel.JoinKind.LeftSemi, on = Some(cond))
    }
    Combined(withRels, bindsRF, qrRF, resolveRef, augTypeOf)
  }

  /** LANG-REFERENCE-FOLLOW-VALUE: realize each `singleton from (<retrieve> X where …)` that is
    * used for its VALUE (not as an existence `with`/`without`) as a correlated LEFT OUTER JOIN projecting the
    * followed resource struct per outer row, and rewrite the sub-query node to an [[AliasRef]] to that
    * projected column. Only a SINGLE-source retrieve-backed query with a correlated `where` and no
    * return/sort/aggregate/relationship/let is diverted here — a System-list `singleton from` stays on the
    * Array path (its source is an array, not a retrieve, so [[isRelationSource]] is false), and anything
    * else falls through to the Array path and fails loudly (CORE-NO-SILENT-MISLOWER). The join keys on the
    * subject (the followed retrieve is patient-scoped) plus the inner `where` (`X.id = <outer>.<ref>`); the
    * followed resource's `__subject_id` is renamed away so the outer frame's columns stay collision-free
    * under a `SELECT *`. Returns the augmented frame, the binds extended with each projected column, and the
    * query with the follow sub-queries replaced by their column references. */
  private def realizeRefFollows(joined: SqlRel, binds: Map[String, SqlExpr], qr: Query, env: SqlEvalEnv,
                                lib: Library, typeOf: Expr => CqlType, resolveRef: SqlColumnLowering.RefResolver,
                                relDefs: Set[String]): (SqlRel, Map[String, SqlExpr], Query) = {
    def isRefFollow(q: Query): Boolean =
      q.sources.sizeIs == 1 && q.relationships.isEmpty && q.aggregate.isEmpty && q.sort.isEmpty &&
        q.returnExpr.isEmpty && q.lets.isEmpty && q.where.isDefined && isRelationSource(q.sources.head.expr, relDefs)

    val clauseExprs: Seq[Expr] =
      qr.where.toSeq ++ qr.returnExpr.toSeq ++ qr.sort.flatMap(_.expr) ++ qr.relationships.map(_.suchThat)
    val follows: Seq[Query] = clauseExprs.flatMap(_.collect { case SingletonFrom(q: Query) if isRefFollow(q) => q }).distinct
    if (follows.isEmpty) return (joined, binds, qr)

    val subst = scala.collection.mutable.LinkedHashMap.empty[Query, Expr]
    var acc   = joined
    var b     = binds
    follows.foreach { fq =>
      val n         = env.hoistCounter.getAndIncrement()
      val src       = fq.sources.head
      val relBase   = aliasedSource(src.alias, src.expr, env, lib, typeOf, relDefs)
      val followCol = s"__reffollow_$n"
      val subjCol   = s"__reffollow_subj_$n"
      // Rename the followed retrieve's `__subject_id` (a `SELECT *` over the join would otherwise duplicate
      // it against the outer frame) and expose the resource struct under a unique `__reffollow_<n>` name.
      val relRenamed = SqlRel.Select(
        projections = Seq(SqlRel.Proj(Col(SubjectId), subjCol), SqlRel.Proj(Col(src.alias), followCol)),
        from = asFrom(relBase, s"__rfsrc_$n"))
      // The correlation `where`, with the inner alias bound to the projected struct and the outer aliases to
      // their frame columns; joined onto the subject key (the retrieve is patient-scoped).
      val corr = lowerClause(fq.where.get, b + (src.alias -> Col(followCol)), typeOf, resolveRef)
      val cond = Infix("AND", Infix("=", Col(SubjectId), Col(subjCol)), corr)
      acc = SqlRel.Join(acc, SqlRel.AliasRel(relRenamed, s"__rfj_$n"), SqlRel.JoinKind.LeftOuter, on = Some(cond))
      b = b + (followCol -> Col(followCol))
      subst += (fq -> AliasRef(followCol))
    }
    def rw(e: Expr): Expr = Expr.coerce(e.transformDown { case SingletonFrom(q: Query) if subst.contains(q) => subst(q) })
    val qr2 = qr.copy(
      where         = qr.where.map(rw),
      returnExpr    = qr.returnExpr.map(rw),
      sort          = qr.sort.map(si => si.expr.map(e => si.copy(expr = Some(rw(e)))).getOrElse(si)),
      relationships = qr.relationships.map(r => r.copy(suchThat = rw(r.suchThat))))
    (acc, b, qr2)
  }

  /** Push a value-set-membership conjunct on the RELATED alias (`M.code in vs`) OUT of a reference-follow
    * `such that` and INTO the related retrieve as a code filter (`[Medication: vs]`), leaving only the
    * correlated reference equality as the join predicate — the SQL twin of `QueryLowering.pushRelMembership`.
    * Only when the related source is a plain CODELESS retrieve; otherwise the conjunct
    * stays and fails loudly rather than being silently dropped. Returns the (possibly rewritten) related
    * source expression and the residual join predicate (`None` if the membership was the only conjunct). */
  private def pushRelMembership(relExpr: Expr, relAlias: String, suchThat: Expr): (Expr, Option[Expr]) = {
    def codelessRetrieve(e: Expr): Boolean = e match {
      case r: Retrieve                          => r.codes.isEmpty
      case As(inner, _, _, cts) if cts.nonEmpty => codelessRetrieve(inner)
      case _                                    => false
    }
    val cs       = RetrieveFilter.conjuncts(suchThat)
    val pushable = cs.flatMap(c => RetrieveFilter.membership(c).filter(_._1 == relAlias).map(c -> _))
    if (pushable.isEmpty || !codelessRetrieve(relExpr)) (relExpr, Some(suchThat))
    else {
      val (conj, (_, path, vs)) = pushable.head // one code filter per retrieve
      def rewrite(e: Expr): Expr = e match {
        case r: Retrieve if r.codes.isEmpty => r.copy(codes = Some(vs), codeProperty = Some(path))
        case As(inner, a, b, c)             => As(rewrite(inner), a, b, c)
        case other                          => other
      }
      (rewrite(relExpr), cs.filterNot(_ eq conj).reduceOption(And(_, _)))
    }
  }

  private def subjLeftAlias(rel: SqlRel): String = "__l"

  /** The set of column identifiers a lowered [[SqlExpr]] references (a `Col(name)` anywhere, minus any
    * names bound by an enclosing HOF lambda). Used by the `such that` hoister to decide which JOIN INPUT a
    * temporal operand roots on (source aliases → the left frame, the related alias → the right frame). */
  private def freeColRefs(e: SqlExpr): Set[String] = e match {
    case Col(n)            => Set(n)
    case Field(b, _)       => freeColRefs(b)
    case Call(_, as)       => as.iterator.flatMap(freeColRefs).toSet
    case Infix(_, l, r)    => freeColRefs(l) ++ freeColRefs(r)
    case Prefix(_, x)      => freeColRefs(x)
    case Postfix(x, _)     => freeColRefs(x)
    case Cast(x, _)        => freeColRefs(x)
    case SqlExpr.Case(ws, oe) => ws.iterator.flatMap { case (c, v) => freeColRefs(c) ++ freeColRefs(v) }.toSet ++ oe.map(freeColRefs).getOrElse(Set.empty)
    case Struct(fs)        => fs.iterator.flatMap { case (_, v) => freeColRefs(v) }.toSet
    case Lambda(ps, b)     => freeColRefs(b) -- ps.toSet
    case Over(fn, pb, ob)  => freeColRefs(fn) ++ pb.flatMap(freeColRefs).toSet ++ ob.flatMap(k => freeColRefs(k.expr)).toSet
    case _: Lit            => Set.empty
  }

  /** Rewrite every `Col(n)` with `n ∈ names` to `Field(Col(carrier), n)` — read a carried let/operand as a
    * struct field of the carrier lambda var `carrier` instead of a bare column. The twin of `freeColRefs`
    * used by the `such that` two-sided-operand carrier ([[combined]]'s `route`): a genuinely two-sided
    * temporal/interval operand (references BOTH join inputs, e.g. `onsetSpan()`'s age arms read the
    * inner alias' onset AND the outer `Patient.birthDate`) can be hoisted onto NEITHER frame, so it is bound
    * ONCE via the scalar-let carrier idiom (the `element_at(transform(array(struct(…)), …))` chain
    * [[letCarrier]] uses) and every re-use reads the field. A shadowing HOF lambda param is left untouched. */
  private def substCols(e: SqlExpr, names: Set[String], carrier: String): SqlExpr = {
    def go(x: SqlExpr): SqlExpr = x match {
      case Col(n) if names(n) => Field(Col(carrier), n)
      case c: Col             => c
      case Field(b, n)        => Field(go(b), n)
      case Call(fn, as)       => Call(fn, as.map(go))
      case Infix(op, l, r)    => Infix(op, go(l), go(r))
      case Prefix(op, x0)     => Prefix(op, go(x0))
      case Postfix(x0, op)    => Postfix(go(x0), op)
      case Cast(x0, ty)       => Cast(go(x0), ty)
      case SqlExpr.Case(ws, oe) => SqlExpr.Case(ws.map { case (c, v) => (go(c), go(v)) }, oe.map(go))
      case Struct(fs)         => Struct(fs.map { case (n, v) => (n, go(v)) })
      case Lambda(ps, b)      => Lambda(ps, substCols(b, names -- ps, carrier))
      case Over(fn, pb, ob)   => Over(go(fn), pb.map(go), ob.map(k => k.copy(expr = go(k.expr))))
      case l: Lit             => l
    }
    go(e)
  }

  /** Apply a query `where`: value-set-membership conjuncts over a retrieve alias become ROW-LEVEL
    * semi-joins (the SQL twin of `QueryLowering.applyWhere`, LANG-TERMINOLOGY); a membership (or a
    * correlated single-source `exists`) BURIED in an `Or`/`Not` — not a top-level conjunct, so not
    * semi-joinable — is LIFTED to a per-row boolean flag column ([[liftMembershipFlags]]); the residual
    * conjuncts then lower as one 3VL predicate over the flag-augmented frame. */
  private def applyWhere(joined: SqlRel, q: Query, env: SqlEvalEnv, lib: Library, binds: Map[String, SqlExpr],
                         typeOf: Expr => CqlType, relDefs: Set[String],
                         resolveRef: SqlColumnLowering.RefResolver): SqlRel = q.where match {
    case None => joined
    case Some(pred) =>
      val (membership, residual) = RetrieveFilter.conjuncts(pred).partition(canSemiJoin(_, q, relDefs))
      val semiJoined = membership.foldLeft(joined)((f, c) => membershipWhere(f, c, q, env, lib, typeOf))
      // A membership / correlated exists buried in an `Or`/`Not` can't be a semi-join; lift each to a
      // per-row boolean flag column and rewrite it to a reference to that column (LANG-TERMINOLOGY).
      val (flagged, residualRw, flagResolveRef) =
        liftMembershipFlags(semiJoined, residual, q, env, lib, binds, typeOf, resolveRef, relDefs)
      residualRw.reduceOption(And(_, _)) match {
        case None    => flagged
        case Some(p) =>
          // ARCH-SQL-CSE: hoist the predicate's duplication-prone temporal/interval operands to `__h<N>`
          // columns of a derived table, then filter on the lowered predicate referencing them — so a
          // `during`/`overlaps` interval operand is emitted ONCE, not re-inlined per `.low`/`.high`
          // access (the OR-of-temporal-`exists` exclusion blowup, SqlHoist).
          val (fromH, pred) = SqlHoist.hoisted(flagged, env.hoistCounter, "__wf")(h =>
            lowerClause(p, binds, typeOf, flagResolveRef, h, true))
          wrapWhere(fromH, pred)
      }
  }

  /** Lift every value-set-membership `InValueSet` — and every correlated single-source `exists` — BURIED
    * in a residual predicate (inside an `Or`/`Not`, so not a top-level semi-join conjunct:
    * `reasonCode in "VS" or exists([Condition:"VS"] O where …)`, `notDoneReason in "VS1" or notDoneReason
    * in "VS2"`) into a per-row BOOLEAN FLAG column: LEFT-join the outer rows that satisfy it — keyed by
    * the outer row identity (subject + each source's ordinal) — tagged `true`, and rewrite the buried node
    * to an `ExpressionRef` to that column (absent outer row → `false` via the returned resolver). The SQL
    * twin of `QueryLowering.liftMembershipFlags`: the terminology expansion / the correlated exists is a
    * RELATION (no compile-time literal set), so a membership/exists in a non-conjunctive position becomes
    * a joined flag rather than a scalar column. Each flag subquery reads the STABLE pre-lift frame
    * (`frame0`) so chaining several flags stays linear, not quadratically nested. Returns the augmented
    * frame, the rewritten predicates, and the resolver that binds the flag columns. */
  private def liftMembershipFlags(frame0: SqlRel, preds: Seq[Expr], q: Query, env: SqlEvalEnv, lib: Library,
                                  binds: Map[String, SqlExpr], typeOf: Expr => CqlType,
                                  resolveRef: SqlColumnLowering.RefResolver,
                                  relDefs: Set[String]): (SqlRel, Seq[Expr], SqlColumnLowering.RefResolver) = {
    var frame = frame0
    val flagCols = scala.collection.mutable.LinkedHashSet.empty[String]
    val memo     = scala.collection.mutable.LinkedHashMap.empty[String, String] // dedup key -> flag col
    // The OUTER row identity for a per-row flag join-back: subject + each source's ordinal.
    val outerKeys = SubjectId +: q.sources.map(s => ordName(s.alias))
    val keyProjs  = outerKeys.map(k => SqlRel.Proj(Col(k)))

    // LEFT-join `matched` (the outer keys that satisfy the condition, tagged `<flagCol> = true`) onto the
    // frame so every kept outer row carries the flag (absent → null → coalesced to false at reference).
    def joinFlag(flagCol: String, matched: SqlRel): Unit = {
      frame = SqlRel.Join(frame, SqlRel.AliasRel(matched, s"__f_$flagCol"), SqlRel.JoinKind.LeftOuter, using = outerKeys)
      flagCols += flagCol
    }

    // The distinct outer keys whose lowered CODE OPERAND has a coding in the value set — the row-level
    // equi-join membership (mirrors [[membershipSemiJoin]]'s explode+equi-semi-join, keyed by the outer
    // identity rather than the resource id). Lowering the code operand (rather than an `alias.path`) is
    // robust to ANY code shape — a plain `Property`, a `SingletonFrom` sub-query, an extension-derived
    // CodeableConcept (a negation-rationale `doNotPerformReason` extension accessor) — since
    // the semi-join needs only its `.coding`, not a navigable path. Reads the STABLE `frame0`.
    def membershipMatched(codeCol: SqlExpr, isList: Boolean, vsId: String, flagCol: String): SqlRel = {
      // A LIST-valued code (`reasonCode: List<CodeableConcept>`) explodes the list, then each element's
      // `coding`; a single CodeableConcept explodes its `coding` directly.
      val codingRows: SqlRel =
        if (isList)
          SqlRel.LateralView(
            SqlRel.LateralView(SqlRel.AliasRel(frame0, "__lf"), Call("explode", Seq(codeCol)), "__ccx", Seq("__cc")),
            Call("explode", Seq(Field(Col("__cc"), "coding"))), "__cx", Seq("__coding"))
        else
          SqlRel.LateralView(SqlRel.AliasRel(frame0, "__lf"),
            Call("explode", Seq(Field(codeCol, "coding"))), "__cx", Seq("__coding"))
      val exploded = SqlRel.Select(
        projections = keyProjs ++ Seq(
          SqlRel.Proj(Field(Col("__coding"), "system"), "system"),
          SqlRel.Proj(Field(Col("__coding"), "code"), "code")),
        from = codingRows)
      SqlRel.Select(
        projections = keyProjs :+ SqlRel.Proj(bool(true), flagCol), distinct = true,
        from = SqlRel.Join(asFrom(exploded, "__ex"), SqlRel.AliasRel(valueSetMount(vsId, lib, env), "__vs"),
          SqlRel.JoinKind.LeftSemi, using = Seq("system", "code")))
    }

    // The distinct outer keys with ≥1 matching inner row for a correlated single-source `exists`: build a
    // synthetic query = the OUTER sources + this inner query as a `with` relationship (its `where` becomes
    // the `such that`), realize it through [[combined]] (correlated join + the value[x]/onsetSpan rewrites
    // over the inner + referenced outer aliases), and distinct the matched outer keys.
    def existsMatched(inner: Query, flagCol: String): SqlRel = {
      val trueLit = Literal("{urn:hl7-org:elm-types:r1}Boolean", Some("true"))
      val rel = RelationshipClause(inner.sources.head.alias, inner.sources.head.expr,
        inner.where.getOrElse(trueLit), negated = false)
      val synth = q.copy(where = None, relationships = Seq(rel), returnExpr = None, sort = Nil, aggregate = None)
      SqlRel.Select(
        projections = keyProjs :+ SqlRel.Proj(bool(true), flagCol), distinct = true,
        from = asFrom(combined(synth, env, lib, typeOf, relDefs).rel, "__ex"))
    }

    // List-cardinality of the value-set membership CODE OPERAND. `typeOf(code)` alone is unreliable
    // here: the code is a bare `alias.path` navigated in isolation, and the source alias is NOT bound
    // in the top-level type ctx, so `typeOf` reports `AnyType` (never a `ListType`) — worse for a
    // het-union alias, whose Choice source type carries no cardinality at all. Resolve the alias's
    // source expression from the query sources and read the property's cardinality off the FHIR model
    // for EACH union arm: a 0..* code element on ANY arm (`reasonCode: List<CodeableConcept>` on both
    // ServiceRequest and MedicationRequest, unioned in one define) means `.coding` is
    // `array<array<coding>>` and MUST list-explode before the coding-explode, else the semi-join key
    // comes out `ARRAY<STRING>` (DATATYPE_MISMATCH). Falls back to `typeOf` for non-`alias.path` shapes.
    def codeIsList(code: Expr): Boolean = {
      val aliasNav = code match {
        case Property(None, Some(a), p)           => Some((a, p))
        case Property(Some(AliasRef(a)), None, p) => Some((a, p))
        case _                                    => None
      }
      aliasNav.flatMap { case (a, p) =>
        q.sources.collectFirst { case QuerySource(`a`, se) => se }.map(se =>
          flattenUnion(se).flatMap(arm => aliasResourceType(arm, typeOf)).exists(FhirModel.isListElement(_, p)))
      }.getOrElse(typeOf(code).isInstanceOf[CqlType.ListType])
    }

    def rw(e: Expr): Expr = e match {
      case InValueSet(code, vs: ValueSetRef) =>
        val vsId = valueSetId(lib, vs.name)
        val col = memo.getOrElseUpdate(s"invs:${code.hashCode}|$vsId", {
          val c = s"__invs_${memo.size}"
          // Lower the CODE OPERAND to a SqlExpr (resolving source aliases via `binds`), then semi-join its
          // coding(s) against the value-set expansion — robust to any code shape (Property / SingletonFrom /
          // extension-derived CodeableConcept), unlike the retrieve-scan semi-join which needs `alias.path`.
          val codeCol = lowerClause(code, binds, typeOf, resolveRef, temporalUdf = true)
          joinFlag(c, membershipMatched(codeCol, codeIsList(code), vsId, c)); c
        })
        ExpressionRef(col, None)
      // A (possibly CORRELATED) single-source relation `exists` buried in an `or`/`not` — restricted to a
      // plain relation source with no return/sort/aggregate/nested relationship; anything else falls
      // through and fails loudly at lowering (CORE-NO-SILENT-MISLOWER), matching the compiler twin.
      case Exists(inner: Query)
          if inner.sources.sizeIs == 1 && inner.aggregate.isEmpty && inner.relationships.isEmpty
            && inner.returnExpr.isEmpty && inner.sort.isEmpty
            && isRelationSource(inner.sources.head.expr, relDefs) =>
        val col = memo.getOrElseUpdate(s"exists:${inner.hashCode}", {
          val c = s"__exists_${memo.size}"
          joinFlag(c, existsMatched(inner, c)); c
        })
        ExpressionRef(col, None)
      case other => other.withNewChildren(other.children.map { case c: Expr => rw(c); case c => c }).asInstanceOf[Expr]
    }

    val out = preds.map(rw)
    val flagResolveRef: SqlColumnLowering.RefResolver =
      name => if (flagCols.contains(name)) Call("coalesce", Seq(Col(name), bool(false))) else resolveRef(name)
    (frame, out, flagResolveRef)
  }

  private def canSemiJoin(c: Expr, q: Query, relDefs: Set[String]): Boolean =
    RetrieveFilter.membership(c).exists { case (alias, _, _) =>
      q.sources.exists { case QuerySource(`alias`, e) => isRelationSource(e, relDefs); case _ => false }
    }

  /** Realize one membership conjunct as a semi-join of the combined frame: keep rows whose alias
    * resource has a coding in the value set (the SQL twin of `QueryLowering.membershipWhere`). The coding
    * array is navigated off the alias's struct column already bound in the frame. */
  private def membershipWhere(frame: SqlRel, conjunct: Expr, q: Query, env: SqlEvalEnv, lib: Library,
                              typeOf: Expr => CqlType): SqlRel = {
    val (alias, path, vs) = RetrieveFilter.membership(conjunct).get
    val srcExpr = q.sources.collectFirst { case QuerySource(`alias`, e) => e }
    val isList  = srcExpr.flatMap(e => aliasResourceType(e, typeOf)).exists(FhirModel.isListElement(_, path))
    membershipSemiJoin(SqlRel.AliasRel(frame, "__m"), Col(alias), path, valueSetId(lib, vs.name), env, isList, lib)
  }

  /** `exists(query)`: the distinct subjects with ≥1 surviving combined row (the SQL twin of
    * `QueryLowering.relationExists`). */
  def relationExists(q: Query, env: SqlEvalEnv, lib: Library, typeOf: Expr => CqlType,
                     relDefs: Set[String] = Set.empty): SqlRel =
    SqlRel.Select(projections = Seq(SqlRel.Proj(Col(SubjectId))),
      from = asFrom(combined(q, env, lib, typeOf, relDefs).rel, "__e"), distinct = true)

  /** The distinct `__subject_id`s having ≥1 instance of the retrieve, reading through the single
    * filtered-retrieve home so the retrieve's value-set / date-range filters both apply (the SQL twin of
    * `FrameLowering.subjectsPresent`). */
  def subjectsPresent(r: Retrieve, env: SqlEvalEnv, lib: Library, typeOf: Expr => CqlType): SqlRel = {
    val rt = localName(r.dataType)
    val read = if (isUnfilteredScan(r)) retrieveMount(Some(r), rt, lib, env)
               else filteredRetrieve(r, rt, env, lib, typeOf)
    SqlRel.Select(projections = Seq(SqlRel.Proj(Col(SubjectId))), from = asFrom(read, "__sp"), distinct = true)
  }

  /** GRAIN-FIRST (ARCH-GRAIN) Relation realization of a plain `return all` list query — the combined
    * source rows projected straight to `__subject_id` + `__ordinal` + `__val`, with NO collect/box round
    * trip (the SQL twin of `QueryLowering.relationFrameDirect`). */
  def relationFrameDirect(q: Query, env: SqlEvalEnv, lib: Library, typeOf: Expr => CqlType,
                          relDefs: Set[String] = Set.empty): SqlRel = {
    val c = combined(q, env, lib, typeOf, relDefs)
    val aliases = c.query.sources.map(_.alias)
    // ARCH-SQL-CSE: hoist the `__val` element's duplication-prone operands onto the combined relation.
    val (fromH, elem) = SqlHoist.hoisted(c.rel, env.hoistCounter)(h =>
      elemProjection(c.query, c.binds, c.typeOf, c.resolveRef, h, true))
    val ord: SqlExpr =
      if (aliases.size == 1) Col(ordName(aliases.head))
      else Struct(aliases.map(a => a -> Col(ordName(a))))
    SqlRel.Select(
      projections = Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(ord, "__ordinal"), SqlRel.Proj(elem, ValueCol)),
      from = asFrom(fromH, "__c"))
  }

  /** GRAIN-FIRST single-key `sort` list query — the rows at their natural grain with a SORTED `__ordinal`
    * via a window `row_number()` re-ordinal (the SQL twin of `QueryLowering.relationFrameSortedDirect`).
    * The secondary natural-ordinal key reproduces the stable tie order of the collected path. */
  def relationFrameSortedDirect(q: Query, env: SqlEvalEnv, lib: Library, typeOf: Expr => CqlType,
                                relDefs: Set[String] = Set.empty): SqlRel = {
    val c = combined(q, env, lib, typeOf, relDefs)
    val aliases = c.query.sources.map(_.alias)
    // ARCH-SQL-CSE: hoist the `__val` element's duplication-prone operands onto the combined relation.
    val (fromH, elem) = SqlHoist.hoisted(c.rel, env.hoistCounter)(h =>
      elemProjection(c.query, c.binds, c.typeOf, c.resolveRef, h, true))
    val natOrd: SqlExpr =
      if (aliases.size == 1) Col(ordName(aliases.head))
      else Struct(aliases.map(a => a -> Col(ordName(a))))
    val item = c.query.sort.head
    val key = sortKeyExpr(Col(ValueCol), item, aliases, c.binds, c.typeOf, c.resolveRef, true)
    // Bind `__val`/`__nat` in an inner projection so the window can order by them.
    val rows = SqlRel.Select(
      projections = Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(natOrd, "__nat"), SqlRel.Proj(elem, ValueCol)),
      from = asFrom(fromH, "__c"))
    val ordinal = Infix("-",
      Over(Call("row_number", Nil), Seq(Col(SubjectId)),
        Seq(SqlRel.SortKey(key, ascending = item.direction != "desc"), SqlRel.SortKey(Col("__nat")))),
      int(1))
    SqlRel.Select(
      projections = Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(ordinal, "__ordinal"), SqlRel.Proj(Col(ValueCol))),
      from = asFrom(rows, "__r"))
  }

  /** An `aggregate` Relation query folded to a per-subject SCALAR (LANG-QUERY-AGGREGATE): the SQL twin of
    * the compiler's boxed-then-`postCollect` fold, but SQL-native. The combined rows are collected into
    * ONE ordered element array per subject (`array_sort` of `(ordinal, val)` by ordinal, then projected to
    * the value — CQL folds in query order), and the CQL fold is Spark's `aggregate(arr, init, (acc, x) ->
    * body)` HOF. LEFT-joined onto `base` so EVERY subject folds (absent → empty array → the `starting`
    * seed). Single-source only (multi-source aggregate is a later increment, like the Array path). */
  def relationAggregate(q0: Query, base: SqlRel, env: SqlEvalEnv, lib: Library, typeOf: Expr => CqlType,
                        relDefs: Set[String] = Set.empty): SqlRel = {
    val agg = q0.aggregate.getOrElse(throw UnsupportedNode("Query.aggregate", "not an aggregate query"))
    if (q0.sources.sizeIs != 1)
      throw UnsupportedNode("Query.aggregate", "aggregate over a multi-source relation query arrives in a later increment")
    // The combined rows at grain (the aggregate/sort clauses do not affect row realization — the fold
    // runs over the natural query order, `__ordinal`).
    val rows = relationFrameDirect(q0.copy(aggregate = None, sort = Nil), env, lib, typeOf, relDefs)
    // One ORDERED element array per subject: collect `(ordinal, val)`, sort by ordinal (string, matching
    // the driver-side ORDER BY __ordinal), project to the value.
    val ordStruct = Struct(Seq("o" -> Col("__ordinal"), "v" -> Col(ValueCol)))
    val cmp = Lambda(Seq("__l", "__r"), SqlExpr.Case(Seq(
      Infix("<", Field(Col("__l"), "o"), Field(Col("__r"), "o")) -> int(-1),
      Infix(">", Field(Col("__l"), "o"), Field(Col("__r"), "o")) -> int(1)), Some(int(0))))
    val arr = Call("transform", Seq(
      Call("array_sort", Seq(Call("collect_list", Seq(ordStruct)), cmp)),
      Lambda(Seq("__e"), Field(Col("__e"), "v"))))
    val arrRel = SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(arr, "__arr")),
      from = asFrom(rows, "__ar"), groupBy = Seq(Col(SubjectId)))
    // The CQL fold as a Spark `aggregate` HOF: the accumulator identifier binds to `__acc`, the source
    // alias to the element `__x`. An absent subject folds over the empty array to the `starting` seed.
    val init = SqlColumnLowering.lower(agg.starting, typeOf = typeOf, temporalUdf = true)
    val binds: Map[String, SqlExpr] = Map(agg.identifier -> Col("__acc"), q0.sources.head.alias -> Col("__x"))
    val body = SqlColumnLowering.lower(bindScopes(agg.body),
      resolveAlias = a => binds.getOrElse(a, SqlColumnLowering.rejectRefs(a)), typeOf = typeOf,
      temporalUdf = true)
    val fix = foldType(agg.starting, typeOf)
    val fold = Call("aggregate", Seq(
      Call("coalesce", Seq(Col("__arr"), Call("array", Nil))), fix(init), Lambda(Seq("__acc", "__x"), fix(body))))
    SqlRel.Select(Seq(SqlRel.Proj(Col(SubjectId)), SqlRel.Proj(fold, ValueCol)),
      from = SqlRel.Join(asFrom(base, "__b"), SqlRel.AliasRel(arrRel, "__agg"), SqlRel.JoinKind.LeftOuter, using = Seq(SubjectId)))
  }

  /** LANG-QUERY-AGGREGATE: Spark's `aggregate` requires the fold body to have exactly the seed's type, and
    * decimal arithmetic changes precision and scale (`DECIMAL(38,8) + DECIMAL(28,8)` is `DECIMAL(38,6)` under
    * precision loss), so a `Decimal` accumulator fails analysis. Pin the seed and the body to CQL's Decimal,
    * 28 digits with 8 after the point (CQL spec, Types: Decimal), i.e. `DECIMAL(28,8)`. Other accumulator
    * types keep their natural lowering. */
  private def foldType(starting: Expr, typeOf: Expr => CqlType): SqlExpr => SqlExpr =
    typeOf(starting) match {
      case CqlType.DecimalType => e => Cast(e, "DECIMAL(28,8)")
      case _                   => identity
    }

  /** The collected/projected element for one combined row: the `return` shape, else the single source's
    * struct or the multi-source alias tuple (mirrors the `elem` computation in the `QueryLowering`
    * relation paths). */
  private def elemProjection(q: Query, binds: Map[String, SqlExpr], typeOf: Expr => CqlType,
                             resolveRef: SqlColumnLowering.RefResolver,
                             hoist: SqlColumnLowering.Hoister = SqlColumnLowering.noHoist,
                             temporalUdf: Boolean = false): SqlExpr = q.returnExpr match {
    case Some(r)                   => lowerClause(r, binds, typeOf, resolveRef, hoist, temporalUdf)
    case None if q.sources.size == 1 => binds(q.sources.head.alias)
    case None                      => Struct(q.sources.map(s => s.alias -> binds(s.alias)))
  }

  /** The sort key over a (returned) list element (mirrors `QueryLowering.sortKey`): ByColumn navigates
    * the element's field; ByExpression lowers its key over the element; ByDirection is the element. */
  private def sortKeyExpr(elem: SqlExpr, item: SortItem, aliases: Seq[String], binds: Map[String, SqlExpr],
                          typeOf: Expr => CqlType, resolveRef: SqlColumnLowering.RefResolver,
                          temporalUdf: Boolean = false): SqlExpr =
    item.path match {
      case Some(p) => Field(elem, p)
      case None    => item.expr match {
        case Some(e) => SqlColumnLowering.lower(bindScopes(e), resolveRef,
          nm => if (aliases.size == 1) elem else Field(elem, nm), typeOf, temporalUdf = temporalUdf)
        case None    => elem
      }
    }

  // ===========================================================================
  // Schema-tolerant FHIR value[x] projection (LANG-CHOICE-MODEL, DATA-SCHEMA-CONTRACT). PURE IR->IR
  // rewrites copied VERBATIM from `QueryLowering` — they touch only the IR ADT + the static `FhirModel`,
  // no Spark `Column`/`DataType`, so they carry over to the SQL target unchanged (a re-target would be a
  // needless divergence). They resolve each `As/Is/IsNull(ToValue(<alias>.<value[x]>))` against the
  // model's typed slots so navigation lands on a real physical column, and constant-fold the resulting
  // literal type-tests so the SQL never spells a statically-dead branch.
  // ===========================================================================

  private val FhirChoiceBool = "{urn:hl7-org:elm-types:r1}Boolean"

  /** Unwrap a Phase-B `As(_, choiceTypes)` to the inner relation. */
  private def unionArmInner(e: Expr): Expr = e match {
    case As(inner, _, _, cts) if cts.nonEmpty => unionArmInner(inner)
    case other                                => other
  }

  private def flatResourceMembers(t: CqlType): Seq[CqlType] = t match {
    case CqlType.ListType(el)   => flatResourceMembers(el)
    case CqlType.ChoiceType(ms) => ms.flatMap(flatResourceMembers)
    case n: CqlType.NamedType   => Seq(n)
    case _                      => Nil
  }
  private def unionMembers(u: Union, typeOf: Expr => CqlType): Seq[CqlType] =
    SqlColumnLowering.dedupByTag(flattenUnion(u).flatMap(a => flatResourceMembers(typeOf(unionArmInner(a)))))
  private def isHetResourceUnion(u: Union, typeOf: Expr => CqlType): Boolean = {
    val ms = unionMembers(u, typeOf)
    ms.sizeIs > 1 && ms.forall { case _: CqlType.NamedType => true; case _ => false }
  }

  /** The FHIR `value[x]` slot suffixes a System type can physically land on, in preference order — the
    * caller picks the first the STATIC MODEL actually declares for that element.
    *
    * A candidate LIST rather than one suffix, because a System.Quantity is not one FHIR datatype but
    * THREE: `Quantity`, `Age` and `Duration` are all `Quantity` in CQL (`Condition.onsetAge`,
    * `Timing.boundsDuration`, `Dosage.doseQuantity`). Returning only `"Quantity"` made
    * `Condition.onset as Quantity` look for a non-existent `onsetQuantity` slot, resolve to `None`, and
    * lower the whole AGE arm of a helper library's `onsetSpan`/`abatementSpan` — and hence its
    * prevalence interval — to a CONSTANT NULL (a degenerate `CASE WHEN NULL IS NULL THEN NULL` off an
    * empty choice placeholder). Every Condition with an age-coded onset/abatement silently left the
    * population, in all four measures (`LANG-CHOICE-MODEL`, `CORE-NO-SILENT-MISLOWER`).
    *
    * `"Quantity"` stays FIRST so the exact-name case (`valueQuantity`, `doseQuantity`) resolves as before
    * and its emission is byte-unchanged; `Age`/`Duration` are only reached when no `…Quantity` slot is
    * declared, so no element can resolve to two slots. */
  private def fhirSlotSuffixes(sysType: String): Seq[String] = CqlType.parse(sysType) match {
    case _: CqlType.DateTimeType                                             => Seq("DateTime")
    case _: CqlType.DateType                                                 => Seq("Date")
    case _: CqlType.TimeType                                                 => Seq("Time")
    case _: CqlType.QuantityType                                             => Seq("Quantity", "Age", "Duration")
    case CqlType.IntervalType(_: CqlType.DateTimeType | _: CqlType.DateType) => Seq("Period")
    case CqlType.IntervalType(_: CqlType.QuantityType)                       => Seq("Range")
    case CqlType.BooleanType                                                 => Seq("Boolean")
    case CqlType.IntegerType                                                 => Seq("Integer")
    case CqlType.StringType                                                  => Seq("String")
    case CqlType.NamedType("Concept")                                        => Seq("CodeableConcept")
    case CqlType.NamedType(n)                                                => Seq(n)
    case _                                                                   => Nil
  }

  /** The declared slot for `sysType` on choice element `path` of FHIR type `rt`, or `None`. */
  private def fhirSlotOn(rt: String, path: String, sysType: String): Option[String] = {
    val declared = ViewSlots.choiceSlots(rt, path)
    fhirSlotSuffixes(sysType).map(path + _).find(declared.contains)
  }

  /** `valueXConverter` for a Quantity target already yields the System.Quantity `{value, unit}` shape, so
    * re-wrapping it in the original `As … as Quantity` would normalize a SECOND time — reading `.code`
    * off a struct that no longer has one. Keep the converter bare for that target; every other target
    * still carries its `As` (the cast is meaningful there). */
  private def asOrBare(conv: Expr, asType: String, strict: Boolean): Expr =
    if (CqlType.parse(asType).isInstanceOf[CqlType.QuantityType]) conv
    else As(conv, asType, strict, Nil)

  private def valueXConverter(sysType: String, slot: Expr): Expr = CqlType.parse(sysType) match {
    case _: CqlType.DateTimeType => FunctionRef("ToDateTime", Some("FHIRHelpers"), Seq(slot))
    case _: CqlType.DateType     => FunctionRef("ToDate", Some("FHIRHelpers"), Seq(slot))
    // Convert a Quantity-family slot EXPLICITLY rather than leaving the raw slot for the enclosing
    // `As … as Quantity` to normalize. That normalization is guarded by `isQuantityLikeType(typeOf(…))`,
    // and `typeOf` cannot see a slot this rewrite has only just synthesized, so an `Age`/`Duration` slot
    // fell through to the SCALAR promotion and emitted `CAST(<the Age struct> AS DECIMAL(28,8))` —
    // a hard Spark analysis failure once `fhirSlotSuffixes` made those slots reachable at all
    // (`Procedure.performedAge`).
    // `ToQuantity` lowers to the same `{value, unit}` struct the `As` path produced, so emission for the
    // already-working `…Quantity` slots is byte-unchanged; see `stripRedundantQuantityCast`.
    case _: CqlType.QuantityType => FunctionRef("ToQuantity", Some("FHIRHelpers"), Seq(slot))
    case CqlType.IntervalType(_: CqlType.DateTimeType | _: CqlType.DateType) =>
      FunctionRef("ToInterval", Some("FHIRHelpers"), Seq(slot))
    case CqlType.IntervalType(_: CqlType.QuantityType) =>
      def bound(field: String): Expr =
        FunctionRef("ToQuantity", Some("FHIRHelpers"), Seq(Property(Some(slot), None, field)))
      IntervalExpr(Some(bound("low")), Some(bound("high")), lowClosed = true, highClosed = true)
    case _                       => slot
  }

  private def choiceTagOf(t: CqlType): String = t match {
    case _: CqlType.QuantityType  => "Quantity"
    case _: CqlType.DateType      => "Date"
    case _: CqlType.DateTimeType  => "DateTime"
    case _: CqlType.TimeType      => "Time"
    case CqlType.IntervalType(p)  => s"Interval<${choiceTagOf(p)}>"
    case other                    => other.show
  }
  private def sameChoiceTag(a: String, b: String): Boolean =
    choiceTagOf(CqlType.parse(a)) == choiceTagOf(CqlType.parse(b))

  private def fuseChoiceProjection(e: Expr): Expr = Expr.coerce(e.transformDown {
    case As(CqlCase(c, items, els), t, s, Nil) =>
      CqlCase(c, items.map(i => CaseItem(i.when, As(i.thenExpr, t, s, Nil))), As(els, t, s, Nil))
    case Is(CqlCase(c, items, els), t) =>
      CqlCase(c, items.map(i => CaseItem(i.when, Is(i.thenExpr, t))), Is(els, t))
    case As(If(c, th, el), t, s, Nil) => If(c, As(th, t, s, Nil), As(el, t, s, Nil))
    case Is(If(c, th, el), t)         => If(c, Is(th, t), Is(el, t))
    case As(As(inner @ As(_, innerT, _, _), _, _, cts), t2, s, Nil) if cts.nonEmpty =>
      if (sameChoiceTag(innerT, t2)) inner else As(NullExpr(), t2, s, Nil)
    case Is(As(As(_, innerT, _, _), _, _, cts), t2) if cts.nonEmpty =>
      Literal(FhirChoiceBool, Some(sameChoiceTag(innerT, t2).toString))
  })

  private def boolLitExpr(b: Boolean): Expr = Literal(FhirChoiceBool, Some(b.toString))
  private def asBoolLit(e: Expr): Option[Boolean] = e match {
    case Literal(t, Some("true"))  if t.endsWith("Boolean") => Some(true)
    case Literal(t, Some("false")) if t.endsWith("Boolean") => Some(false)
    case _                                                  => None
  }

  private def foldConstants(e: Expr): Expr = Expr.coerce(e.transformUp {
    case If(c, t, el) => asBoolLit(c) match {
      case Some(true)  => t
      case Some(false) => el
      case None        => if (t == el) t else If(c, t, el)
    }
    case CqlCase(None, items, els) =>
      val kept = scala.collection.mutable.ArrayBuffer.empty[CaseItem]
      var result = els; var done = false
      items.foreach { it => if (!done) asBoolLit(it.when) match {
        case Some(false) =>
        case Some(true)  => result = it.thenExpr; done = true
        case None        => kept += it
      }}
      if (kept.isEmpty) result
      else if ((kept.map(_.thenExpr) :+ result).distinct.size == 1) result
      else CqlCase(None, kept.toSeq, result)
    case And(a, b) => (asBoolLit(a), asBoolLit(b)) match {
      case (Some(false), _) | (_, Some(false)) => boolLitExpr(false)
      case (Some(true), _)                     => b
      case (_, Some(true))                     => a
      case _                                   => And(a, b)
    }
    case Or(a, b) => (asBoolLit(a), asBoolLit(b)) match {
      case (Some(true), _) | (_, Some(true)) => boolLitExpr(true)
      case (Some(false), _)                  => b
      case (_, Some(false))                  => a
      case _                                 => Or(a, b)
    }
    case Not(o)                         => asBoolLit(o).map(b => boolLitExpr(!b)).getOrElse(Not(o))
    case Is(NullExpr(), _)              => boolLitExpr(false)
    case Is(As(NullExpr(), _, _, _), _) => boolLitExpr(false)
  })

  /** Resolve a value[x] `O.value as FHIR.T` extractor to its physical typed slot from the STATIC FHIR
    * model (copied verbatim from `QueryLowering.rewriteChoiceValueXStatic` — pure IR->IR). */
  private def rewriteChoiceValueXStatic(q: Query, aliasTypes: Map[String, String]): Query = {
    def slot(alias: String, path: String, sysType: String): Option[String] =
      aliasTypes.get(alias).flatMap(rt => fhirSlotOn(rt, path, sysType))
    def choiceSlots(alias: String, path: String): Seq[String] =
      aliasTypes.get(alias).toSeq.flatMap(rt => ViewSlots.choiceSlots(rt, path))
    def prop(alias: String, name: String): Expr = Property(None, Some(alias), name)
    def boolLit(b: Boolean): Expr = Literal(FhirChoiceBool, Some(b.toString))

    def rootAlias(e: Expr): Option[String] = e match {
      case AliasRef(a)        => Some(a)
      case As(inner, _, _, _) => rootAlias(inner)
      case _                  => None
    }
    object ToValueChoice {
      def unapply(e: Expr): Option[(String, String)] = e match {
        case FunctionRef("ToValue", Some("FHIRHelpers"), Seq(Property(None, Some(a), p)))   => Some((a, p))
        case FunctionRef("ToValue", Some("FHIRHelpers"), Seq(Property(Some(src), None, p))) => rootAlias(src).map(_ -> p)
        case _                                                                              => None
      }
    }
    object QuantityChoice {
      def unapply(e: Expr): Option[(String, String)] = e match {
        case Property(None, Some(a), p)   => Some((a, p))
        case Property(Some(src), None, p) => rootAlias(src).map(_ -> p)
        case _                            => None
      }
    }
    def underlyingChoiceElement(e: Expr): Option[(String, String)] = e match {
      case ToValueChoice(a, p)         => Some((a, p))
      case As(inner, _, _, _)          => underlyingChoiceElement(inner)
      case Property(Some(inner), _, _) => underlyingChoiceElement(inner)
      case _                           => None
    }

    def rwValueX(e: Expr): Expr = Expr.coerce(e.transformDown {
      case As(operand, asType, strict, cts) if cts.nonEmpty =>
        underlyingChoiceElement(operand) match {
          case Some((a, p)) if aliasTypes.contains(a) && FhirModel.isChoiceElement(p) =>
            val live = cts.filter(m => slot(a, p, m).isDefined)
            if (live.nonEmpty && live.size < cts.size) As(operand, asType, strict, live)
            else As(operand, asType, strict, cts)
          case _ => As(operand, asType, strict, cts)
        }
      case As(ToValueChoice(a, p), asType, strict, Nil)
          if aliasTypes.contains(a) && FhirModel.isChoiceElement(p) =>
        slot(a, p, asType) match {
          case Some(s) => asOrBare(valueXConverter(asType, prop(a, s)), asType, strict)
          case None    => As(NullExpr(), asType, strict, Nil)
        }
      case Is(ToValueChoice(a, p), isType)
          if aliasTypes.contains(a) && FhirModel.isChoiceElement(p) =>
        slot(a, p, isType) match {
          case Some(s) => Not(IsNull(prop(a, s)))
          case None    => boolLit(false)
        }
      case IsNull(ToValueChoice(a, p))
          if aliasTypes.contains(a) && FhirModel.isChoiceElement(p) =>
        choiceSlots(a, p).map(s => IsNull(prop(a, s)): Expr).reduceOption(And(_, _)).getOrElse(boolLit(true))
      case FunctionRef("ToQuantity", lib @ Some("FHIRHelpers"), Seq(QuantityChoice(a, p)))
          if aliasTypes.contains(a) && FhirModel.isChoiceElement(p) =>
        slot(a, p, "Quantity") match {
          case Some(s) => FunctionRef("ToQuantity", lib, Seq(prop(a, s)))
          case None    => NullExpr()
        }
      // `(choice.low as Quantity)` in a helper's age-range arm (`onsetSpan`/`abatementSpan`:
      // `when choice is Interval<Quantity> then Interval[Patient.birthDate + (choice.low as Quantity), …]`)
      // reads the AGE-RANGE slot's bound. Test the target through `CqlType.parse`, NEVER a literal
      // "Quantity": an `As` carrying an `asTypeSpecifier` (rather than `asType`) loads with the raw ELM
      // QName `{urn:hl7-org:elm-types:r1}Quantity` (ElmCodec `typeSpecName` does not strip the brace
      // prefix), so a literal match silently fell through to the `Interval<DateTime>` case below — the arm
      // then read `onsetPeriod.start`/`abatementPeriod.start` and the residual `as Quantity` degenerated to
      // a SCALAR promotion, emitting `CAST(<dateTime string> AS DECIMAL(28,8))` with the dimensionless unit
      // '1' (runtime CAST_INVALID_INPUT). Must precede the `Interval<DateTime>` bound case.
      case As(Property(Some(ToValueChoice(a, p)), _, lh), asType, strict, Nil)
          if (lh == "low" || lh == "high") && CqlType.parse(asType).isInstanceOf[CqlType.QuantityType] &&
            aliasTypes.contains(a) && FhirModel.isChoiceElement(p) =>
        slot(a, p, "Interval<Quantity>") match {
          case Some(s) => FunctionRef("ToQuantity", Some("FHIRHelpers"), Seq(Property(Some(prop(a, s)), None, lh)))
          case None    => As(NullExpr(), asType, strict, Nil)
        }
      case Property(Some(ToValueChoice(a, p)), _, lh)
          if (lh == "low" || lh == "high") && aliasTypes.contains(a) && FhirModel.isChoiceElement(p) =>
        slot(a, p, "Interval<DateTime>") match {
          case Some(s) => FunctionRef("ToDateTime", Some("FHIRHelpers"),
                            Seq(Property(Some(prop(a, s)), None, if (lh == "low") "start" else "end")))
          case None    => NullExpr()
        }
    })

    def clause(e: Expr): Expr = foldConstants(fuseChoiceProjection(rwValueX(e)))
    q.copy(
      where = q.where.map(clause),
      returnExpr = q.returnExpr.map(clause),
      relationships = q.relationships.map(r => r.copy(suchThat = clause(r.suchThat))),
      lets = q.lets.map(l => l.copy(expr = clause(l.expr))),
      sort = q.sort.map(si => si.copy(expr = si.expr.map(clause))))
  }

  private def elemT(t: CqlType): CqlType = t match { case CqlType.ListType(el) => el; case o => o }

  /** A best-effort CqlType resolver over a query's IN-SCOPE names (source/relationship aliases + lets),
    * for NESTED value[x] slot dispatch (LANG-CHOICE-MODEL). `typeOf` is context-free, so a choice element
    * reached through a `let`/alias navigation chain (`timing.repeat.bounds` — `timing` is a `let`
    * off `R.dosageInstruction`) has no alias root the by-name rewrite can key on. This walks the chain,
    * seeding aliases from `typeOf` (already scope-aware for the ENCLOSING query via combined's augTypeOf)
    * and folding lets in dependency order, navigating each `Property` step through the static model. Falls
    * back to `typeOf` for anything outside the scope. */
  private def scopeTypeResolver(q: Query, typeOf: Expr => CqlType): Expr => CqlType = {
    def nav(t: CqlType, p: String): CqlType = t match {
      // navType resolves a plain element; a navigation ALREADY dispatched to a value[x] slot
      // (`doseAndRate.doseQuantity`) is typed by slotType (navType skips choice slots).
      case CqlType.NamedType(n) => FhirModel.navType(n, p).orElse(FhirModel.slotType(n, p)).getOrElse(CqlType.AnyType)
      case _                    => CqlType.AnyType
    }
    def resolve(e: Expr, scope: Map[String, CqlType]): CqlType = e match {
      case AliasRef(n)                                        => scope.getOrElse(n, typeOf(e))
      case QueryLetRef(n)                                     => scope.getOrElse(n, typeOf(e))
      case Property(Some(b), _, p)                            => nav(resolve(b, scope), p)
      case Property(None, Some(sc), p)                        => nav(scope.getOrElse(sc, CqlType.AnyType), p)
      case SingletonFrom(o)                                   => elemT(resolve(o, scope))
      case FunctionRef("ToValue", Some("FHIRHelpers"), Seq(a)) => resolve(a, scope)
      case As(_, t, _, _)                                     => CqlType.parse(t)
      case _                                                  => typeOf(e)
    }
    // Sources/relationships fold like lets: a source EXPR may navigate an OUTER alias (`O.component`,
    // an inner query over an Observation's components) — resolve it through `resolve` (scope-aware, roots via
    // `typeOf`), NOT raw `typeOf` (which gives a bare `Property(outerAlias, …)` → Any and collapses the alias
    // type). An earlier source is in scope for a later one (multi-source).
    val srcs = (q.sources.map(s => s.alias -> s.expr) ++ q.relationships.map(r => r.alias -> r.expr))
      .foldLeft(Map.empty[String, CqlType]) { case (m, (a, e)) => m + (a -> elemT(resolve(e, m))) }
    val scope = q.lets.foldLeft(srcs) { (m, l) => m + (l.identifier -> resolve(l.expr, m)) }
    e => resolve(e, scope)
  }

  /** Typed value[x] dispatch (LANG-CHOICE-MODEL): resolve `ToValue(<base>.<choice>)` to the typed slot of
    * `<base>`'s STATIC FHIR type, for any base the resolver types, not only a query alias. Shared by the
    * nested query rewrite ([[rewriteNestedChoiceValueX]], a base reached through a `let`/alias chain) and by
    * a scalar define body ([[SqlFrameLowering]]), where the base is a define reference that holds one
    * resource (`"Only Result".value is Quantity`, the `FhirChoiceValueOffDefine` conformance fixture). There
    * is no alias to key on there, so without this the bare `.value` read the whole resource
    * struct.
    *
    * - `As(choice, T)` reads the `T` slot through its FHIRHelpers converter.
    * - `Is(choice, T)` is "the `T` slot is present", and `false` when the type declares no `T` slot.
    * - `IsNull(choice)` is "every declared slot is null" (CQL `is null` on a choice: no member is set).
    *
    * `Is`/`IsNull` rewrite only when the model declares slots for the element, so an unmodeled base is left
    * as it was rather than folded to a constant. `letMap` resolves a `let` that binds the raw choice value
    * (`r: ToValue(doseAndRate.dose)` used as `As(r, Interval<Quantity>)`). */
  private[lower] def rewriteTypedChoiceValueX(e: Expr, stype: Expr => CqlType,
                                              letMap: Map[String, Expr] = Map.empty): Expr = {
    def declared(base: Expr, p: String): Seq[String] = stype(base) match {
      case CqlType.NamedType(rt) => ViewSlots.choiceSlots(rt, p)
      case _                     => Nil
    }
    def slot(base: Expr, p: String, sysType: String): Option[String] = stype(base) match {
      case CqlType.NamedType(rt) => fhirSlotOn(rt, p, sysType)
      case _                     => None
    }
    def at(base: Expr, s: String): Expr = Property(Some(base), None, s)
    object TypedChoice {
      def unapply(e: Expr): Option[(Expr, String)] = e match {
        case FunctionRef("ToValue", Some("FHIRHelpers"), Seq(Property(Some(base), None, p)))
            if FhirModel.isChoiceElement(p) => Some((base, p))
        case QueryLetRef(n) => letMap.get(n).flatMap(unapply)
        case _ => None
      }
    }
    Expr.coerce(e.transformDown {
      case As(TypedChoice(base, p), asType, strict, Nil) if slot(base, p, asType).isDefined =>
        asOrBare(valueXConverter(asType, at(base, slot(base, p, asType).get)), asType, strict)
      case Is(TypedChoice(base, p), isType) if declared(base, p).nonEmpty =>
        slot(base, p, isType).map(s => Not(IsNull(at(base, s))): Expr).getOrElse(boolLitExpr(false))
      case IsNull(TypedChoice(base, p)) if declared(base, p).nonEmpty =>
        declared(base, p).map(s => IsNull(at(base, s)): Expr).reduce(And(_, _))
    })
  }

  /** NESTED value[x] dispatch (LANG-CHOICE-MODEL): a `<base>.<choice>[x]` reached through a `let`/alias
    * navigation chain (not an alias-rooted `ToValue(alias.value)`, which [[rewriteChoiceValueXStatic]]
    * handles) — a medication-request period helper's `timing.repeat.bounds as Interval<DateTime>` and
    * `doseAndRate.dose`. The base's type is resolved via the query-scope resolver; the `as` cast (or the
    * ambient sysType for a bare read) picks the typed slot off the model, and the FHIRHelpers converter
    * (`ToInterval`/`ToDate…`/`ToQuantity`) is applied so downstream `start of`/`.value` read the slot, not
    * a literal `bounds` field. Runs AFTER the alias-rooted rewrites (which have already resolved every
    * `ToValue(alias.value)` to a concrete slot, so this never re-touches them). */
  private def rewriteNestedChoiceValueX(q: Query, typeOf: Expr => CqlType): Query = {
    val stype = scopeTypeResolver(q, typeOf)
    val letMap: Map[String, Expr] = q.lets.map(l => l.identifier -> l.expr).toMap
    def rw(e: Expr): Expr = rewriteTypedChoiceValueX(e, stype, letMap)
    q.copy(
      where = q.where.map(rw),
      returnExpr = q.returnExpr.map(rw),
      relationships = q.relationships.map(r => r.copy(suchThat = rw(r.suchThat))),
      lets = q.lets.map(l => l.copy(expr = rw(l.expr))),
      sort = q.sort.map(si => si.copy(expr = si.expr.map(rw))))
  }

  /** Resolve a navigation on a HETEROGENEOUS-union alias (LANG-CHOICE-RESOURCE) by dispatching PER MEMBER
    * over the discriminated Choice struct ([[SqlFrameLowering.choiceTagArm]] / `hetUnionRel`): each
    * navigation reads the matching member slot `I.as_<M>.<slot>` (null on the other arms, so per row
    * exactly one contributes) and Coalesces/Ors across members. Copied VERBATIM from
    * `QueryLowering.rewriteHetUnionNav` (pure IR->IR — the member slot is a plain `Property` over the
    * struct column), only `ColumnLowering.choiceSlot` re-targeted to [[SqlColumnLowering.choiceSlot]].
    * Runs BEFORE the single-type rewrites (which then see no het-union alias). */
  private[lower] def rewriteHetUnionNav(q: Query, hetMembers: Map[String, Seq[CqlType]],
                                        flat: Boolean = false): Query = {
    // FLATTEN (choiceTagArmFlat): the demand slots live at the TOP LEVEL of the discriminated struct, so a
    // navigation is a shallow `alias.<slot>` read — drop the per-arm `asFHIR_<M>` level. Every member then
    // maps to the SAME flat slot, so the per-member Coalesce/Or collapses (dedup).
    // EMIT-SQL-FLAT-CHOICE: the het-union navigation stays a fully-typed `Property(alias, seg)` — the same
    // node the flat carrier uses, so it types correctly (the slot's modeled type) with no shim. That slot
    // reads a native top-level field of the flat carrier struct at the ONE navigation leaf
    // ([[SqlColumnLowering]]), NOT via a typeless marker minted here.
    def memberNav(alias: String, m: CqlType, seg: String): Expr =
      if (flat) Property(None, Some(alias), seg)
      else Property(Some(Property(None, Some(alias), SqlColumnLowering.choiceSlot(m))), None, seg)
    def dedup(es: Seq[Expr]): Seq[Expr] = if (flat) es.distinct else es
    def vxSlot(m: CqlType, p: String, sysType: String): Option[String] = m match {
      case CqlType.NamedType(rt) => fhirSlotOn(rt, p, sysType)
      case _ => None
    }
    def vxSlots(m: CqlType, p: String): Seq[String] = m match {
      case CqlType.NamedType(rt) => ViewSlots.choiceSlots(rt, p)
      case _                     => Nil
    }
    def vxSlotFhir(m: CqlType, p: String, fhirType: String): Option[String] = m match {
      case CqlType.NamedType(rt) =>
        val s = p + localName(fhirType).capitalize
        Option(s).filter(ViewSlots.choiceSlots(rt, p).contains)
      case _ => None
    }
    def hasField(m: CqlType, p: String): Boolean = m match {
      case CqlType.NamedType(rt) => FhirModel.navType(rt, p).isDefined
      case _                     => false
    }
    def coalesceOpt(es: Seq[Expr]): Option[Expr] = es match {
      case Seq()  => None
      case Seq(x) => Some(x)
      case xs     => Some(Coalesce(xs))
    }
    def hetProp(e: Expr): Option[(String, String)] = e match {
      case Property(None, Some(a), path)           if hetMembers.contains(a) => Some((a, path))
      case Property(Some(AliasRef(a)), None, path) if hetMembers.contains(a) => Some((a, path))
      case _                                                                 => None
    }
    object HetToValue {
      def unapply(e: Expr): Option[(String, String)] = e match {
        case FunctionRef("ToValue", Some("FHIRHelpers"), Seq(p)) => hetProp(p)
        case _                                                   => None
      }
    }
    def rw(e: Expr): Expr = Expr.coerce(e.transformDown {
      case As(HetToValue(a, p), asType, strict, Nil) =>
        val navs = hetMembers(a).flatMap(m => vxSlot(m, p, asType).map(s => valueXConverter(asType, memberNav(a, m, s))))
        As(coalesceOpt(dedup(navs)).getOrElse(NullExpr()), asType, strict, Nil)
      case As(p0, asType, strict, cts) if hetProp(p0).exists { case (a, p) => hetMembers(a).exists(m => vxSlotFhir(m, p, asType).isDefined) } =>
        val (a, p) = hetProp(p0).get
        val navs = hetMembers(a).flatMap(m => vxSlotFhir(m, p, asType).map(s => memberNav(a, m, s)))
        As(coalesceOpt(dedup(navs)).getOrElse(NullExpr()), asType, strict, cts)
      case Is(p0, isType) if hetProp(p0).exists { case (a, p) => hetMembers(a).exists(m => vxSlotFhir(m, p, isType).isDefined) } =>
        val (a, p) = hetProp(p0).get
        dedup(hetMembers(a).flatMap(m => vxSlotFhir(m, p, isType).map(s => Not(IsNull(memberNav(a, m, s))): Expr)))
          .reduceOption(Or(_, _)).getOrElse(Literal(FhirChoiceBool, Some("false")))
      case Is(HetToValue(a, p), isType) =>
        val fs = hetMembers(a).flatMap(m => vxSlot(m, p, isType).map(s => Not(IsNull(memberNav(a, m, s))): Expr))
        dedup(fs).reduceOption(Or(_, _)).getOrElse(Literal(FhirChoiceBool, Some("false")))
      case IsNull(HetToValue(a, p)) =>
        val navs = hetMembers(a).flatMap(m => vxSlots(m, p).map(s => IsNull(memberNav(a, m, s)): Expr))
        dedup(navs).reduceOption(And(_, _)).getOrElse(Literal(FhirChoiceBool, Some("true")))
      case FunctionRef("ToQuantity", lib @ Some("FHIRHelpers"), Seq(p)) if hetProp(p).isDefined =>
        val (a, path) = hetProp(p).get
        val navs = hetMembers(a).flatMap(m => vxSlot(m, path, "Quantity").map(s => FunctionRef("ToQuantity", lib, Seq(memberNav(a, m, s)))))
        coalesceOpt(dedup(navs)).getOrElse(NullExpr())
      case FunctionRef("ToValue", tv @ Some("FHIRHelpers"), Seq(p)) if hetProp(p).exists { case (a, path) => hetMembers(a).exists(hasField(_, path)) } =>
        val (a, path) = hetProp(p).get
        val navs = hetMembers(a).collect { case m if hasField(m, path) => FunctionRef("ToValue", tv, Seq(memberNav(a, m, path))): Expr }
        coalesceOpt(dedup(navs)).getOrElse(NullExpr())
      case e0: Expr if hetProp(e0).exists { case (a, path) => hetMembers(a).exists(hasField(_, path)) } =>
        val (a, path) = hetProp(e0).get
        // Under FLATTEN (the default) the demanded fields live at the TOP LEVEL of the discriminated struct
        // (choiceTagArmFlat), so a nav is a shallow top-level read — all members map to the same flat slot,
        // collapsing to ONE expr after dedup. This native read is what makes choice navigation Photon-eligible
        // and bounded in analysis; the former opaque `cql_choice_nav_*` UDF is retired (EMIT-SQL-FLAT-CHOICE).
        val navs = hetMembers(a).collect { case m if hasField(m, path) => memberNav(a, m, path) }
        coalesceOpt(dedup(navs)).getOrElse(NullExpr())
    })
    q.copy(where = q.where.map(rw), returnExpr = q.returnExpr.map(rw),
      relationships = q.relationships.map(r => r.copy(suchThat = rw(r.suchThat))),
      lets = q.lets.map(l => l.copy(expr = rw(l.expr))),
      sort = q.sort.map(si => si.copy(expr = si.expr.map(rw))))
  }

  /** The het-Choice member types of an alias bound to a heterogeneous-resource value — an INLINE union or
    * a relation define whose element type is `List<Choice<resources>>` (the discriminated struct a
    * navigation dispatches over, [[rewriteHetUnionNav]]). `None` for a homogeneous / non-Choice source. */
  private[lower] def hetAliasMembers(e: Expr, typeOf: Expr => CqlType): Option[Seq[CqlType]] =
    typeOf(e) match {
      // Canonicalize with the SAME `flatResourceMembers` + `dedupByTag` the CONSTRUCTION decision uses
      // (`isHetResourceUnion`/`unionMembers`), so navigation agrees with construction at EVERY scope
      // (unified het-detection, EMIT-SQL-FLAT-CHOICE). Crucially this flattens a NESTED het union — an
      // alias bound to `("…Ordered" typed List<Choice<SR,MR>>) union ("…Performed" typed List<Procedure>)`
      // has type `List<Choice<Choice<SR,MR>, Procedure>>`; the old `ListType(ChoiceType(ms))`-with-all-NamedType
      // guard bailed (the inner `Choice<SR,MR>` is not a NamedType), so navigation read NATIVELY while
      // construction flat-encoded — dropping the moved-into-carrier slots. Dedup still collapses a
      // DEGENERATE `Choice<Condition, Condition>` (two profiles of one resource) to ONE member → `None` →
      // native direct navigation (a union of two Condition profiles, unchanged).
      case lt: CqlType.ListType =>
        val deduped = SqlColumnLowering.dedupByTag(flatResourceMembers(lt))
        if (deduped.sizeIs > 1 && deduped.forall(_.isInstanceOf[CqlType.NamedType])) Some(deduped) else None
      case _ => None
    }

  /** Typed-slot dispatch for a `value[x]` choice navigated to a SUB-FIELD (`MR.medication.reference.value`)
    * — copied verbatim from `QueryLowering.rewriteChoiceSubfieldValueX` (pure IR->IR). */
  private def rewriteChoiceSubfieldValueX(q: Query, aliasTypes: Map[String, String]): Query = {
    def aliasOf(p: Property): Option[String] = p.scope.orElse(p.source match {
      case Some(AliasRef(a)) => Some(a)
      case _                 => None
    })
    def rw(e: Expr): Expr = Expr.coerce(e.transformDown {
      case p @ Property(src, scope, path) if path.contains('.') =>
        val segs = path.split("\\.").toList
        val head = segs.head
        val sub  = segs(1)
        val slot = for {
          a  <- aliasOf(p)
          rt <- aliasTypes.get(a)
          if FhirModel.isChoiceElement(head)
          s  <- FhirModel.choiceSlotForSubfield(rt, head, sub)
        } yield s
        slot.fold(p: Expr)(s => Property(src, scope, (s :: segs.tail).mkString(".")))
      case Property(Some(inner @ Property(isrc, iscope, base)), None, sub) if !base.contains('.') =>
        val slot = for {
          a  <- aliasOf(inner)
          rt <- aliasTypes.get(a)
          if FhirModel.isChoiceElement(base)
          s  <- FhirModel.choiceSlotForSubfield(rt, base, sub)
        } yield s
        slot.fold(Property(Some(inner), None, sub): Expr)(s => Property(Some(Property(isrc, iscope, s)), None, sub))
    })
    q.copy(where = q.where.map(rw), returnExpr = q.returnExpr.map(rw),
      relationships = q.relationships.map(r => r.copy(suchThat = rw(r.suchThat))),
      lets = q.lets.map(l => l.copy(expr = rw(l.expr))),
      sort = q.sort.map(si => si.copy(expr = si.expr.map(rw))))
  }
}
