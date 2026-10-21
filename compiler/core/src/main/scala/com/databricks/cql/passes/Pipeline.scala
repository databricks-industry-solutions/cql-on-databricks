package com.databricks.cql.passes

import com.databricks.cql.env.{Config, Context, Env}
import com.databricks.cql.ir.Library
import com.databricks.cql.load.ElmCodec
import com.databricks.cql.rules.{Batch, RuleExecutor, Strategy}
import com.databricks.cql.translate.CqlToElmCli
import com.databricks.cql.verify.{DefineGraph, Verifier}

/** The multi-pass compiler pipeline (ARCH-PIPELINE, `02` section 2):
  *
  * {{{
  * CQL -> [Translate] -> ELM JSON -> [Load] -> [Resolve] -> [Type/Encoding] -> [Normalize]
  *                                -> [Optimize] -> [Lower] -> [Schedule]
  * }}}
  *
  * The PUBLIC surface is **CQL** (`normalizeCql`, TECH-ELM-INPUT) — this is CQL on Databricks, not
  * ELM-on-Spark; the engine translates in-process via the bundled cqframework front end. ELM is
  * an internal IR-input seam (`normalize(elmJson)`), kept for differential test parity
  * (the same ELM feeds the engine and the ground-truth reference, CONF-GROUND-TRUTH), not as
  * the public contract.
  *
  * SCOPE. Load, Resolve (cycle detection), Type/Encoding, Normalize (function inlining +
  * the fixpoint rewrite batch), Lower, and Schedule are real through Phase 4. The Optimize
  * slot (retrieve pushdown, ARCH-PUSHDOWN) is wired here in Phase 5 via [[optimize]] and
  * gated by `config.optimizeRetrievePushdown`; its rule batch grows one rule at a time
  * (ARCH-SEAMS), each kept green against the optimized-vs-unoptimized self-diff
  * (CONF-GATE-SELFDIFF). The verifier scaffold runs at pass boundaries (CONF-VERIFIER).
  *
  * Verifiers run when `config.verifiersEnabled` (CONF-VERIFIER, test/debug builds). A
  * verifier failure points at the pass boundary that produced it.
  */
final class Pipeline(config: Config) {

  /** A log of which rule fired in which batch/iteration — the plan-diff traceability you
    * need when a measure's rate moves (ARCH-IR). Deterministic, append-only. */
  private val firedRules = scala.collection.mutable.ArrayBuffer.empty[String]
  def firedRuleLog: Seq[String] = firedRules.toSeq

  /** PUBLIC API SURFACE (TECH-ELM-INPUT): compile CQL text. The engine translates CQL→ELM
    * IN-PROCESS (no service, no subprocess) via the bundled cqframework front end, then runs
    * the ELM through the pipeline. Translation errors fail loudly (CORE-NO-SILENT-MISLOWER) —
    * no ELM is fabricated from invalid CQL. */
  def normalizeCql(cqlText: String): Library = {
    val translated = CqlToElmCli.translateText(cqlText)
    translated.elmJson match {
      case Some(elm) => normalize(elm)
      case None =>
        throw new IllegalArgumentException(
          s"CQL translation produced errors (no ELM emitted): ${translated.errors.mkString("; ")}")
    }
  }

  /** Optimize pass (ARCH-PIPELINE, ARCH-PUSHDOWN): the retrieve-pushdown rule batch, gated by
    * `config.optimizeRetrievePushdown` (default on; off = execute as written, CONF-GATE-SELFDIFF).
    * Runs between Normalize and Lower on the kernel IR; fired rules append to the same plan-diff
    * trace as Normalize. The `config` defaults to the pipeline's frozen build config, but the
    * differential/self-diff harnesses pass `env.config` so Optimize and Lower agree on the switch. */
  def optimize(lib: Library, config: Config = config): Library =
    Optimize.run(lib, config, (batch, rule, iter) => firedRules += s"$batch/$rule@$iter")

  /** Load only (ELM JSON -> IR). Total: unmodeled nodes become explicit Unknown nodes.
    * The internal ELM seam beneath the CQL surface (see `normalizeCql`). */
  def load(elmJson: String): Library = ElmCodec.loadString(elmJson)

  /** Run the Phase 0 pipeline as far as it is built: Load -> Resolve -> Normalize, with
    * verifier checkpoints. Returns the normalized IR. Does NOT lower (Lower is Phase 1). */
  def normalize(elmJson: String): Library = normalize(load(elmJson))

  /** Run the Phase 0 pipeline from an already-built IR library — the seam used after
    * [[com.databricks.cql.passes.LibraryLinker]] flattens a multi-library measure into one
    * self-contained library (CONF-GATE-MEASURE). Same verifier checkpoints as the ELM-string entry. */
  def normalize(loaded: Library): Library = {
    if (config.verifiersEnabled) Verifier.runAll(loaded, Seq(Verifier.WellFormedLibrary))

    val resolved = resolve(loaded)
    val normalized = runNormalize(resolved)

    // Type/Encoding is a recomputable attribute (TypeEncoding.typeOf), so its checkpoint runs
    // here on the kernel Lower consumes; it moves ahead of Normalize with type-directed
    // desugaring (Phase 4). OnlyKernelNodes runs first so unmodeled nodes trip that gate.
    if (config.verifiersEnabled)
      Verifier.runAll(normalized, Seq(Verifier.OnlyKernelNodes, Verifier.WellTyped))
    normalized
  }

  /** Resolve pass (Phase 0 portion): reject reference cycles among defines/functions —
    * CQL forbids both (LANG-FUNCTION, ARCH-PIPELINE). Linking includes/params/valuesets to
    * broadcast handles is wired in later phases. */
  def resolve(lib: Library): Library = {
    DefineGraph.assertAcyclic(lib)
    lib
  }

  /** Normalize pass: desugar ELM into the minimal kernel. Phase 1 inlines user-defined
    * functions (LANG-FUNCTION, `FunctionInliner`); query-clause and conditional desugaring
    * arrive in Phase 4. After inlining, the (currently empty) rewrite batch runs through the
    * real `RuleExecutor` so the fixpoint machinery stays exercised end to end. */
  def runNormalize(lib: Library): Library = {
    // Inline functions first so parameters referenced inside inlined bodies are then bound.
    val inlined = FunctionInliner.normalize(lib)
    val bound = ParameterResolver.normalize(inlined)
    // Resolve code/concept promotions to literal ConceptValues (LANG-TERMINOLOGY) after inlining, so
    // codes inside inlined fluent-function bodies (a status check, an onset interval) are resolved too.
    val concepts = ConceptResolver.normalize(bound)
    // Eliminate CORRELATED scalar-source let-queries (LANG-QUERY): `(e) a return body` over a singular
    // source that references an enclosing alias is `body[a := e]`. Runs after inlining so a fluent
    // function's let-query (an onset-interval helper's `(end of …abatementSpan()) abatementDate return …`)
    // is substituted with the caller's alias first, then collapsed — removing the nested correlated
    // query that would otherwise escape its enclosing alias. Non-correlated queries are left untouched.
    val letInlined = LetQueryInliner.normalize(concepts)
    // Resolve bare sort-key identifiers (ELM IdentifierRef) to scope-rooted Property against the
    // enclosing query's source alias (LANG-QUERY-SORT) — before the kernel verifier, so no residual
    // IdentifierRef reaches Lower.
    val identifiersResolved = IdentifierRefResolver.normalize(letInlined)
    val executor = new RuleExecutor(
      batches = Seq(Batch("Normalize", Strategy.FixedPoint(maxIterations = 50) /* no rewrite rules yet */)),
      onRuleFired = (batch, rule, iter) => firedRules += s"$batch/$rule@$iter")
    executor.execute(identifiersResolved).asInstanceOf[Library]
  }

  /** The root build Environment with the frozen config (ARCH-ENV, TECH-CONFIG). Context is
    * derived from the library's first context def when present (defaults to Patient). */
  def rootEnv(lib: Library): Env = {
    val ctxName = lib.contexts.headOption.map(_.name).getOrElse("Patient")
    Env.root(Context.named(ctxName), config)
  }
}

object Pipeline {
  def apply(config: Config = Config.defaults): Pipeline = new Pipeline(config)
}
