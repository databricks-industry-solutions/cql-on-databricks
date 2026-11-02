package com.databricks.cql.spark

import org.apache.spark.sql.Column
import org.apache.spark.sql.functions.col

/** The reserved `__*` system-column namespace (DATA-SYSTEM-COLUMNS, `03` section 4).
  *
  * Double-underscore is safe against source data: FHIR JSON uses a single underscore for
  * primitive extensions (`_birthDate`); QDM uses none. The Retriever rejects any SOURCE
  * column matching `__*` (enforced in Phase 2).
  *
  * This object is also the first real touch of Spark in engine-core: it pins the
  * `compileOnly` Spark 4.0.0 dependency (TECH-TOOLCHAIN), so a broken coordinate or an
  * API drift fails the build here rather than silently in Phase 1.
  */
object SystemColumns {

  /** The context subject's identifier; the grouping/join key for set-at-a-time
    * evaluation (ARCH-SET-EVAL). */
  val SubjectId    = "__subject_id"

  /** The single synthetic `__subject_id` value for an ungrouped context
    * (Unfiltered/Population — LANG-CONTEXT). An Unfiltered library is just the degenerate
    * one-subject case of the set-at-a-time frame (ARCH-SET-EVAL), so one base frame and one
    * lowering path serve every context (PROC-DRY) instead of a separate scalar path. */
  val UnfilteredSubject = "*"

  /** Element order within a Relation-encoded list — mandatory end to end (DATA-LIST-ENCODING).
    * The difference between passing and silently failing First/Last/indexed access. */
  val Ordinal      = "__ordinal"

  /** The VALUE column every define result carries (`EMIT-SQL-VALUE-COLUMN`) — the one payload name a
    * consumer reads, whatever lowering path produced the define:
    *   - a scalar define publishes `(__subject_id, __val)`;
    *   - a grain-first Relation define publishes `(__subject_id, __ordinal, __val)`, one row per element.
    *
    * UNIFORM BY CONSTRUCTION: the define's own name is NOT part of its result schema (it names the OBJECT
    * only, `SqlNames.obj`), so a generic reader never re-derives `SqlNames.sanitize` nor branches on
    * whether a define happened to lower as a scalar or a relation. The kind travels as METADATA on the
    * compile contract (`DefineShape`), never as a schema difference to be sniffed at runtime
    * (`ARCH-NO-RUNTIME-SCHEMA`).
    *
    * A define's value column is UNIQUE within its own object but NOT across a join of several defines, so
    * a frame that reads N dependency defines RENAMES each `__val` to that dependency's object name inside
    * the join subquery (`SqlFrameLowering.scalarDepJoin`) — the same rename-at-join the `__is_null` sidecar
    * already uses. Aliasing the joined relation instead does NOT work: a define's projection sits outside a
    * derived-table wrap (`asFrom`) and `SqlHoist` re-projects through `*`, so join-level aliases are out of
    * scope by the time the value expression is built. */
  val Val          = "__val"

  /** Marks a value's list-encoding state (Array vs Relation, DATA-LIST-ENCODING). */
  val Encoding     = "__encoding"

  /** Choice discriminator tag (LANG-CHOICE). */
  val Choice       = "__choice"

  /** Export lineage — which source instance fed a result (DATA-EXPORT). */
  val ResourceId   = "__resource_id"
  val ResourceType = "__resource_type"

  /** The whole resource carried by a retrieve mount as ONE struct column (BIND-CONTRACT): the
    * single schema contract `(__subject_id, STRUCT<resource + __resource_type + __resource_id> AS
    * __resource)` every `DataProvider` adapter returns. The struct FLOWS THROUGH the plan — every
    * retrieve consumer navigates it (`__resource.<field>`) rather than rebuilding it. */
  val Resource     = "__resource"

  /** The reserved prefix. The Retriever rejects source columns matching it. */
  val ReservedPrefix = "__"

  val all: Set[String] = Set(SubjectId, Ordinal, Val, Encoding, Choice, ResourceId, ResourceType, Resource)

  /** True if a (source) column name intrudes on the reserved namespace. */
  def isReserved(name: String): Boolean = name.startsWith(ReservedPrefix)

  /** Convenience: a Spark `Column` reference to the subject key. */
  def subjectId: Column = col(SubjectId)

  /** Convenience: a Spark `Column` reference to the ordinal. */
  def ordinal: Column = col(Ordinal)
}
