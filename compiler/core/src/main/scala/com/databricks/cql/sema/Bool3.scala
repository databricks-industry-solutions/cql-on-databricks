package com.databricks.cql.sema

/** CQL three-valued logic (LANG-3VL, `01` section 1).
  *
  * CQL booleans are `true | false | null`, and null propagation is the single most common
  * source of subtle bugs. CQL's 3VL resembles SQL's but is NOT identical — and Spark's
  * boolean defaults diverge from CQL on nulls — so EVERY boolean-producing CQL operator
  * routes through these primitives, never bare Spark `=`/`AND`/`OR`/`NOT` (the global
  * verifier in CONF-VERIFIER enforces this once lowering exists).
  *
  * This is the pure, plan-independent realization (TECH-SEMANTIC-LIB): it is the unit of
  * per-operator conformance testing (Gate 1, CONF-GATE-OPERATOR) and the source of truth the
  * Phase 1 native Catalyst `Column` realization must match. Implemented here as a closed
  * algebra so the truth tables are auditable in one place.
  */
sealed trait Bool3 {
  def and(other: Bool3): Bool3 = Bool3.and(this, other)
  def or(other: Bool3): Bool3 = Bool3.or(this, other)
  def xor(other: Bool3): Bool3 = Bool3.xor(this, other)
  def implies(other: Bool3): Bool3 = Bool3.implies(this, other)
  def negate: Bool3 = Bool3.not(this)

  /** The library truth test (`cqlTrue`): true ONLY when the value is exactly `true`. */
  def isCqlTrue: Boolean = this == Bool3.True

  /** The else/negative test (`not cqlTrue(x)`): true for `false` OR `null`. */
  def isNotCqlTrue: Boolean = !isCqlTrue
}

object Bool3 {
  case object True  extends Bool3
  case object False extends Bool3
  case object Null  extends Bool3

  def fromBoolean(b: Boolean): Bool3 = if (b) True else False
  def fromOption(o: Option[Boolean]): Bool3 = o match {
    case Some(true)  => True
    case Some(false) => False
    case None        => Null
  }
  def toOption(b: Bool3): Option[Boolean] = b match {
    case True  => Some(true)
    case False => Some(false)
    case Null  => None
  }

  /** `cqlTrue(x)` — true only when `x` is exactly `true` (`01` section 1). Used by conditionals:
    * a null condition is not-true and routes to the `else` branch (ARCH-CONDITIONAL). */
  def cqlTrue(b: Bool3): Boolean = b == True

  /** `not cqlTrue(x)` — the negative/else test: true for `false` or `null`. */
  def notCqlTrue(b: Bool3): Boolean = b != True

  // CQL truth tables (CQL Reference, operator definitions).

  def not(b: Bool3): Bool3 = b match {
    case True  => False
    case False => True
    case Null  => Null
  }

  def and(a: Bool3, b: Bool3): Bool3 = (a, b) match {
    case (False, _) | (_, False) => False        // false dominates
    case (True, True)            => True
    case _                       => Null          // any remaining null -> null
  }

  def or(a: Bool3, b: Bool3): Bool3 = (a, b) match {
    case (True, _) | (_, True)   => True          // true dominates
    case (False, False)          => False
    case _                       => Null          // any remaining null -> null
  }

  def xor(a: Bool3, b: Bool3): Bool3 = (a, b) match {
    case (Null, _) | (_, Null) => Null            // null is contagious for xor
    case (True, False) | (False, True) => True
    case _ => False
  }

  /** `a implies b` ≡ `(not a) or b`, evaluated under 3VL. */
  def implies(a: Bool3, b: Bool3): Bool3 = or(not(a), b)
}
