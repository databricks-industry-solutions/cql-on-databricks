package com.databricks.cql.retrieve

import org.apache.spark.sql.DataFrame

/** The terminology seam (LANG-TERMINOLOGY, DATA-TERMINOLOGY-ADAPTER). Value-set membership is a
  * broadcast semi-join, so the provider returns a value set's expansion as a small frame of
  * `(system, code)` rows — pre-expanded and version-pinned upstream. The engine joins resource
  * codings against it; it never calls a terminology service at evaluation time.
  *
  * Supplied by the host (deployed adapter, or the conformance harness reading sample expansions),
  * so engine-core stays free of any terminology source's specifics. */
trait TerminologyProvider {

  /** The expansion of the value set identified by `valueSetId` (its canonical URL) as a frame
    * with string columns `system` and `code`. */
  def expand(valueSetId: String): DataFrame
}
