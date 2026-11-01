package com.databricks.cql.meta

import com.databricks.cql.env.{Config, Context}
import com.databricks.cql.ir.{CqlType, Expr, ExpressionDef, ExpressionRef, Library}
import com.databricks.cql.passes.{BuildInfo, Optimize, Pipeline, RetrieveCatalog, RetrieveDemand, TypeEncoding}
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.transpiler.lower.{RetrieveCall, SqlEvalEnv, SqlFrameLowering}

/** A NEUTRAL, pipeline-agnostic description of a CQL library's compiled graph (PHASE-10). This repo does
  * not generate a pipeline — it DESCRIBES one: an external pipeline (e.g. the parameterized SDP in the
  * accelerator) reads this to build and drive its own dataset graph, decide parallelism, and reuse shared
  * libraries across measures. Deliberately carries NO pipeline concepts (no `@dp`, no SDP, no provider).
  *
  * @param defines             the executable define names, in dependency order (the graph nodes).
  * @param dependencies        `define -> the defines it references` (edges among `defines`), so a consumer
  *                            builds the DAG / parallelizes / dedups shared sub-graphs.
  * @param boundaries          the materialization boundary set (`EXEC-MATERIALIZE-FILTER`: populations +
  *                            every define that filters) — the RAW seam keys (`ctx_`/`rel_`/`def_`/`cse_`).
  *                            This is exactly `Config.materializeBoundaries` a deployed
  *                            `cql(define, boundaries=…)` call re-lowers against.
  * @param materializedDefines convenience: the DEFINES to persist (those whose `rel_`/`def_` seam key is in
  *                            `boundaries`) — a consumer's "make this its own dataset" hint. Derivable from
  *                            `boundaries`; provided for ergonomics.
  * @param compilerVersion     the compiler version that produced this (`BUILD-VERSION-PIN` / determinism).
  * @param terminologyVersion  the pinned terminology version, when the library uses value sets. */
final case class LibraryGraph(
    defines: Seq[String],
    dependencies: Map[String, Seq[String]],
    boundaries: Set[String],
    materializedDefines: Set[String],
    compilerVersion: String,
    terminologyVersion: Option[String] = None,
    /** EMIT-DESCRIBE-ENCODING: the het-Choice carrier ENCODING the library was described/lowered against —
      * always `"struct"` (the flat native carrier, EMIT-SQL-FLAT-CHOICE, is the sole encoding since the
      * VARIANT alternative was retired, EMIT-SQL-VARIANT-CHOICE RETIRED). Kept in the deploy contract (the
      * Python client reads it) so a future encoding change stays a described, validated boundary. */
    encoding: String = "struct",
    /** EMIT-DESCRIBE-ENCODING / EMIT-SQL-RETRIEVE-DEMAND-BOUND: per FHIR resource TYPE, the top-level
      * physical fields the measure DEMANDS (a `value[x]` base expanded to its typed slots) — the minimum a
      * customer binding must supply (`BIND-CAPABILITY-OVERLAP`); `["*"]` for a whole-body / het-Choice use
      * (`Full`). Lets a downstream pipeline validate its typed-subset coverage before running. */
    retrieveDemand: Map[String, Seq[String]] = Map.empty,
    /** EMIT-SQL-VALUE-COLUMN: each define's ROW shape — `"scalar"` (one row per subject,
      * `(__subject_id, __val)`) or `"relation"` (grain-first, one row per list element,
      * `(__subject_id, __ordinal, __val)`). Every define carries its payload in the SAME [[valueColumn]],
      * so this is the ONLY thing that differs between two results and the one bit a reader needs.
      *
      * Published here — not just on a compile result — so `describe` is SELF-SUFFICIENT for a consumer that
      * builds its own dataset graph: it can plan how to READ each define without a second compile call and
      * without inspecting a produced schema (`ARCH-NO-RUNTIME-SCHEMA`). Keyed by RAW define name. */
    defineKinds: Map[String, String] = Map.empty,
    /** EMIT-SQL-VALUE-COLUMN: the payload column EVERY define result carries, whatever its
      * [[defineKinds]] entry says. Published rather than assumed so the convention lives in the contract. */
    valueColumn: String = SystemColumns.Val,
    /** EMIT-DESCRIBE-RETRIEVE-TYPES: every FHIR resource type the library retrieves, sorted. Unlike
      * [[retrieveDemand]], a retrieve that navigates no field (`exists [Encounter]`) is listed too, so a
      * caller knows exactly which data a run needs, and that a library listing none needs no data. */
    retrieveTypes: Seq[String] = Seq.empty,
    /** BIND-PROVIDER-REQUEST: each retrieve the compiled SQL makes, with its id and request, in first-seen order.
      * The client asks its data provider for each and binds the relations by id. Read off the same lowering as
      * everything else here, so the ids are the ones the compile looks up. */
    retrieves: Seq[RetrieveCall] = Seq.empty,
    /** BIND-PROVIDER-REQUEST: every value set the linked library declares, `(id, version)`, distinct by id and
      * sorted, so the client asks its terminology source for exactly these. */
    valueSets: Seq[(String, Option[String])] = Seq.empty)

/** Compute the neutral [[LibraryGraph]] for a library (the "describe" half of the compiler's public
  * surface, beside "compile"). It runs ONE generation-mode lowering of the SQL back end
  * ([[SqlFrameLowering]]) and reads back the defines the SQL materialization classifier stages as
  * barriers, plus the define graph off the IR. SPARK-FREE: the SQL lowering is pure text, so describing a
  * library needs no `SparkSession`, no data adapter, and no reachable server — the compiler DESCRIBES
  * without running (a strict strengthening of the former session-light path).
  *
  * `describe` reading the SAME classifier the runtime lowers with (`SqlFrameLowering`) is what keeps the
  * reported boundary set EQUAL to what the deployed pipeline actually materializes — the two are one
  * function, so they cannot drift (the proto cost model and the SQL classifier previously could). */
object LibraryDescribe {

  /** Describe `elm` under the injected environment. `hasTerminology` reports whether value-set expansion
    * views are mounted (the SQL analog of an injected `TerminologyProvider`); `overrides`/`grainFirst`
    * must match the deploy config so the boundary set the runtime re-lowers against is the one here. */
  def describe(elm: String,
               context: Context = Context.Patient,
               hasTerminology: Boolean = false,
               overrides: Map[String, String] = Map.empty,
               grainFirst: Boolean = false,
               terminologyVersion: Option[String] = None): LibraryGraph =
    describe(Pipeline().load(elm), context, hasTerminology, overrides, grainFirst, terminologyVersion)

  /** Describe an already-LOADED library — the seam used after [[com.databricks.cql.passes.LibraryLinker]]
    * flattens a multi-library measure into one self-contained library (`BUILD-COMPILE-LINK`), so the service
    * describes the SAME linked/pruned IR it compiles. `Pipeline().normalize` (idempotent on a normalized lib)
    * runs the front end; the rest is identical to the ELM-string entry. */
  def describe(lib0: Library,
               context: Context,
               hasTerminology: Boolean,
               overrides: Map[String, String],
               grainFirst: Boolean,
               terminologyVersion: Option[String]): LibraryGraph = {
    // GENERATION mode (boundaries = None): the SQL classifier chooses the set; grainFirst mirrors deploy.
    val config = Config.resolve(overrides).copy(materializeBoundaries = None, grainFirst = grainFirst)
    val lib = Pipeline().normalize(lib0)
    val optimized = Optimize.run(lib, config)
    // BIND-PROVIDER-REQUEST: describe runs before the client binds terminology, so a membership test lowers
    // here whatever the host has; nothing describe returns depends on the value-set relation.
    val env = SqlEvalEnv(context, config, terminology = true)
    val lowered = SqlFrameLowering.lower(optimized, env)

    val nodes = lowered.names
    val nodeSet = nodes.toSet
    // Edges among the executable defines only (a reference to the context resource is not a node).
    val deps = lib.statements.collect { case e: ExpressionDef if nodeSet.contains(e.name) => e }
      .map(d => d.name -> d.expression.collect { case ExpressionRef(n, _) if nodeSet.contains(n) => n }.distinct)
      .toMap
    // The RAW-name boundary contract (`rel_`/`def_` seam keys over Config.materializeBoundaries): a
    // grain-first Relation define keys `rel_<name>`, a subject-grained scalar define `def_<name>`.
    val materialized = lowered.materializedDefines
    val boundaries = materialized.map(d => if (lowered.relationDefines.contains(d)) s"rel_$d" else s"def_$d")

    // EMIT-DESCRIBE-ENCODING: publish the encoding + the per-resource demand contract the runtime/customer
    // validates against (the SAME RetrieveDemand the lowering wired).
    val encoding = "struct" // EMIT-SQL-FLAT-CHOICE is the sole het-Choice encoding (VARIANT retired)
    val typeOf: Expr => CqlType = { val ctx = TypeEncoding.ctxOf(optimized); e => TypeEncoding.typeOf(e, ctx) }
    val demand = RetrieveDemand.analyze(optimized, typeOf).map {
      case (rt, RetrieveDemand.Full)          => rt -> Seq("*")
      case (rt, RetrieveDemand.Narrow(fields)) => rt -> fields.toSeq.sorted
    }
    // BIND-DESCRIBE-RETRIEVES: the SCAN contract (`passes.RetrieveCatalog`) is computed off this same
    // optimized IR but is deliberately NOT published here yet — see the requirement note. Nothing
    // consumes it, and its shape depends on a consumer design that is still open; publishing a guess
    // would freeze the wrong contract.
    // EMIT-SQL-VALUE-COLUMN: the per-define ROW shape, read off the SAME lowering that chose the boundaries
    // (so describe and the deployed pipeline cannot disagree about how a result is shaped).
    val kinds = nodes.map(n => n -> (if (lowered.relationDefines.contains(n)) "relation" else "scalar")).toMap
    // EMIT-DESCRIBE-RETRIEVE-TYPES: the scanned types alone, off the same optimized IR. The full scan
    // contract (BIND-DESCRIBE-RETRIEVES) stays unpublished; only the types have a consumer.
    val retrieveTypes = RetrieveCatalog.analyze(optimized).retrieves.map(_.resourceType).distinct.sorted
    LibraryGraph(nodes, deps, boundaries, materialized, BuildInfo.version, terminologyVersion, encoding, demand,
      defineKinds = kinds, retrieveTypes = retrieveTypes, retrieves = env.retrieveCalls.values.toSeq,
      valueSets = optimized.valueSets.groupBy(_.id).map { case (id, defs) => id -> defs.flatMap(_.version).headOption }
        .toSeq.sortBy(_._1))
  }
}
