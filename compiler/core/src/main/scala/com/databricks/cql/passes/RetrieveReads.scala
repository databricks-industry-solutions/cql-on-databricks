package com.databricks.cql.passes

import com.databricks.cql.env.Config
import com.databricks.cql.ir._
import com.databricks.cql.sema.FhirModel

/** The OPTIONAL retrieve-barrier decision (`DATA-RETRIEVE-BARRIER-OPT`): which FILTERED data reads become
  * their own named object instead of being inlined into every define that consumes them.
  *
  * WHY THIS EXISTS. A retrieve has no name. It is inlined at each use site
  * (`SqlQueryLowering.filteredRetrieve`), so "the retrieve returned 100K rows over 20K patients" is not an
  * observable quantity anywhere in a compiled measure — there is no relation to count. Nor can a consumer
  * probe for it: the mount's eight arguments are ADVISORY (`BIND-CAPABILITY`), so a conformant UDTF may
  * ignore them and hand back a superset, and the predicates that make the answer correct are re-applied
  * ABOVE the call, inside whichever define consumed it. The quantity is the CQL retrieve EXPRESSION's
  * value — mount rows intersected with the value set — and only the compiler can see it.
  *
  * WHAT A READ IS (the identity question). Not an ELM element: `localId`/`locator` are dropped at load
  * (`ElmCodec`), and they would be the wrong key anyway — this object is deliberately 1:N, collapsing
  * every occurrence that reads the same rows into one table. Nor the raw [[Retrieve]] node: after
  * `LibraryLinker` flattens a measure, one value-set URL can carry several local aliases (a measure declares
  * `Inpatient Encounter` and so does its `Helpers` dependency), and two nodes differing only in the alias
  * they wrote read IDENTICAL rows. So the identity is the RESOLVED [[descriptor]] — canonical value-set
  * ids, not names — which is what makes a read shared across measures collapse to one object.
  *
  * WHAT IT DOES NOT COVER. A BARE (unfiltered) retrieve is never a candidate under any mode: it is a flat
  * copy of a potentially large source table and the I/O does not pay for the inspectability
  * (`DATA-SKIP-UNFILTERED`'s rationale). [[isFiltered]] is the single source of that rule — the SQL
  * lowering's `isUnfilteredScan` delegates to it, so the emitter and this pass cannot disagree about which
  * reads are candidates.
  *
  * Pure IR → metadata (no Spark, no session), computed from the SAME linked + pruned + OPTIMIZED library
  * the SQL back end lowers — so the date pushdown has already folded into `dateRange`/`periodScanFilter`
  * and the reads described here are the reads that actually run. */
object RetrieveReads {

  /** One materialization CANDIDATE: a distinct filtered read, every IR node that reads it, and the
    * coordinates a consumer needs to know what the resulting table holds.
    *
    * @param obj       the emitted object name ([[objName]]) — stable for the same coordinates.
    * @param nodes     the DISTINCT [[Retrieve]] nodes that resolve to this read. Several nodes map to one
    *                  read when they differ only in a local value-set alias; the lowering keys on these so
    *                  a substitution at the emit seam is an O(1) node lookup.
    * @param defines   the defines whose bodies own a call site, in library order (deduplicated).
    * @param callSites the number of call sites — the reuse figure `costly` thresholds. Counts
    *                  OCCURRENCES, not defines: a define reading the same read twice pays for it twice.
    * @param coords    what the read filters on ([[Coordinates]]). */
  final case class Read(obj: String,
                        nodes: Seq[Retrieve],
                        defines: Seq[String],
                        callSites: Int,
                        coords: Coordinates)

  /** What a materialized read holds, in the terms a consumer can act on — the answer to "which rows is
    * this table?" without re-parsing the CQL.
    *
    * The exact `dateRange` is deliberately NOT published as a window: after pushdown it is an arbitrary
    * expression, commonly derived from the measurement-period parameter, so only its PRESENCE and the
    * property it applies to are statically known. The coarse period window IS literal (the pushdown rule
    * builds it from ISO strings) and is published as such. Reporting a half-resolved window as if it were
    * the filter would be worse than reporting none (`CORE-NO-SILENT-MISLOWER`).
    *
    * @param resourceType the FHIR type read (`"Condition"`).
    * @param profile      the SPECIALISED profile canonical constrained to (`LANG-RETRIEVE-PROFILE`).
    * @param codeProperty the element the code filter tests (`"code"`, `"type"`, `"medication"`).
    * @param codeFilter   what that element is filtered against — a canonical value-set id, literal codes,
    *                     or `Other` when the bracket cannot be named statically (never dropped).
    * @param dateProperty the element a folded date filter applies to.
    * @param dateFiltered whether a date range folded onto the read.
    * @param periodWindow the COARSE literal period pre-filter, `(property, lowInclusive, highExclusive)`. */
  final case class Coordinates(resourceType: String,
                               profile: Option[String] = None,
                               codeProperty: Option[String] = None,
                               codeFilter: Option[RetrieveCatalog.CodeFilter] = None,
                               dateProperty: Option[String] = None,
                               dateFiltered: Boolean = false,
                               periodWindow: Option[(String, String, String)] = None)

  /** True when a retrieve carries a SCAN FILTER — a value set / code, a folded date range, a coarse period
    * window, or a profile constraint. The SINGLE source of the candidate rule: `SqlQueryLowering`'s
    * `isUnfilteredScan` is its negation, so the emitter cannot classify a read differently from this pass.
    *
    * A profile constraint counts: a profile-only retrieve has no codes or dates at all, and treating it as
    * bare would fuse it to the mount and drop the predicate (a profile over-match). */
  def isFiltered(r: Retrieve): Boolean =
    r.codes.isDefined || r.dateRange.isDefined || r.periodScanFilter.isDefined || r.profileConstraint.isDefined

  /** The canonical descriptor of a read — the string its IDENTITY and its [[objName]] hash derive from.
    *
    * Built from RESOLVED semantics (a value set contributes its canonical id, never the local alias the
    * author wrote) so that two measures binding the same value-set OID under different names share one object.
    *
    * The read's own fields are spelled out field-by-field for readability and name stability. The date
    * RANGE is the exception: it is an arbitrary sub-expression, so it contributes its TOTAL case-class
    * `toString` rather than a rendered form. `TreePrinter` would be prettier but is a readability
    * rendering with a lossy catch-all — two different windows could render alike, collapse onto one
    * object, and silently apply the wrong predicate to half their call sites. A faithful key that moves
    * when the IR changes is the right trade against a pretty key that can be wrong. */
  def descriptor(r: Retrieve, codeFilterOf: Expr => RetrieveCatalog.CodeFilter): String = {
    val parts = Seq(
      "type="    + FhirModel.localName(r.dataType),
      "profile=" + r.profileConstraint.getOrElse(""),
      "codeProp=" + r.codeProperty.getOrElse(""),
      "codes="   + r.codes.map(codeFilterOf).map {
        case RetrieveCatalog.ValueSet(id, _)  => s"vs:$id"
        case RetrieveCatalog.Codes(codes, _)  => codes.sorted.map { case (s, c) => s"$s|$c" }.mkString("codes:", ",", "")
        case RetrieveCatalog.Other(elmType)   => s"other:$elmType"
      }.getOrElse(""),
      "dateProp=" + r.dateProperty.getOrElse(""),
      // The range EXPRESSION identifies the filter structurally; two reads sharing an object must apply the
      // same predicate, so the TOTAL form is the safe key even though it is not published as a window.
      "dateRange=" + r.dateRange.map(_.toString).getOrElse(""),
      "dateConv=" + r.datePointConverter.getOrElse(""),
      "period="  + r.periodScanFilter.map { case (p, lo, hi) => s"$p|$lo|$hi" }.getOrElse(""))
    parts.mkString(";")
  }

  /** The emitted object name for a read: `read_<type>[_<filter>]_<hash8>` —
    * `read_condition_example_condition_a79cb213` — the hash being the first 8 hex chars of the
    * SHA-256 of [[descriptor]].
    *
    * The hash alone is the IDENTITY; the `<filter>` slug ([[filterSlug]]) is READABILITY, so that a table
    * list, a `SHOW TABLES`, or a Spark UI stage is legible without joining back to the published
    * [[Coordinates]]. Correlation is still by those coordinates — exactly as a define recipe is correlated
    * by `(library, version, define)` rather than by parsing `SqlNames.sanitize` — because the slug is
    * LOSSY: it is truncated, it names only the code filter (or the profile), and it says nothing about the
    * date range or period window that also separate two reads of one value set. Content addressing remains
    * the CORRECT identity for task keying: re-binding a value set yields a different read, which should be
    * a different table and a different task.
    *
    * The slug is derived from the value set's LOCAL CQL ALIAS, which is the only human-readable name a
    * value set has here (the resolved identity is an OID). Two libraries binding one OID under different
    * aliases therefore hash alike but NAME differently — a missed cross-measure dedup, never a wrong
    * answer. Stripping the linker's library qualifier makes the case that actually occurs agree
    * (`Helpers.Inpatient Encounter` and `Inpatient Encounter` both slug to `inpatient_encounter`), and
    * [[candidates]] picks the group's slug deterministically rather than by first occurrence.
    *
    * The `read_` prefix keeps this namespace disjoint from the `retrieve_<Type>` MOUNT the runtime
    * registers — the object is built FROM the mount and must never collide with it. */
  def objName(r: Retrieve, codeFilterOf: Expr => RetrieveCatalog.CodeFilter): String = {
    val digest = java.security.MessageDigest.getInstance("SHA-256")
      .digest(descriptor(r, codeFilterOf).getBytes("UTF-8"))
    val slug = filterSlug(r, codeFilterOf)
    val hash = digest.take(4).map("%02x".format(_)).mkString
    s"read_${FhirModel.localName(r.dataType).toLowerCase}${if (slug.isEmpty) "" else s"_$slug"}_$hash"
  }

  /** The readability slug in [[objName]]: WHAT the read filters on, in one identifier segment.
    *
    *   - a value set → its local alias (`"Example Condition"` → `example_condition`);
    *   - a SINGLE literal code → that code's declared name (a multi-code bracket is left unnamed rather
    *     than picking one of several arbitrarily, or concatenating them into an unreadable name);
    *   - no nameable code filter → the profile canonical's last segment (`LANG-RETRIEVE-PROFILE`), so a
    *     profile-only read reads as `read_observation_us_core_bmi_<hash>`;
    *   - nothing nameable (a date/period-only read, or an [[RetrieveCatalog.Other]] bracket) → `""`, and
    *     the name degrades to the bare `read_<type>_<hash8>`.
    *
    * Presentation only: never consulted by [[descriptor]], so no slug rule can merge or split two reads. */
  def filterSlug(r: Retrieve, codeFilterOf: Expr => RetrieveCatalog.CodeFilter): String = {
    val fromCodes = r.codes.map(codeFilterOf).flatMap {
      case RetrieveCatalog.ValueSet(_, name)             => Some(name)
      case RetrieveCatalog.Codes(_, names) if names.sizeIs == 1 => Some(names.head)
      case _                                             => scala.None
    }
    // A profile-only read has no code filter to name; its canonical's last segment is the author's term
    // for it (`…/StructureDefinition/us-core-bmi`).
    val source = fromCodes.orElse(r.profileConstraint.map(_.split('/').last))
    source.map(slugify).getOrElse("")
  }

  /** One CQL name → one lowercase identifier segment: drop the linker's `<Library>.` qualifier, collapse
    * every run of non-alphanumerics to a single `_`, lowercase, and cap the length.
    *
    * The qualifier is stripped only when the prefix looks like a library ALIAS (an identifier, no spaces),
    * so a value-set name that itself contains a dot keeps it. The cap cuts back to the last WORD boundary
    * rather than mid-word (`emergency_department_evaluation_and_management`, not
    * `emergency_department_evaluation_and_management_v`) — a half-word reads as a typo, and the name still
    * carries the full hash, so dropping readable characters can never merge two reads. It only shortens a
    * published dataset name that already carries a `{library}_{version}__` prefix. */
  private def slugify(name: String): String = {
    val unqualified = name.indexOf('.') match {
      case -1                                                   => name
      case i if name.take(i).nonEmpty && !name.take(i).contains(' ') => name.drop(i + 1)
      case _                                                    => name
    }
    val collapsed = unqualified.replaceAll("[^A-Za-z0-9]+", "_").toLowerCase
    val capped =
      if (collapsed.lengthIs <= SlugMaxChars) collapsed
      else {
        val cut = collapsed.take(SlugMaxChars)
        // Keep the cut only if it landed ON a boundary (the next character starts a new word); otherwise
        // fall back to the last complete word — unless there is none, in which case the hard cut stands.
        if (collapsed.charAt(SlugMaxChars) == '_') cut
        else cut.lastIndexOf('_') match {
          case -1 => cut
          case i  => cut.take(i)
        }
      }
    capped.stripPrefix("_").stripSuffix("_")
  }

  /** The readable slug's character budget. Bounded so a published dataset name
    * (`{library}_{version}__read_{type}_{slug}_{hash8}`) stays comfortably within a catalog's identifier
    * limit even for value-set names of 60 characters or more. */
  private val SlugMaxChars = 48

  /** Every filtered read in the library's LOWERED defines, grouped by identity — the full candidate set,
    * before the mode is applied. Deterministic: reads in first-occurrence order, defines in library order.
    *
    * Counts over the same define set `SqlFrameLowering` lowers (expression defines minus the context
    * defines), so a call site that is never emitted never inflates the reuse figure. */
  def candidates(lib: Library): Seq[Read] = {
    val codeFilterOf = RetrieveCatalog.codeFilterOf(lib)
    val contextNames = lib.contexts.map(_.name).toSet
    val defines = lib.statements.collect { case e: ExpressionDef => e }
      .filterNot(d => contextNames.contains(d.name))

    // (descriptor, define, retrieve) for every filtered call site, in library order. The descriptor is
    // computed ONCE per site — it walks a sub-expression, so re-deriving it while grouping would be
    // quadratic on a measure with many reads.
    val sites: Seq[(String, String, Retrieve)] = defines.flatMap { d =>
      d.expression.collect { case r: Retrieve if isFiltered(r) => r }
        .map(r => (descriptor(r, codeFilterOf), d.name, r))
    }

    // LinkedHashMap preserves FIRST-OCCURRENCE order (deterministic, and readable in the emitted script).
    val grouped = scala.collection.mutable.LinkedHashMap.empty[String, Seq[(String, Retrieve)]]
    sites.foreach { case (desc, define, r) =>
      grouped(desc) = grouped.getOrElse(desc, Vector.empty) :+ (define -> r)
    }

    val reads = grouped.toSeq.map { case (_, group) =>
      // Every node in the group shares the descriptor, so they agree on the hash and on every published
      // coordinate EXCEPT the value set's local alias — which the readable slug is built from. Pick the
      // representative by SORT rather than by first occurrence so the emitted name (a published dataset
      // name, hence a contract) cannot move when defines are reordered.
      val rep = group.map(_._2).minBy(objName(_, codeFilterOf))
      Read(
        obj       = objName(rep, codeFilterOf),
        nodes     = group.map(_._2).distinct,
        defines   = group.map(_._1).distinct,
        callSites = group.size,
        coords    = coordinatesOf(rep, codeFilterOf))
    }

    // The object name is a TRUNCATED hash, so distinct reads could in principle land on one name — which
    // would silently merge two different row sets into one table and apply the wrong predicate to half the
    // call sites. Astronomically unlikely at measure scale, and a loud failure either way
    // (CORE-NO-SILENT-MISLOWER): a wrong measure result is far more expensive than a failed compile.
    val collisions = reads.groupBy(_.obj).filter(_._2.sizeIs > 1)
    if (collisions.nonEmpty)
      throw new IllegalStateException(
        s"retrieve read name collision on ${collisions.keys.mkString(", ")} — distinct reads hashed alike; " +
          "widen the hash in RetrieveReads.objName")
    reads
  }

  /** The reads to materialize under `config` (`DATA-RETRIEVE-BARRIER-OPT`):
    *   - `none`  → nothing (today's behaviour, byte-for-byte);
    *   - `costly` → those reused at ≥ `materialize.retrieve_min_reuse` call sites;
    *   - `all`   → every filtered read.
    *
    * `costly` gates on REUSE rather than on "is filtered" because the candidate set is already exactly the
    * filtered reads — using the same predicate twice would make `costly` and `all` select identically. */
  def selected(lib: Library, config: Config): Seq[Read] = config.materializeRetrieves match {
    case Config.RetrieveMode.None   => Nil
    case Config.RetrieveMode.All    => candidates(lib)
    case Config.RetrieveMode.Costly => candidates(lib).filter(_.callSites >= config.materializeRetrieveMinReuse)
  }

  /** The published coordinates of one read. */
  def coordinatesOf(r: Retrieve, codeFilterOf: Expr => RetrieveCatalog.CodeFilter): Coordinates =
    Coordinates(
      resourceType = FhirModel.localName(r.dataType),
      profile      = r.profileConstraint,
      codeProperty = r.codeProperty,
      codeFilter   = r.codes.map(codeFilterOf),
      dateProperty = r.dateProperty,
      dateFiltered = r.dateRange.isDefined,
      periodWindow = r.periodScanFilter)
}
