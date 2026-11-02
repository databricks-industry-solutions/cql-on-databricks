package com.databricks.cql.runtime

/** How to turn a collected Spark cell into the caller's result value type `V`, and how to build the
  * list / empty-list / null-state sentinels a grain-first list define collapses to. The value
  * REPRESENTATION is the consumer's job: the conformance harness renders `ujson.Value` for the
  * ground-truth diff (`Differential.UjsonResults`), a notebook might render display strings, etc. Keeps
  * the collect surface free of any JSON/diff library (BUILD-MODULES).
  *
  * The SQL-path collector (`SqlDifferential.collect`) is its sole user. */
trait ResultBuilder[V] {
  /** One collected cell (a scalar define's value, or one element of a list define). */
  def cell(raw: Any): V
  /** A list-valued (grain-first) define's assembled, order-preserved elements. */
  def list(values: Seq[V]): V
  /** A list define that produced no rows and is NOT null-state (empty list). */
  def emptyList: V
  /** A list define whose body evaluated to NULL (ARCH-LIST-NULL-SIDECAR / LANG-LIST-NULL-STATE). */
  def nullValue: V
}
