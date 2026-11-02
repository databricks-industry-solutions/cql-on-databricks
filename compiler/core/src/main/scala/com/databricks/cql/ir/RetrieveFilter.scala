package com.databricks.cql.ir

/** Pure structural analysis of a query `where` for retrieve pushdown (ARCH-PUSHDOWN) and its
  * unoptimized lowering twin (LANG-TERMINOLOGY). Shared so the optimizer rule
  * (`rules.TerminologyPushdown`) and the lowering (`lower.QueryLowering`) recognize a value-set
  * membership conjunct IDENTICALLY (PROC-DRY) — the rule folds exactly what the lowering would
  * otherwise realize as a semi-join, which is what makes the optimized/unoptimized self-diff a
  * meaningful gate.
  */
object RetrieveFilter {

  /** Flatten a conjunction into its top-level conjuncts (a non-`And` predicate is one conjunct).
    * Pushdown classifies and folds per conjunct; a predicate buried under `Or`/`Not` is never
    * decomposed, so it stays in place (and lowers as written). */
  def conjuncts(e: Expr): Seq[Expr] = e match {
    case And(a, b) => conjuncts(a) ++ conjuncts(b)
    case other     => Seq(other)
  }

  /** A value-set-membership conjunct `<alias>.<path> in <ValueSet>`: the query alias it filters, the
    * code property path, and the value set. Unwraps the FHIRHelpers `ToConcept` the translator
    * inserts around the navigated code. `None` if the conjunct is not a single-alias membership
    * test (it then stays a residual predicate). Accepts both the pre-bind `scope` form
    * (`Property(scope = a, …)`) and the post-bind `AliasRef` form. */
  def membership(e: Expr): Option[(String, String, ValueSetRef)] = e match {
    case InValueSet(code, vs: ValueSetRef) => codeAliasPath(code).map { case (a, p) => (a, p, vs) }
    case _                                 => None
  }

  /** A date-range conjunct `<alias>.<dateProperty> during <interval>` over a FHIR retrieve date
    * (CQL point-in-interval): the alias, the date property path, the range interval, and the
    * FHIRHelpers converter for the point (`"ToDateTime"` for a `dateTime` property, `"ToDate"` for a
    * `date` property). The converter is recorded on the folded retrieve so the scan filter
    * reconstructs an IDENTICAL predicate — same precision (DateTime vs Date) — to the unoptimized
    * `where` (exact self-diff). Matched only when the interval is SCAN-SAFE — a constant (a
    * Measurement-Period parameter is inlined to a literal by `ParameterResolver`, so it lowers
    * standalone at the retrieve scan; a data-dependent range stays in the `where`). Both single-point
    * converters fold exactly; coarser Period-property pushdown is deferred. `None` otherwise. */
  def dateRange(e: Expr): Option[(String, String, Expr, String)] = e match {
    case In(FunctionRef(conv @ ("ToDateTime" | "ToDate"), Some("FHIRHelpers"), Seq(prop)), interval)
        if scanSafe(interval) =>
      propAliasPath(prop).map { case (a, p) => (a, p, interval, conv) }
    case _ => None
  }

  /** A Period-property `during` conjunct `<alias>.<periodPath> during <literal interval>` (the
    * interval-in-interval `IncludedIn` the translator emits for `C.period during MP`, wrapping the
    * Period in `FHIRHelpers.ToInterval`): the alias, the period property path, and the half-open YEAR
    * window `(lowInclusive, highExclusive)` as zero-padded 4-digit strings (`"2023"`, `"2024"`).
    *
    * This drives [[com.databricks.cql.rules.PeriodRangePushdown]], which folds a COARSE,
    * non-lossy scan pre-filter (`period.end >= lowInclusive AND period.start < highExclusive` on the
    * RAW ISO strings) and KEEPS the precise `during` (ARCH-PUSHDOWN section 7 step 6). The window is
    * YEAR-granular on purpose: it is the coarsest precision a partial FHIR date (`"2023"`) can carry,
    * so a year prefix is the only bound that never drops a partial that `during` would include
    * (lexicographic: `"2023" < "2023-06-15" < "2024"`). Matched only for a SCAN-SAFE (constant)
    * interval whose bounds carry an in-range 4-digit year; `None` otherwise. */
  def periodDuring(e: Expr): Option[(String, String, String, String)] = e match {
    // The relation's named precision (LANG-INTERVAL-PRECISION) is irrelevant here: this is a COARSE
    // year-prefix scan bound, deliberately wider than the precise predicate, which is re-applied in full
    // afterwards. Any precision only narrows that predicate, never the year window — so match either way.
    case IncludedIn(FunctionRef("ToInterval", Some("FHIRHelpers"), Seq(prop)), interval, _) if scanSafe(interval) =>
      for {
        (alias, path)  <- propAliasPath(prop)
        (lowYr, hiExcl) <- yearWindow(interval)
      } yield (alias, path, lowYr, hiExcl)
    case _ => None
  }

  /** The half-open year window of a literal interval as zero-padded 4-digit strings `(low, highExcl)`
    * — `Interval[@2023-…, @2023-…]` → `("2023", "2024")`. `None` unless both bounds are literal
    * date/dateTimes whose year is in a safe range `[1, 9998]` (so `highExcl = high+1` stays a 4-digit
    * string that compares correctly — a 5-digit `"10000"` would sort BELOW `"9999-…"` and make the
    * pre-filter lossy). */
  private def yearWindow(interval: Expr): Option[(String, String)] = interval match {
    case IntervalExpr(low, high, _, _) =>
      for {
        lo <- low.flatMap(yearOf)
        hi <- high.flatMap(yearOf)
        if lo >= 1 && hi >= 1 && hi <= 9998
      } yield (f"$lo%04d", f"${hi + 1}%04d")
    case _ => None
  }

  /** The (literal) year component of a date/dateTime expression — its first part. */
  private def yearOf(e: Expr): Option[Int] = e match {
    case DateTimeExpr(parts) => parts.headOption.flatMap(litInt)
    case DateExpr(parts)     => parts.headOption.flatMap(litInt)
    case _                   => None
  }
  private def litInt(e: Expr): Option[Int] = e match {
    case Literal(_, Some(raw)) => raw.toIntOption
    case _                     => None
  }

  /** True if `e` is a constant expression — no reference that needs the surrounding plan to lower
    * (a define/alias/parameter/operand/let). Such an expression can be lowered at the retrieve scan. */
  def scanSafe(e: Expr): Boolean = !e.exists {
    case _: ExpressionRef | _: AliasRef | _: ParameterRef | _: OperandRef | _: QueryLetRef => true
    case _                                                                                 => false
  }

  /** Unwrap the FHIRHelpers converter(s) the translator wraps a navigated code in, down to the
    * `<alias>.<path>` it navigates. */
  private def codeAliasPath(code: Expr): Option[(String, String)] = code match {
    case FunctionRef(_, Some("FHIRHelpers"), Seq(inner)) => codeAliasPath(inner)
    case other                                           => propAliasPath(other)
  }

  /** The `<alias>.<path>` a navigated property addresses — the pre-bind `scope` form or the
    * post-bind `AliasRef` form. */
  private def propAliasPath(e: Expr): Option[(String, String)] = e match {
    case Property(None, Some(alias), path)        => Some((alias, path))
    case Property(Some(AliasRef(alias)), _, path) => Some((alias, path))
    case _                                        => None
  }
}
