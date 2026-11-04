package com.databricks.cql.env

import com.databricks.cql.error.{ConfigError, RetiredConfigKey}

/** Immutable engine configuration (TECH-CONFIG, `technical-implementation.md` section 8).
  *
  * Resolved once, up front, and frozen into the `Env` (ARCH-ENV) — never read from
  * mutable global/session state. This is what lets the engine run correctly where
  * compute is ephemeral, shared, and multi-tenant (serverless-style), keeps plan
  * building pure (ARCH-PURITY), and makes a run reproducible from recorded inputs
  * (TECH-DETERMINISM): the resolved config is recorded with any materialized output.
  *
  * Precedence is explicit and immutable: caller-supplied values override built-in
  * defaults; once resolved, the config does not change for the duration of the build.
  */
final case class Config(
    /** Retrieve predicate pushdown (ARCH-PUSHDOWN). `false` = execute exactly as
      * written — the optimizer-faithfulness ground truth. */
    optimizeRetrievePushdown: Boolean = true,
    /** The single cost-driven materialization policy (DATA-MATERIALIZE-POLICY, `data-format.md`
      * section 6.2): `none` (never), `all` (every candidate), or `costly` (cost × reuse over
      * [[materializeCostThreshold]]). Default `costly`. */
    materializeMode: Config.MaterializeMode = Config.MaterializeMode.Costly,
    /** The `X` a candidate's `Cost.score(frame) × reuse` must EXCEED to be materialized under
      * `costly` (DATA-MATERIALIZE-POLICY). In proto-byte units (see `cost.Cost.score`). */
    materializeCostThreshold: Long = Config.DefaultMaterializeCostThreshold,
    /** Skip materializing bare unfiltered-retrieve pass-through defines
      * (DATA-SKIP-UNFILTERED) — honored in EVERY mode. */
    materializeSkipUnfilteredRetrieves: Boolean = true,
    /** `DATA-RETRIEVE-BARRIER-OPT` — the OPTIONAL retrieve-barrier policy: whether a FILTERED retrieve
      * read becomes its own named object instead of being inlined into every define that consumes it.
      * `None` (the DEFAULT) is today's behaviour byte-for-byte; `Costly` materializes a filtered read
      * reused at ≥[[materializeRetrieveMinReuse]] call sites; `All` materializes every filtered read.
      *
      * A BARE (unfiltered) retrieve is NEVER a barrier under ANY value — it is a flat copy of a
      * potentially large source table and the I/O does not pay (`DATA-SKIP-UNFILTERED`'s rationale,
      * enforced here rather than inherited: the flag of that name is a pure-IR define-level input the
      * SQL path does not read). Orthogonal to [[materializeMode]], which governs DEFINES. */
    materializeRetrieves: Config.RetrieveMode = Config.RetrieveMode.None,
    /** The `N` a filtered read's CALL-SITE count must reach to be materialized under
      * `materialize.retrieves = costly` (`DATA-RETRIEVE-BARRIER-OPT`). `2` mirrors the define-level
      * shared-cut-point threshold. Ignored under `none`/`all`. */
    materializeRetrieveMinReuse: Int = 2,
    /** `BUILD-COMPILE-SERVICE` — the COMPILER MODE selector.
      * `None` (default) is GENERATION mode: the materialization boundary set is CHOSEN here by the cost
      * model ([[materializeMode]] × `Cost.score`). `Some(keys)` is RUNTIME mode: lower against a FIXED,
      * pre-decided boundary set — [[com.databricks.cql.lower.MaterializationPolicy]] materializes
      * exactly the seam keys in this set and NEVER computes `Cost.score` (cost-free; the ~7s/update of
      * per-candidate scoring is skipped). The keys are the materialize-seam keys (`ctx_<context>`,
      * `rel_<define>`, `def_<define>`, `cse_<hash>`) a generation-mode lowering recorded (the runtime
      * materializer's `barrierKeys`). Separation of concerns by TIME: the pipeline generator scores ONCE
      * and bakes the set in; each pipeline UPDATE re-lowers against it, cost-free. `skip_unfiltered`
      * still wins in both modes. */
    materializeBoundaries: Option[Set[String]] = None,
    /** Which TerminologyProvider adapter/source to bind (DATA-TERMINOLOGY-ADAPTER). */
    terminologyAdapter: Option[String] = None,
    /** EMIT-SQL-OBJECT-PREFIX: namespace for emitted SESSION object names — `obj`/`rel`/null-sidecar
      * names become `<prefix>__<name>`. EMPTY BY DEFAULT (bare names, the long-standing emitted form).
      *
      * Set it when ONE Spark session lowers MORE THAN ONE library: object names are sanitized DEFINE
      * names, so two measures both emit a view called `Numerator`, `CREATE OR REPLACE TEMP VIEW` rebinds
      * silently, and — because DataFrames are lazy — the first handle then reads the SECOND library's
      * rows. Off by default because turning it on changes emitted SQL for every existing caller. */
    objectPrefix: String = "",
    /** Run verifier checkpoints between passes (CONF-VERIFIER). On in test/debug. */
    verifiersEnabled: Boolean = true,
    /** DEBUG ONLY (DEBUG-PLAN-DUMP). When set, a lowered library's artifacts — source CQL,
      * ELM JSON, the normalized IR kernel tree (the closest-to-ELM logical plan), and the
      * Spark Connect unresolved logical plan — are written under this directory for inspection.
      * `None` (the default) disables the dump entirely, so production builds never touch the
      * filesystem or pay any cost (`com.databricks.cql.debug.StageDump`). */
    debugDumpDir: Option[String] = None,
    /** Grain-first lowering (ARCH-GRAIN). When `true`,
      * list-valued defines are carried as `Relation`s (rows at the natural resource grain) and the
      * `Array` boxing is deferred to a SINGLE coercion at the output boundary, instead of the
      * per-define collect_list→posexplode→re-collect round-trip that folds every define onto one
      * wide frame, which blows up the plan. DEFAULTS OFF so every existing fixture lowers
      * byte-unchanged; the grain-first fixtures turn it on and are checked against the plan-shape
      * goldens. */
    grainFirst: Boolean = false,
    /** ARCH-COND-UNION (`transformation-catalog.md` section 24, `engine-design.md` section 6). When
      * `true`, a LIST-valued `if`/`case` whose branches are all relation-valued is lowered to a UNION
      * of the branches with `cqlTrue(condition)` / its negation pushed into each branch as a
      * per-subject gate — every branch stays `Relation`-encoded, so NO `collect_list`/`posexplode`
      * boxing of the conditional enters the plan. DEFAULTS OFF: off = today's boxed `when/otherwise`
      * lowering (the ground truth); on = the union rewrite, proven result-preserving by the
      * optimized-vs-unoptimized self-diff, exactly as `optimize.retrieve_pushdown`
      * is. Requires `grainFirst` (the union is realized as a `Relation` define). Scalar conditionals
      * are unaffected (they stay columnar `when/otherwise`, `ColumnLowering`). */
    optimizeConditionalUnion: Boolean = false,
    /** ARCH-COND-UNION depth guard. A nested `if`/`case` expands to a UNION of 2ⁿ leaves (each leaf a
      * relation under a conjunction of (possibly negated) conditions); this bounds `n` so a pathological
      * nesting fails loudly rather than silently exploding the plan. Only consulted when
      * `optimizeConditionalUnion` is on. */
    conditionalUnionMaxDepth: Int = 8,
    /** ARCH-CTE-DEF (`engine-design.md`). When `true`, EVERY define is lowered as a named, referenceable
      * CTE-def relation (a Spark temp view referenced by name) rather than inlined into one threaded
      * frame — so the unresolved plan is a faithful define-once / reference-many DAG (`read named_table`
      * leaves), reads define-by-define, and materialization barriers (`localCheckpoint`) attach ONLY to
      * these CTE-def nodes. DEFAULT ON (validated diff-clean vs the Java reference, CONF-GATE-MEASURE,
      * across materialize modes). It activates ONLY where a [[Materializer]] is injected (a session to
      * register the views into); a server-free or materializer-less host falls back to inlining, so those
      * plans stay byte-unchanged. The naming layer is always-on under the flag; the cost policy
      * ([[materializeMode]]) only decides which CTE-defs are additionally `localCheckpoint`-materialized
      * (kept temp/session-scoped, never persisted). Set `-Dcqldatabricks.cteDefs=false` to force inlining. */
    cteDefs: Boolean = true
) {

  /** A stable, ordered key/value view of the resolved config, recorded with output for
    * reproducibility (TECH-DETERMINISM). Deterministic order — never hashmap order. */
  def recordedView: Seq[(String, String)] = Seq(
    "optimize.retrieve_pushdown"            -> optimizeRetrievePushdown.toString,
    "materialize.mode"                      -> materializeMode.key,
    "materialize.cost_threshold"            -> materializeCostThreshold.toString,
    "materialize.skip_unfiltered_retrieves" -> materializeSkipUnfilteredRetrieves.toString,
    "materialize.retrieves"                 -> materializeRetrieves.key,
    "materialize.retrieve_min_reuse"        -> materializeRetrieveMinReuse.toString,
    // Compiler mode: the fixed boundary set if RUNTIME mode (sorted for determinism), else
    // `<cost>` for GENERATION mode (the cost model chooses). Recorded so a runtime-compiled artifact
    // pins the exact boundaries it lowered against (CORE-DETERMINISM / BUILD-VERSION-PIN).
    "materialize.boundaries"                -> materializeBoundaries.map(_.toSeq.sorted.mkString(",")).getOrElse("<cost>"),
    "terminology.adapter"                   -> terminologyAdapter.getOrElse(""),
    "emit.object_prefix"                    -> objectPrefix,
    "verifiers.enabled"                     -> verifiersEnabled.toString,
    "debug.dump_dir"                        -> debugDumpDir.getOrElse(""),
    "lower.grain_first"                     -> grainFirst.toString,
    "optimize.conditional_union"            -> optimizeConditionalUnion.toString,
    "optimize.conditional_union_max_depth"  -> conditionalUnionMaxDepth.toString,
    "lower.cte_defs"                        -> cteDefs.toString
  )
}

object Config {
  /** The default `materialize.cost_threshold` — the `X` (in `cost.Cost.score` proto-byte units) a
    * candidate's `cost × reuse` must EXCEED under `costly`. Tuned so the default `costly` subsumes the
    * old reuse-only `selective` AND keeps the heavy cut-points large measures depend on (the `ctx_<context>`
    * struct build and the value[x] `cse_` barrier) materialized, while a small single-use frame stays a
    * view (DATA-MATERIALIZE-POLICY watch-items). */
  val DefaultMaterializeCostThreshold: Long = 8192L

  /** The single materialization policy (DATA-MATERIALIZE-POLICY). Keys `all`/`costly`/`none`;
    * `selective` is a DEPRECATED ALIAS of `costly` (the cost-driven policy generalizes the old
    * reuse-only selective). */
  sealed abstract class MaterializeMode(val key: String)
  object MaterializeMode {
    case object None    extends MaterializeMode("none")
    case object Costly  extends MaterializeMode("costly")
    case object All     extends MaterializeMode("all")

    def fromKey(k: String): Option[MaterializeMode] = k.toLowerCase match {
      case "none"      => Some(None)
      case "costly"    => Some(Costly)
      case "selective" => Some(Costly) // deprecated alias — the cost policy subsumes reuse-only selective
      case "all"       => Some(All)
      case _           => scala.None
    }
  }

  /** The OPTIONAL retrieve-barrier policy (`DATA-RETRIEVE-BARRIER-OPT`). Deliberately a SEPARATE type
    * from [[MaterializeMode]] even though the key names match: the two policies govern different objects
    * (defines vs retrieve reads), have different defaults (`costly` vs `none`), and `costly` means
    * different things — for a define it is the structural `SqlFrameLowering.classify` rule, for a read it
    * is call-site REUSE (a filtered read is the whole candidate set, so "filtered" cannot also be the
    * discriminator that separates `costly` from `all`). */
  sealed abstract class RetrieveMode(val key: String)
  object RetrieveMode {
    /** Today's behaviour: a retrieve is always inlined at its use site (`DATA-RETRIEVE-NO-BARRIER`). */
    case object None   extends RetrieveMode("none")
    /** A filtered read reused at ≥`materialize.retrieve_min_reuse` call sites becomes an object. */
    case object Costly extends RetrieveMode("costly")
    /** EVERY filtered read becomes an object — the diagnosis/audit setting. */
    case object All    extends RetrieveMode("all")

    def fromKey(k: String): Option[RetrieveMode] = k.toLowerCase match {
      case "none"   => Some(None)
      case "costly" => Some(Costly)
      case "all"    => Some(All)
      case _        => scala.None
    }
  }

  /** The built-in defaults (the documented defaults in TECH-CONFIG). */
  val defaults: Config = Config()

  /** Keys no lowering reads any more. A request that sends one is refused (SVC-PROTOCOL). */
  private val RetiredKeys = Seq("lower.project_fuse", "lower.retrieve_views")

  /** Resolve config from a caller-supplied namespaced key/value map over the defaults. Precedence: caller
    * overrides defaults (TECH-CONFIG). A known key whose value does not parse fails with [[ConfigError]],
    * naming the key and the value; the default is never taken in its place (CORE-NO-SILENT-MISLOWER). A
    * retired key fails with [[RetiredConfigKey]]. An unknown key is ignored (SVC-PROTOCOL). */
  def resolve(overrides: Map[String, String]): Config = {
    RetiredKeys.find(overrides.contains).foreach(key => throw RetiredConfigKey(key))

    def bool(key: String, current: Boolean): Boolean =
      parsed(key, "true or false")(v => v.toLowerCase match {
        case "true"  => Some(true)
        case "false" => Some(false)
        case _       => None
      }).getOrElse(current)

    /** The value of `key` read by `read`, or the failure that names it. Absent keys are `None`. */
    def parsed[A](key: String, expected: String)(read: String => Option[A]): Option[A] =
      overrides.get(key).map(v => read(v).getOrElse(throw ConfigError(key, v, expected)))

    val base = defaults
    base.copy(
      optimizeRetrievePushdown = bool("optimize.retrieve_pushdown", base.optimizeRetrievePushdown),
      materializeMode = parsed("materialize.mode", "none, costly or all")(MaterializeMode.fromKey)
        .getOrElse(base.materializeMode),
      materializeCostThreshold = parsed("materialize.cost_threshold", "an integer")(v => scala.util.Try(v.toLong).toOption)
        .getOrElse(base.materializeCostThreshold),
      materializeSkipUnfilteredRetrieves =
        bool("materialize.skip_unfiltered_retrieves", base.materializeSkipUnfilteredRetrieves),
      materializeRetrieves = parsed("materialize.retrieves", "none, costly or all")(RetrieveMode.fromKey)
        .getOrElse(base.materializeRetrieves),
      materializeRetrieveMinReuse = parsed("materialize.retrieve_min_reuse", "an integer of at least 1")(v =>
          scala.util.Try(v.toInt).toOption.filter(_ >= 1))
        .getOrElse(base.materializeRetrieveMinReuse),
      terminologyAdapter = overrides.get("terminology.adapter").filter(_.nonEmpty)
        .orElse(base.terminologyAdapter),
      // EMIT-SQL-OBJECT-PREFIX: sanitized so the prefix can never inject punctuation into an identifier.
      objectPrefix = overrides.get("emit.object_prefix").map(_.trim).filter(_.nonEmpty)
        .map(_.replaceAll("[^A-Za-z0-9]", "_")).getOrElse(base.objectPrefix),
      verifiersEnabled = bool("verifiers.enabled", base.verifiersEnabled),
      debugDumpDir = overrides.get("debug.dump_dir").filter(_.nonEmpty)
        .orElse(base.debugDumpDir),
      grainFirst = bool("lower.grain_first", base.grainFirst),
      optimizeConditionalUnion = bool("optimize.conditional_union", base.optimizeConditionalUnion),
      conditionalUnionMaxDepth = parsed("optimize.conditional_union_max_depth", "an integer")(v =>
          scala.util.Try(v.toInt).toOption).getOrElse(base.conditionalUnionMaxDepth),
      cteDefs = bool("lower.cte_defs", base.cteDefs)
    )
  }
}
