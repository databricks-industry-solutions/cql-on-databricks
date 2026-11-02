package com.databricks.cql.passes

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._

/** Flatten a multi-library CQL measure into ONE self-contained [[Library]] the existing
  * single-library pipeline (resolve → inline → lower) consumes unchanged (CONF-GATE-MEASURE,
  * ARCH-PIPELINE). The engine was built single-library; cross-library linking was deferred
  * (`Pipeline.resolve`) — this is that link step.
  *
  * A multi-library measure `include`s its dependency libraries (shared helpers) and
  * calls their defines/functions qualified by a local alias (`Helpers.isFinished`). The
  * linker merges every dependency's statements/value-sets/etc. into the measure with names
  * **qualified by the dependency's canonical library id** (so they are globally unique regardless
  * of each library's own local aliases); the measure's OWN names stay bare so the differential and
  * the reference match by the authored define names. Every cross-library reference is rewritten to
  * a LOCAL reference to the qualified name.
  *
  * `FHIRHelpers` is the one exception: its refs stay `Some("FHIRHelpers")` because the engine
  * lowers the FHIRHelpers primitive conversions directly (and [[FunctionInliner]] deliberately does
  * not inline them). It is therefore NOT merged.
  *
  * Overloads: dependency functions reachable from a measure's populations are single-arity in the
  * QI-Core libraries; merging keys by qualified name like the rest of the engine. A measure that
  * reached a genuinely overloaded dependency function would surface as an arity mismatch in
  * [[FunctionInliner]] (loud, never a silent mislowering, CORE-NO-SILENT-MISLOWER).
  */
object LibraryLinker {

  /** The FHIR conversion library kept external (lowered directly, not merged/inlined). */
  val FhirHelpers = "FHIRHelpers"

  /** Link `root` (the measure) with its dependency libraries, keyed by library id (the value of
    * an [[IncludeDef]] `path`). `deps` must contain the full transitive non-FHIRHelpers closure. */
  def link(root: Library, deps: Map[String, Library]): Library = {
    val mergedIds = closure(root, deps) // dependency library ids to merge, in discovery order
    val mergedLibs = mergedIds.map(id => deps.getOrElse(id,
      throw UnsupportedNode("IncludeDef", s"missing dependency library '$id' for linking ${root.id}")))

    // Context defines (the patient-context expression, named after the `context`) are GLOBAL: every
    // library declares its own `context Patient` + `define Patient`, but they all denote the one
    // runtime context. So context names are never qualified, and dependency context defines are not
    // merged (the root's serves) — otherwise a dep's `Patient` becomes an un-lowerable second define.
    val contextNames = (root.contexts ++ mergedLibs.flatMap(_.contexts)).map(_.name).toSet
    // Measure parameters are GLOBAL by name (like contexts): a measure binds one `Measurement Period`
    // and every library's same-named parameter denotes it. A dependency declares its own
    // `parameter "Measurement Period"` (often with NO default), so a qualified `Helpers.Measurement
    // Period` would be unbound — share the ROOT's parameter by name instead (drop the dep declaration,
    // rewrite the dep's ref to the bare root parameter).
    val sharedParamNames = root.parameters.map(_.name).toSet
    val rewrittenRoot = rewriteLibrary(root, isRoot = true, contextNames, sharedParamNames)
    val rewrittenDeps = mergedLibs.map(d => rewriteLibrary(d, isRoot = false, contextNames, sharedParamNames))

    Library(
      id = root.id,
      version = root.version,
      usings = root.usings,
      // Keep only the FHIRHelpers include; the merged dependencies are now inlined as local defs.
      includes = root.includes.filter(i => canonicalId(i.path) == FhirHelpers),
      parameters  = rewrittenRoot.parameters  ++ rewrittenDeps.flatMap(_.parameters),
      codeSystems = rewrittenRoot.codeSystems ++ rewrittenDeps.flatMap(_.codeSystems),
      valueSets   = rewrittenRoot.valueSets   ++ rewrittenDeps.flatMap(_.valueSets),
      codes       = rewrittenRoot.codes       ++ rewrittenDeps.flatMap(_.codes),
      concepts    = rewrittenRoot.concepts    ++ rewrittenDeps.flatMap(_.concepts),
      contexts = root.contexts,
      statements = rewrittenRoot.statements ++ rewrittenDeps.flatMap(_.statements),
      // EMIT-ROOT-INDEPENDENT: every merged name remembers the library that declares it.
      owners = rewrittenDeps.flatMap(_.owners).toMap)
  }

  /** EMIT-ROOT-INDEPENDENT — name a linked library's OWN defines and functions by their library, as [[link]]
    * names an included library's (`<library id>.<name>`), and record each in `owners`. After this, a define's
    * name, and so every object and alias the SQL emits for it, is the same whichever library is being
    * compiled: a measure's `Numerator` is `<MeasureLibrary>.Numerator` alone and when another
    * library includes it. Context defines stay bare (one runtime context). Parameters, value sets and
    * codes keep their names: the SQL emits their values, not their names.
    *
    * The pipeline compile applies it (`CompileService.compilePipeline`); describe and the session compile keep
    * the authored names they publish. */
  def ownerQualified(lib: Library): Library = {
    val contextNames = lib.contexts.map(_.name).toSet
    val own = lib.statements.collect {
      case e: ExpressionDef if !contextNames.contains(e.name) && !lib.owners.contains(e.name) => e.name
      case f: FunctionDef if !lib.owners.contains(f.name)                                      => f.name
    }.toSet
    def qualified(name: String): String = if (own.contains(name)) s"${lib.id}.$name" else name
    def rw(e: Expr): Expr = e match {
      case ExpressionRef(n, None)     => ExpressionRef(qualified(n), None)
      case FunctionRef(n, None, args) => FunctionRef(qualified(n), None, args.map(rw))
      case other =>
        other.withNewChildren(other.children.map {
          case c: Expr => rw(c)
          case c       => c
        }).asInstanceOf[Expr]
    }
    val version = lib.version.getOrElse("")
    lib.copy(
      parameters = lib.parameters.map(p => p.copy(default = p.default.map(rw))),
      statements = lib.statements.map {
        case e: ExpressionDef => e.copy(name = qualified(e.name), expression = rw(e.expression))
        case f: FunctionDef   => f.copy(name = qualified(f.name), body = rw(f.body))
        case other            => other
      },
      owners = lib.owners ++ own.map(n => qualified(n) -> StatementOwner(lib.id, version, n)))
  }

  /** Prune a linked library to the transitive closure of `roots` (the population define names),
    * dropping every define/function/value-set/etc. not reachable from them. This MUST run before
    * the kernel-node verifier so unmodeled nodes in unused defines (e.g. an exclusion library not
    * yet in scope) never trip the gate — only the slice under test is verified and lowered.
    * Follows `ExpressionRef` and local (non-FHIRHelpers) `FunctionRef` edges; context defines are
    * always kept (they may be referenced and are skipped by lowering). */
  def prune(lib: Library, roots: Set[String]): Library = {
    val exprDefs = lib.statements.collect { case e: ExpressionDef => e.name -> e }.toMap
    val funcDefs = lib.statements.collect { case f: FunctionDef => f.name -> f }.toMap
    val contextNames = lib.contexts.map(_.name).toSet

    val keepExpr = scala.collection.mutable.Set.empty[String]
    val keepFunc = scala.collection.mutable.Set.empty[String]
    val keepValueSet = scala.collection.mutable.Set.empty[String]
    val keepCodeSystem = scala.collection.mutable.Set.empty[String]
    val keepCode = scala.collection.mutable.Set.empty[String]
    val keepConcept = scala.collection.mutable.Set.empty[String]
    val keepParam = scala.collection.mutable.Set.empty[String]

    def visitExpr(name: String): Unit =
      if (!keepExpr(name)) {
        keepExpr += name
        exprDefs.get(name).foreach(d => walk(d.expression))
        // a context define ("Patient") referenced as an ExpressionRef has no def entry; fine.
      }
    def visitFunc(name: String): Unit =
      if (!keepFunc(name)) {
        keepFunc += name
        funcDefs.get(name).foreach(f => walk(f.body))
      }
    def walk(e: Expr): Unit = e match {
      case ExpressionRef(n, _)                     => visitExpr(n)
      case FunctionRef(_, Some(FhirHelpers), args) => args.foreach(walk) // external, no def to keep
      case FunctionRef(n, _, args)                 => visitFunc(n); args.foreach(walk)
      case ValueSetRef(n, _)                       => keepValueSet += n
      case CodeSystemRef(n, _)                     => keepCodeSystem += n
      case CodeRef(n, _)                           => keepCode += n
      case ConceptRef(n, _)                        => keepConcept += n
      case ParameterRef(n, _)                      => keepParam += n
      case other => other.children.foreach { case c: Expr => walk(c); case _ => () }
    }
    roots.foreach(visitExpr)

    // Concepts pull in their codes; codes pull in their code systems (name-level dependencies).
    lib.concepts.filter(c => keepConcept(c.name)).foreach(_.codeNames.foreach(keepCode += _))
    lib.codes.filter(c => keepCode(c.name)).foreach(_.codeSystem.foreach(keepCodeSystem += _))

    lib.copy(
      parameters  = lib.parameters.filter(p => keepParam(p.name)),
      codeSystems = lib.codeSystems.filter(c => keepCodeSystem(c.name)),
      valueSets   = lib.valueSets.filter(v => keepValueSet(v.name)),
      codes       = lib.codes.filter(c => keepCode(c.name)),
      concepts    = lib.concepts.filter(c => keepConcept(c.name)),
      statements  = lib.statements.filter {
        case e: ExpressionDef => keepExpr(e.name) || contextNames.contains(e.name)
        case f: FunctionDef   => keepFunc(f.name)
        case _                => true
      })
  }

  // ---- internals -----------------------------------------------------------

  /** A library's canonical id from an include `path` (the path IS the library id here). */
  private def canonicalId(path: String): String = path

  /** The dependency library ids to merge: a BFS over `root`'s includes (and their includes),
    * excluding FHIRHelpers (kept external) and the root itself. Distinct, in discovery order. */
  private def closure(root: Library, deps: Map[String, Library]): Seq[String] = {
    val out = scala.collection.mutable.LinkedHashSet.empty[String]
    val queue = scala.collection.mutable.Queue.empty[Library]
    queue.enqueue(root)
    while (queue.nonEmpty) {
      val lib = queue.dequeue()
      lib.includes.map(i => canonicalId(i.path)).foreach { id =>
        if (id != FhirHelpers && id != root.id && !out.contains(id)) {
          out += id
          deps.get(id).foreach(queue.enqueue(_))
        }
      }
    }
    out.toSeq
  }

  /** Rewrite every definition and reference in `lib` for merging: dependency names are qualified
    * by `lib.id`; the root's own names stay bare. Cross-library references resolve through `lib`'s
    * own include aliases to the target library id (FHIRHelpers stays external). */
  private def rewriteLibrary(lib: Library, isRoot: Boolean, contextNames: Set[String],
                             sharedParamNames: Set[String]): Library = {
    val aliasToId: Map[String, String] =
      lib.includes.map(i => i.localIdentifier -> canonicalId(i.path)).toMap

    // Context names stay bare (shared across libraries); everything else in a dependency is qualified.
    def localName(name: String): String =
      if (isRoot || contextNames.contains(name)) name else s"${lib.id}.$name"

    // Resolve a reference (possibly aliased) to its merged (libraryName, name) form.
    def resolveRef(refLib: Option[String], name: String): (Option[String], String) = refLib match {
      case Some(alias) =>
        val targetId = aliasToId.getOrElse(alias, alias)
        if (targetId == FhirHelpers) (Some(FhirHelpers), name)
        else (None, s"$targetId.$name")
      case None =>
        (None, localName(name))
    }

    def rw(e: Expr): Expr = e match {
      case ExpressionRef(n, l) => val (rl, rn) = resolveRef(l, n); ExpressionRef(rn, rl)
      // A dependency's unqualified ref to a measure-global parameter (`Measurement Period`) binds to
      // the shared ROOT parameter (bare), not a qualified dep parameter (which has no default).
      case ParameterRef(n, None) if !isRoot && sharedParamNames.contains(n) => ParameterRef(n, None)
      case ParameterRef(n, l)  => val (rl, rn) = resolveRef(l, n); ParameterRef(rn, rl)
      case ValueSetRef(n, l)   => val (rl, rn) = resolveRef(l, n); ValueSetRef(rn, rl)
      case CodeSystemRef(n, l) => val (rl, rn) = resolveRef(l, n); CodeSystemRef(rn, rl)
      case CodeRef(n, l)       => val (rl, rn) = resolveRef(l, n); CodeRef(rn, rl)
      case ConceptRef(n, l)    => val (rl, rn) = resolveRef(l, n); ConceptRef(rn, rl)
      case FunctionRef(n, l, args) =>
        val (rl, rn) = resolveRef(l, n); FunctionRef(rn, rl, args.map(rw))
      case other =>
        other.withNewChildren(other.children.map {
          case c: Expr => rw(c)
          case c       => c
        }).asInstanceOf[Expr]
    }

    // EMIT-ROOT-INDEPENDENT: a dependency's merged names, each with the library that declares it.
    val version = lib.version.getOrElse("")
    val owned: Map[String, StatementOwner] = if (isRoot) Map.empty else (
      lib.statements.collect {
        case e: ExpressionDef if !contextNames.contains(e.name) => e.name
        case f: FunctionDef                                     => f.name
      } ++ lib.parameters.map(_.name).filterNot(sharedParamNames.contains)
    ).map(n => localName(n) -> StatementOwner(lib.id, version, n)).toMap

    lib.copy(
      owners = owned,
      // Drop a dependency's declaration of a measure-global parameter (the root's shared one serves);
      // keep the root's. Otherwise unchanged.
      parameters  = lib.parameters
        .filterNot(p => !isRoot && sharedParamNames.contains(p.name))
        .map(p => p.copy(name = localName(p.name), default = p.default.map(rw))),
      codeSystems = lib.codeSystems.map(c => c.copy(name = localName(c.name))),
      valueSets   = lib.valueSets.map(v => v.copy(name = localName(v.name))),
      codes       = lib.codes.map(c => c.copy(name = localName(c.name), codeSystem = c.codeSystem.map(localName))),
      concepts    = lib.concepts.map(c => c.copy(name = localName(c.name), codeNames = c.codeNames.map(localName))),
      statements  = lib.statements.flatMap {
        // Drop a dependency's context define (e.g. its own `define Patient`) — the root's is shared.
        case e: ExpressionDef if !isRoot && contextNames.contains(e.name) => None
        case e: ExpressionDef => Some(e.copy(name = localName(e.name), expression = rw(e.expression)))
        case f: FunctionDef   => Some(f.copy(name = localName(f.name), body = rw(f.body)))
        case other            => Some(other)
      })
  }
}
