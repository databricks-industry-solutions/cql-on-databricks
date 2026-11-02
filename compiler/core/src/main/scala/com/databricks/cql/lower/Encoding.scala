package com.databricks.cql.lower

/** The list/value encoding a lowered fragment is in (DATA-VALUE-TAXONOMY, DATA-LIST-ENCODING). A tracked
  * physical property read by the pure passes (`TypeEncoding`, `LetQueryInliner`) that decide a define's
  * grain. Kept in the `lower` package (EXEC-SQL-BACKEND); the SQL
  * emitter carries the same distinction through its own relation/scalar lowering. */
sealed trait Encoding
object Encoding {
  case object Scalar      extends Encoding
  case object Relation    extends Encoding // default for lists; carries `__ordinal`
  case object Array       extends Encoding
  case object Struct      extends Encoding
  case object Choice      extends Encoding // discriminated struct (LANG-CHOICE)
}
