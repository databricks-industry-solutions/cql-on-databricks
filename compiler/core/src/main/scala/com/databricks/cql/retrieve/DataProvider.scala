package com.databricks.cql.retrieve

import com.databricks.cql.ir.Expr
import org.apache.spark.sql.DataFrame

/** The Retriever/DataProvider seam (DATA-RETRIEVER, `03`). Set-at-a-time data access: given a FHIR
  * resource type, return ALL instances as ONE frame carrying the context grouping key `__subject_id`
  * (SystemColumns.SubjectId) and the whole resource as a single struct column `__resource`
  * (SystemColumns.Resource) — the SINGLE schema contract every adapter returns (DATA-RETRIEVE-VIEW,
  * [[ResourceStruct]]). For `Condition` the subject is the `subject` reference's id; for `Patient` it
  * is the resource id.
  *
  * The implementation is supplied by the host (the deployed app, or the conformance harness reading
  * sample data) so engine-core stays free of FHIR-shape and storage specifics. */
trait DataProvider {

  /** All instances of `fhirType` (local name, e.g. "Condition") as a frame whose schema is exactly
    * `(__subject_id, STRUCT<resource + __resource_type + __resource_id> AS __resource)` — built via
    * [[ResourceStruct.of]]. Returns an empty-but-typed frame when there are none.
    *
    * `hints` are ADVISORY, STATICALLY-DETERMINED pushdown hints (the compile-time predicates the
    * optimizer already knows): a complex/multi-stage production adapter MAY honor as many as feasible
    * to PRUNE the scan; the local sample-data adapter IGNORES them. Honoring must be SUPERSET-only —
    * correctness NEVER depends on the adapter, because the engine re-applies every predicate at its
    * natural position in the plan. CONF-GATE-SELFDIFF (pushdown on vs off) proves hints-honored is
    * identical to hints-ignored. */
  def retrieve(fhirType: String, hints: RetrieveHints = RetrieveHints.none): DataFrame
}

/** STATIC, compile-time-determined pushdown hints for a [[DataProvider.retrieve]] (DATA-RETRIEVE-VIEW).
  * Runtime-adaptive querying is OUT OF SCOPE — only hints the optimizer knows at compile time are
  * populated. A non-Spark adapter may interpret or ignore them.
  *
  * @param valueSets        terminology semi-join hints `(codeProperty, valueSetId)` — value-set
  *                         membership is a broadcast semi-join against an expansion, NOT a plain
  *                         column predicate, so it stays separate from [[columnPredicates]].
  * @param codes            single-code membership hints `(codeProperty, system, code)` — likewise a
  *                         terminology test, kept separate from [[columnPredicates]].
  * @param columnPredicates ARBITRARY, resource-rooted (an `AliasRef("__r")` root), STATICALLY-
  *                         DETERMINED column predicates — the escape hatch for any compile-time
  *                         pushdownable `where`-conjunct (date-range, period window, …). Represented
  *                         as engine-natural IR `Expr` so a Spark adapter can lower them and a
  *                         non-Spark adapter can interpret or ignore them. */
/** @param project ARCH-RETRIEVE-PROJECT demand-driven projection: when `Some(fields)`, only these
  *                top-level resource fields are navigated anywhere in the library, so the adapter MAY
  *                narrow the read + [[ResourceStruct.of]] to them (plus its own subject-key / `id`
  *                fields). Unlike the other (superset-only) hints, the engine RE-PROJECTS the mount to
  *                this exact set as a correctness guard, so honoring is a pure read/proto optimization —
  *                correctness never depends on it. `None` → the whole modeled body (a type left FULL, or
  *                the flag off). */
final case class RetrieveHints(
    valueSets:        Seq[(String, String)] = Nil,
    codes:            Seq[(String, String, String)] = Nil,
    columnPredicates: Seq[Expr] = Nil,
    project:          Option[Set[String]] = None)

object RetrieveHints {
  val none = RetrieveHints()
}
