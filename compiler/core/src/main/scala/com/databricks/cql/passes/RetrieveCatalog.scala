package com.databricks.cql.passes

import com.databricks.cql.ir._
import com.databricks.cql.sema.FhirModel

/** The RETRIEVE contract a consumer of `describe` reads (`BIND-DESCRIBE-RETRIEVES`): every data scan the
  * described slice will issue, and every terminology expansion it needs mounted.
  *
  * This is the terminology/scan TWIN of [[RetrieveDemand]]. That pass answers "which FIELDS must a
  * binding supply per resource type" (`EMIT-SQL-RETRIEVE-DEMAND-BOUND`); this one answers the questions a
  * binding cannot otherwise ask without re-parsing the CQL:
  *
  *   - does my `valuesets` table hold an expansion for every canonical URL this measure binds?
  *     ([[Catalog.valueSets]] — the emitted SQL reads `WHERE valueset_id = '<id>'`,
  *     `SqlEvalEnv.valueSetMount` / `DATA-ENVIRONMENT-LAYOUT`)
  *   - for resource type T, which column is filtered against which set — so a customer table can be
  *     pre-filtered, or a ConceptMap PREIMAGE computed (`BIND-CONCEPTMAP-PREIMAGE`, `data-binding.md`
  *     section 6.3) instead of translating every row.
  *
  * Pure IR → metadata (no Spark, no session), and computed from the SAME linked + pruned + OPTIMIZED
  * library the SQL back end lowers, so the catalog describes the scans that will actually run: date
  * pushdown has already folded into `Retrieve.dateRange`/`periodScanFilter`, and defines unreachable
  * from the requested populations are already gone.
  *
  * NEVER SILENTLY UNFILTERED (`CORE-NO-SILENT-MISLOWER`): a retrieve carrying `codes` in the IR always
  * surfaces with a `codeFilter`. When the bracket holds something this pass cannot name statically — a
  * parameter, an operand left over from function inlining, an expression — it becomes [[Other]] rather
  * than being dropped, so a consumer sees "filtered, by something I must go look up" instead of
  * mistaking a filtered scan for a full one. */
object RetrieveCatalog {

  /** What a retrieve's bracket (`[Type: <this>]`) filters its code property against. */
  sealed trait CodeFilter

  /** A named value set, resolved to its CANONICAL URL — the key the runtime's `valuesets` table is
    * keyed by. `name` is the (possibly library-qualified) local CQL alias the author wrote. */
  final case class ValueSet(id: String, name: String) extends CodeFilter

  /** Direct `code` / `concept` declarations, resolved to literal `(system, code)` pairs. These need NO
    * terminology mount at all — the codes are emitted inline — which is exactly why they must be
    * distinguishable from the value-set case rather than folded into it. */
  final case class Codes(codes: Seq[(String, String)], names: Seq[String]) extends CodeFilter

  /** A filter this pass cannot name statically (`elmType` is the IR node that carries it). Present so a
    * filtered retrieve is never reported as unfiltered. */
  final case class Other(elmType: String) extends CodeFilter

  /** One data scan the compiled slice will issue.
    *
    * @param define       the define whose body owns the scan.
    * @param resourceType the FHIR resource type scanned (localized, e.g. `"Encounter"`).
    * @param profile      the SPECIALISED profile canonical the scan constrains to (`LANG-RETRIEVE-PROFILE`),
    *                     or `None` when it constrains nothing beyond the resource type.
    * @param codeProperty the element the code filter applies to (`"type"`, `"code"`, `"medication"`).
    * @param codeFilter   what that element is filtered against; `None` only when the retrieve carries no
    *                     code filter at all.
    * @param dateProperty the element a folded date filter applies to, when there is one (`ARCH-PUSHDOWN`).
    * @param dateFiltered whether a date range or period scan window was folded onto the scan. */
  final case class RetrieveInfo(define: String,
                                resourceType: String,
                                profile: Option[String] = None,
                                codeProperty: Option[String] = None,
                                codeFilter: Option[CodeFilter] = None,
                                dateProperty: Option[String] = None,
                                dateFiltered: Boolean = false)

  /** One value set the slice binds, keyed by CANONICAL URL.
    *
    * Keyed by id, NOT by name, because the id is what the runtime looks up
    * (`WHERE valueset_id = '<id>'`) and one URL can carry several local names: after
    * [[LibraryLinker]] flattens a measure, a set declared by BOTH the root and a dependency survives as
    * two `ValueSetDef`s — a measure declares `Inpatient Encounter` and so does its `Helpers` dependency, so
    * the linked library has `Inpatient Encounter` and `Helpers.Inpatient Encounter` pointing at
    * one URL. Keyed by name a consumer would check that one expansion twice and report 30
    * requirements where there are 29. `names` carries every alias so a record still traces back to the
    * CQL the author wrote. */
  final case class ValueSetInfo(id: String, version: Option[String], names: Seq[String])

  /** The whole contract: every scan, and the terminology those scans need mounted. */
  final case class Catalog(retrieves: Seq[RetrieveInfo], valueSets: Seq[ValueSetInfo])

  /** Resolve what a retrieve's bracket filters against, for `lib`'s terminology declarations — the SINGLE
    * home for the rule, shared by [[analyze]] and by `RetrieveReads` (which needs the same coordinates to
    * describe a materialized read, `DATA-RETRIEVE-BARRIER-OPT`). Returns a function so a caller resolving
    * many brackets builds the declaration maps ONCE. */
  def codeFilterOf(lib: Library): Expr => CodeFilter = {
    val valueSetById   = lib.valueSets.map(v => v.name -> v).toMap
    val codeById       = lib.codes.map(c => c.name -> c).toMap
    val conceptById    = lib.concepts.map(c => c.name -> c).toMap
    val codeSystemById = lib.codeSystems.map(s => s.name -> s).toMap

    /** A `CodeDef` as a literal `(system, code)`, or `None` when its code system is undeclared. */
    def codePair(c: CodeDef): Option[(String, String)] =
      c.codeSystem.flatMap(codeSystemById.get).map(_.id -> c.id)

    /** Resolve whatever sits in the bracket. `ToList` is the wrapper the translator puts around a single
      * `code` used where a list is expected (`[Observation: "Some Code"]`), so it
      * is transparent here. Anything that does not fully resolve becomes [[Other]] — partial code lists
      * are NOT reported as `Codes`, since a consumer would read the short list as the whole filter. */
    def resolve(e: Expr): CodeFilter = e match {
      case ValueSetRef(name, _) =>
        valueSetById.get(name).map(v => ValueSet(v.id, v.name)).getOrElse(Other(s"ValueSetRef($name)"))

      case ToList(inner) => resolve(inner)

      case CodeRef(name, _) =>
        codeById.get(name).flatMap(codePair).map(p => Codes(Seq(p), Seq(name)))
          .getOrElse(Other(s"CodeRef($name)"))

      case ConceptRef(name, _) =>
        conceptById.get(name)
          .map(c => (c.codeNames, c.codeNames.flatMap(codeById.get).flatMap(codePair)))
          .filter { case (names, pairs) => pairs.sizeIs == names.size }
          .map { case (names, pairs) => Codes(pairs, names) }
          .getOrElse(Other(s"ConceptRef($name)"))

      // A concept already resolved to literals by `ConceptResolver` (LANG-TERMINOLOGY).
      case ConceptValue(codes) => Codes(codes, Nil)

      case ListExpr(elems) if elems.nonEmpty =>
        val parts = elems.map(resolve)
        if (parts.forall(_.isInstanceOf[Codes]))
          Codes(parts.collect { case c: Codes => c }.flatMap(_.codes),
                parts.collect { case c: Codes => c }.flatMap(_.names))
        else Other("List")

      case other => Other(other.elmType)
    }

    resolve
  }

  /** Build the catalog for a linked/normalized/optimized library. */
  def analyze(lib: Library): Catalog = {
    val codeFilterOf = RetrieveCatalog.codeFilterOf(lib)

    val retrieves = lib.statements.collect { case d: ExpressionDef => d }.flatMap { d =>
      d.expression.collect {
        case r: Retrieve =>
          RetrieveInfo(
            define       = d.name,
            resourceType = FhirModel.localName(r.dataType),
            profile      = r.profileConstraint,
            codeProperty = r.codeProperty,
            codeFilter   = r.codes.map(codeFilterOf),
            dateProperty = r.dateProperty,
            dateFiltered = r.dateRange.isDefined || r.periodScanFilter.isDefined)
      }.distinct // the same scan reached twice in one body is one scan to a consumer
    }

    // Coverage: the value sets the reported scans actually BIND, keyed by canonical id with every local
    // alias attached. Declared-but-unreferenced sets do not appear — `describe` reports the pruned slice.
    // When several declarations of one URL disagree on the version pin (an authoring problem, not
    // something this pass can resolve), take the lowest by sort order so the contract stays DETERMINISTIC
    // rather than dependent on declaration order.
    val valueSets = retrieves.collect { case RetrieveInfo(_, _, _, _, Some(v: ValueSet), _, _) => v }
      .groupBy(_.id).toSeq.sortBy(_._1)
      .map { case (id, vs) =>
        ValueSetInfo(id, lib.valueSets.filter(_.id == id).flatMap(_.version).distinct.sorted.headOption,
          vs.map(_.name).distinct.sorted)
      }

    Catalog(retrieves, valueSets)
  }
}
