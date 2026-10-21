package com.databricks.cql.ir

/** The engine's intermediate representation: a sealed ADT mirroring the normalized
  * ELM kernel (ARCH-IR, `engine-design.md` section 3).
  *
  * Why a sealed ADT and not raw ELM JSON: pattern matching, exhaustiveness checking
  * (the Scala compiler forces every kernel node to be handled or explicitly rejected —
  * ARCH-ROBUSTNESS pillar 1), and a place to attach engine metadata.
  *
  * PHASE-0 SCOPE. We model the library structure fully (so any measure round-trips)
  * plus a representative core of expression nodes. Every ELM node we do NOT yet model
  * is lifted into an explicit `UnknownExpr` / `UnknownStatement` that round-trips
  * losslessly here but is REJECTED loudly at Lower-time as an `UnsupportedNode`
  * (CORE-NO-SILENT-MISLOWER, ARCH-ROBUSTNESS). It is never silently dropped and never
  * produces a plausible-but-wrong plan. Modeling more nodes is the work of Phases 1-4
  * (each arrives as an ADT node + desugaring rule + tests, ARCH-SEAMS).
  */
sealed trait IrNode extends TreeNode[IrNode]

object Expr {
  /** Children of an expression are always expressions; transforms preserve type.
    * Coerce defensively and fail loud if that invariant is ever violated. */
  def coerce(n: IrNode): Expr = n match {
    case e: Expr => e
    case other   =>
      throw new IllegalStateException(
        s"expected an Expr child but found ${other.getClass.getSimpleName}")
  }
}

/** A CQL expression. Every concrete node carries `elmType`, the ELM `type`
  * discriminator, used by Load/Emit and for diagnostics. */
sealed trait Expr extends IrNode {
  def elmType: String
}

/** Leaf expression: no children. */
sealed trait LeafExpr extends Expr {
  final def children: Seq[IrNode] = Nil
  final def withNewChildren(newChildren: Seq[IrNode]): IrNode = this
}

/** Binary operator shape; the ELM type string discriminates the operator. */
sealed abstract class BinaryExpr extends Expr {
  def left: Expr
  def right: Expr
  protected def rebuild(l: Expr, r: Expr): BinaryExpr
  final def children: Seq[IrNode] = Seq(left, right)
  final def withNewChildren(n: Seq[IrNode]): IrNode =
    rebuild(Expr.coerce(n(0)), Expr.coerce(n(1)))
}

/** Unary operator shape. */
sealed abstract class UnaryExpr extends Expr {
  def operand: Expr
  protected def rebuild(o: Expr): UnaryExpr
  final def children: Seq[IrNode] = Seq(operand)
  final def withNewChildren(n: Seq[IrNode]): IrNode = rebuild(Expr.coerce(n(0)))
}

/** N-ary operator shape (variadic operands). */
sealed abstract class NaryExpr extends Expr {
  def operands: Seq[Expr]
  protected def rebuild(os: Seq[Expr]): NaryExpr
  final def children: Seq[IrNode] = operands
  final def withNewChildren(n: Seq[IrNode]): IrNode = rebuild(n.map(Expr.coerce))
}

// ---------------------------------------------------------------------------
// Leaves
// ---------------------------------------------------------------------------

/** ELM `Null`. Distinct from "empty"; see LANG-LIST-NULL for the deferred
  * empty-vs-null-list question. */
final case class NullExpr() extends LeafExpr { val elmType = "Null" }

/** ELM `Literal` — `valueType` is a system type URI/name, `value` its lexical form. */
final case class Literal(valueType: String, value: Option[String]) extends LeafExpr {
  val elmType = "Literal"
}

/** ELM `Quantity` — a `(value, unit)` pair (LANG-QUANTITY). UCUM conversion is a
  * Phase 3 concern; Phase 0 only carries the literal. */
final case class QuantityExpr(value: BigDecimal, unit: Option[String]) extends LeafExpr {
  val elmType = "Quantity"
}

/** ELM `Date` / `DateTime` (LANG-INTERVAL). The temporal value's PRECISION is implied by how
  * many components are present — `parts` holds `[year, month?, day?]` for a Date and
  * `[year, month?, day?, hour?, minute?, second?, millisecond?]` for a DateTime, in significance
  * order. Components are expressions (literal integers in authored CQL). Comparisons over these
  * are precision-aware (can return null) — realized in lowering, never on native Spark date ops. */
final case class DateExpr(parts: Seq[Expr]) extends NaryExpr {
  val elmType = "Date"
  def operands: Seq[Expr] = parts
  protected def rebuild(os: Seq[Expr]) = DateExpr(os)
}
final case class DateTimeExpr(parts: Seq[Expr]) extends NaryExpr {
  val elmType = "DateTime"
  def operands: Seq[Expr] = parts
  protected def rebuild(os: Seq[Expr]) = DateTimeExpr(os)
}
/** ELM `Time` — a time-of-day (LANG-INTERVAL). `parts` holds `[hour, minute?, second?, millisecond?]`
  * in significance order; precision is implied by the component count. Compared precision-aware like
  * Date/DateTime (lowered as a temporal value on a sentinel date). */
final case class TimeExpr(parts: Seq[Expr]) extends NaryExpr {
  val elmType = "Time"
  def operands: Seq[Expr] = parts
  protected def rebuild(os: Seq[Expr]) = TimeExpr(os)
}
/** ELM `Precision` — `precision of x`: the number of significant digits of a Decimal, or the
  * digit-count of a Date/DateTime/Time's precision (CQL Reference). Returns Integer. */
final case class Precision(operand: Expr) extends UnaryExpr {
  val elmType = "Precision"; protected def rebuild(o: Expr) = Precision(o)
}

/** ELM `Ratio` — a `numerator : denominator` pair of quantities (LANG-QUANTITY). Equality and
  * equivalence compare component-wise. */
final case class RatioExpr(numerator: Expr, denominator: Expr) extends Expr {
  val elmType = "Ratio"
  def children: Seq[IrNode] = Seq(numerator, denominator)
  def withNewChildren(n: Seq[IrNode]): IrNode = RatioExpr(Expr.coerce(n(0)), Expr.coerce(n(1)))
}

/** ELM `LowBoundary` / `HighBoundary` — the least/greatest value at a given precision consistent
  * with the (partially-specified) input (CQL Reference). `left` is the value, `right` the target
  * precision (a digit-count for Date/DateTime/Time, decimal places for Decimal; may be null → the
  * type's default). For a Decimal the unspecified low places fill with 0 / high with 9; for a
  * temporal the unspecified components fill with their min / max. */
final case class LowBoundary(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "LowBoundary"; protected def rebuild(l: Expr, r: Expr) = LowBoundary(l, r)
}
final case class HighBoundary(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "HighBoundary"; protected def rebuild(l: Expr, r: Expr) = HighBoundary(l, r)
}

final case class ExpressionRef(name: String, libraryName: Option[String]) extends LeafExpr {
  val elmType = "ExpressionRef"
}
final case class ParameterRef(name: String, libraryName: Option[String]) extends LeafExpr {
  val elmType = "ParameterRef"
}
final case class OperandRef(name: String) extends LeafExpr { val elmType = "OperandRef" }
/** A reference to a query source alias (LANG-QUERY) — bound to the iterated element during
  * query lowering. */
final case class AliasRef(name: String) extends LeafExpr { val elmType = "AliasRef" }
/** ELM `IdentifierRef` (LANG-QUERY-SORT) — a BARE identifier the translator could not bind to a
  * concrete ref, emitted for a property of the IMPLICIT scope element. Its only corpus occurrence is
  * inside a `sort by <expr>` key, where CQL exposes no alias for the element being sorted (e.g.
  * `sort by end of effective.effectiveSpan()` — `effective` is a property of the sort element). The
  * `IdentifierRefResolver` normalize pass rewrites it to a scope-rooted [[Property]] against the
  * enclosing query's source alias before Lower, so it never reaches typing/lowering as a leaf. */
final case class IdentifierRef(name: String) extends LeafExpr { val elmType = "IdentifierRef" }
/** A reference to a query `let` binding or the `aggregate` accumulator (LANG-QUERY-AGGREGATE) —
  * a name bound in query scope, resolved through the same alias-binding machinery as [[AliasRef]]. */
final case class QueryLetRef(name: String) extends LeafExpr { val elmType = "QueryLetRef" }
final case class ValueSetRef(name: String, libraryName: Option[String]) extends LeafExpr {
  val elmType = "ValueSetRef"
}
final case class CodeSystemRef(name: String, libraryName: Option[String]) extends LeafExpr {
  val elmType = "CodeSystemRef"
}
final case class CodeRef(name: String, libraryName: Option[String]) extends LeafExpr {
  val elmType = "CodeRef"
}
final case class ConceptRef(name: String, libraryName: Option[String]) extends LeafExpr {
  val elmType = "ConceptRef"
}
/** ELM system `ToConcept` — promote a `Code` to a `Concept` (the implicit conversion the translator
  * inserts when a code is used where a concept is expected, e.g. `clinicalStatus ~ "active"`).
  * Resolved to a literal [[ConceptValue]] by the [[com.databricks.cql.passes.ConceptResolver]]
  * normalize pass; it never reaches lowering (LANG-TERMINOLOGY). */
final case class ToConcept(operand: Expr) extends UnaryExpr {
  val elmType = "ToConcept"; protected def rebuild(o: Expr) = ToConcept(o)
}
/** A compile-time-resolved CQL Code/Concept value: the `(codeSystemUrl, code)` pairs a `CodeRef`/
  * `ConceptRef`/`ToConcept` resolves to at Normalize ([[com.databricks.cql.passes.ConceptResolver]],
  * LANG-TERMINOLOGY). Lowered only as an operand of `~` (Equivalent) against a FHIR CodeableConcept —
  * a coding-membership test over its `.coding` array (`ColumnLowering`). A bare `Code` and a one-code
  * `Concept` behave identically under `~`, so both collapse to this single node. */
final case class ConceptValue(codes: Seq[(String, String)]) extends LeafExpr {
  val elmType = "ConceptValue"
}

// ---------------------------------------------------------------------------
// Logical (LANG-3VL) — these MUST lower through the library's 3VL primitives,
// never bare Spark booleans. Phase 0 models them; Phase 1 lowers them.
// ---------------------------------------------------------------------------

final case class And(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "And"; protected def rebuild(l: Expr, r: Expr) = And(l, r)
}
final case class Or(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Or"; protected def rebuild(l: Expr, r: Expr) = Or(l, r)
}
final case class Xor(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Xor"; protected def rebuild(l: Expr, r: Expr) = Xor(l, r)
}
final case class Implies(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Implies"; protected def rebuild(l: Expr, r: Expr) = Implies(l, r)
}
final case class Not(operand: Expr) extends UnaryExpr {
  val elmType = "Not"; protected def rebuild(o: Expr) = Not(o)
}

// Equality (=) vs equivalence (~) are DISTINCT operators (LANG-3VL); never collapse.
final case class Equal(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Equal"; protected def rebuild(l: Expr, r: Expr) = Equal(l, r)
}
final case class Equivalent(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Equivalent"; protected def rebuild(l: Expr, r: Expr) = Equivalent(l, r)
}
final case class NotEqual(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "NotEqual"; protected def rebuild(l: Expr, r: Expr) = NotEqual(l, r)
}
final case class Less(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Less"; protected def rebuild(l: Expr, r: Expr) = Less(l, r)
}
final case class Greater(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Greater"; protected def rebuild(l: Expr, r: Expr) = Greater(l, r)
}
final case class LessOrEqual(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "LessOrEqual"; protected def rebuild(l: Expr, r: Expr) = LessOrEqual(l, r)
}
final case class GreaterOrEqual(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "GreaterOrEqual"; protected def rebuild(l: Expr, r: Expr) = GreaterOrEqual(l, r)
}
// `is true` / `is false` (LANG-3VL): a definite (never-null) boolean test — null is neither.
final case class IsTrue(operand: Expr) extends UnaryExpr {
  val elmType = "IsTrue"; protected def rebuild(o: Expr) = IsTrue(o)
}
final case class IsFalse(operand: Expr) extends UnaryExpr {
  val elmType = "IsFalse"; protected def rebuild(o: Expr) = IsFalse(o)
}
final case class IsNull(operand: Expr) extends UnaryExpr {
  val elmType = "IsNull"; protected def rebuild(o: Expr) = IsNull(o)
}

// ---------------------------------------------------------------------------
// Arithmetic
// ---------------------------------------------------------------------------

final case class Add(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Add"; protected def rebuild(l: Expr, r: Expr) = Add(l, r)
}
final case class Subtract(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Subtract"; protected def rebuild(l: Expr, r: Expr) = Subtract(l, r)
}
final case class Multiply(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Multiply"; protected def rebuild(l: Expr, r: Expr) = Multiply(l, r)
}
final case class Divide(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Divide"; protected def rebuild(l: Expr, r: Expr) = Divide(l, r)
}
final case class TruncatedDivide(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "TruncatedDivide"; protected def rebuild(l: Expr, r: Expr) = TruncatedDivide(l, r)
}
final case class Modulo(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Modulo"; protected def rebuild(l: Expr, r: Expr) = Modulo(l, r)
}
final case class Power(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Power"; protected def rebuild(l: Expr, r: Expr) = Power(l, r)
}
final case class Negate(operand: Expr) extends UnaryExpr {
  val elmType = "Negate"; protected def rebuild(o: Expr) = Negate(o)
}
// Unary numeric functions. Abs preserves the operand type; Floor/Ceiling/Truncate
// return Integer (with CQL's 32-bit overflow → null); Predecessor/Successor step by the type's unit.
final case class Abs(operand: Expr) extends UnaryExpr {
  val elmType = "Abs"; protected def rebuild(o: Expr) = Abs(o)
}
final case class Floor(operand: Expr) extends UnaryExpr {
  val elmType = "Floor"; protected def rebuild(o: Expr) = Floor(o)
}
final case class Ceiling(operand: Expr) extends UnaryExpr {
  val elmType = "Ceiling"; protected def rebuild(o: Expr) = Ceiling(o)
}
final case class Truncate(operand: Expr) extends UnaryExpr {
  val elmType = "Truncate"; protected def rebuild(o: Expr) = Truncate(o)
}
final case class Predecessor(operand: Expr) extends UnaryExpr {
  val elmType = "Predecessor"; protected def rebuild(o: Expr) = Predecessor(o)
}
final case class Successor(operand: Expr) extends UnaryExpr {
  val elmType = "Successor"; protected def rebuild(o: Expr) = Successor(o)
}
// Round has an optional `precision` (decimal places, default 0), so it is n-ary, not unary.
final case class Round(operand: Expr, precision: Option[Expr]) extends NaryExpr {
  val elmType = "Round"
  def operands: Seq[Expr] = operand +: precision.toSeq
  protected def rebuild(os: Seq[Expr]) = Round(os.head, os.tail.headOption)
}
// Exponential / logarithm functions. Ln/Exp are unary; Log(x, base) is binary.
final case class Ln(operand: Expr) extends UnaryExpr {
  val elmType = "Ln"; protected def rebuild(o: Expr) = Ln(o)
}
final case class Exp(operand: Expr) extends UnaryExpr {
  val elmType = "Exp"; protected def rebuild(o: Expr) = Exp(o)
}
final case class Log(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Log"; protected def rebuild(l: Expr, r: Expr) = Log(l, r)
}
// Type bounds. `minimum <T>` / `maximum <T>` — leaves carrying the target type.
final case class MinValue(valueType: String) extends LeafExpr { val elmType = "MinValue" }
final case class MaxValue(valueType: String) extends LeafExpr { val elmType = "MaxValue" }

// ---------------------------------------------------------------------------
// Lists & intervals (LANG-LIST, LANG-INTERVAL) — modeled; full semantics later.
// ---------------------------------------------------------------------------

final case class ListExpr(elements: Seq[Expr]) extends NaryExpr {
  val elmType = "List"
  def operands: Seq[Expr] = elements
  protected def rebuild(os: Seq[Expr]) = ListExpr(os)
}

/** ELM `Interval` with open/closed boundaries (LANG-INTERVAL). Boundaries may be
  * absent (open-ended). */
final case class IntervalExpr(low: Option[Expr], high: Option[Expr],
                              lowClosed: Boolean, highClosed: Boolean) extends Expr {
  val elmType = "Interval"
  def children: Seq[IrNode] = low.toSeq ++ high.toSeq
  def withNewChildren(n: Seq[IrNode]): IrNode = {
    var i = 0
    val nl = if (low.isDefined)  { val v = Some(Expr.coerce(n(i))); i += 1; v } else None
    val nh = if (high.isDefined) { val v = Some(Expr.coerce(n(i))); i += 1; v } else None
    IntervalExpr(nl, nh, lowClosed, highClosed)
  }
}

// Interval accessors (LANG-INTERVAL). ELM carries the interval in `operand` (unary shape).
/** ELM `Start` — `start of <interval>`, the (boundary-normalized) low point. */
final case class Start(operand: Expr) extends UnaryExpr {
  val elmType = "Start"; protected def rebuild(o: Expr) = Start(o)
}
/** ELM `End` — `end of <interval>`, the (boundary-normalized) high point. */
final case class End(operand: Expr) extends UnaryExpr {
  val elmType = "End"; protected def rebuild(o: Expr) = End(o)
}
/** ELM `Width` — `width of <interval>`, `end - start` (CQL Reference). */
final case class Width(operand: Expr) extends UnaryExpr {
  val elmType = "Width"; protected def rebuild(o: Expr) = Width(o)
}

// Interval (Allen-relation) operators (LANG-INTERVAL). Binary, `operand` array of two intervals;
// each lowers through the library's boundary-aware algebra, never native Spark date/range ops.
final case class Before(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Before"; protected def rebuild(l: Expr, r: Expr) = Before(l, r)
}
final case class After(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "After"; protected def rebuild(l: Expr, r: Expr) = After(l, r)
}
/** ELM `Overlaps` — `a overlaps b` / `a overlaps <precision> of b`. `precision` (LANG-INTERVAL-PRECISION)
  * is the ELM precision NAME (`Year`…`Millisecond`) the relation compares at; BOTH operands truncate to it
  * at lowering, so operands that arrived at different precisions cannot tie into an UNCERTAIN null. Absent,
  * each operand's own per-row precision drives the comparison. A non-`Expr` attribute, preserved across
  * `rebuild` (as [[SameAs]] already does). */
final case class Overlaps(left: Expr, right: Expr, precision: Option[String] = None) extends BinaryExpr {
  val elmType = "Overlaps"; protected def rebuild(l: Expr, r: Expr) = Overlaps(l, r, precision)
}
/** ELM `Includes` — `a includes b` (every point of b is in a). */
final case class Includes(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Includes"; protected def rebuild(l: Expr, r: Expr) = Includes(l, r)
}
/** ELM `IncludedIn` — `a included in b` / interval `during [<precision> of] b` (every point of a is in b).
  * `precision` carries the named comparison precision exactly as [[Overlaps]] does
  * (LANG-INTERVAL-PRECISION) — `ValidEncounter.period during day of "Measurement Period"` is the canonical
  * case, and dropping it here is what let a day-precision period null out every boundary-day encounter. */
final case class IncludedIn(left: Expr, right: Expr, precision: Option[String] = None) extends BinaryExpr {
  val elmType = "IncludedIn"; protected def rebuild(l: Expr, r: Expr) = IncludedIn(l, r, precision)
}
final case class Starts(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Starts"; protected def rebuild(l: Expr, r: Expr) = Starts(l, r)
}
final case class Ends(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Ends"; protected def rebuild(l: Expr, r: Expr) = Ends(l, r)
}
/** ELM `SameAs` — `a same as b` / `a same <precision> as b`: precision-aware temporal equality
  * (LANG-INTERVAL). `precision` (when present) is the ELM precision name (`Year`…`Millisecond`)
  * the comparison is performed at and above; absent, it compares to the finest precision present in
  * either operand. The precision is a non-`Expr` attribute, preserved across `rebuild`. */
final case class SameAs(left: Expr, right: Expr, precision: Option[String]) extends BinaryExpr {
  val elmType = "SameAs"; protected def rebuild(l: Expr, r: Expr) = SameAs(l, r, precision)
}
/** ELM `SameOrBefore` / `SameOrAfter` — `a on or before b` / `a on or after b`: the inclusive
  * point-comparison siblings of [[Before]]/[[After]] (`<=` / `>=` precision-aware), used by CQL
  * timing phrases like `X ends on or before end of "Measurement Period"` (the translator extracts
  * the boundary so both operands arrive as points). Like Before/After these are plain binaries; an
  * ELM `precision` modifier (rare; absent from the corpus exclusion paths) is dropped at lowering as
  * Before/After already do — the operands' own per-row precision drives the comparison. LANG-INTERVAL. */
final case class SameOrBefore(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "SameOrBefore"; protected def rebuild(l: Expr, r: Expr) = SameOrBefore(l, r)
}
final case class SameOrAfter(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "SameOrAfter"; protected def rebuild(l: Expr, r: Expr) = SameOrAfter(l, r)
}

/** ELM `DateFrom` — the date portion of a Date/DateTime (drops any time-of-day), a Date at
  * Day precision (LANG-INTERVAL). */
final case class DateFrom(operand: Expr) extends UnaryExpr {
  val elmType = "DateFrom"; protected def rebuild(o: Expr) = DateFrom(o)
}

/** ELM system `ToDateTime` — convert a Date to a DateTime (the engine's temporal `struct(ts, prec)`
  * already carries both, so it is an identity on the value; the static type widens). Distinct from
  * the `FHIRHelpers.ToDateTime` FunctionRef, which converts a FHIR primitive. */
final case class ToDateTimeExpr(operand: Expr) extends UnaryExpr {
  val elmType = "ToDateTime"; protected def rebuild(o: Expr) = ToDateTimeExpr(o)
}

/** ELM `Message` — emits a runtime trace/warning and RETURNS its `source` unchanged. The
  * condition/code/severity/message are logging side-effects with no value semantics in batch
  * evaluation, so only the source is modeled (CORE-NO-SILENT-MISLOWER: the value is faithful). */
final case class Message(operand: Expr) extends UnaryExpr {
  val elmType = "Message"; protected def rebuild(o: Expr) = Message(o)
}

/** ELM `CalculateAgeAt(birthDate, asOf)` at a calendar `precision` — completed age in that unit
  * (LANG-INTERVAL). `AgeInYearsAt(x)` lowers to this with precision Year. */
final case class CalculateAgeAt(left: Expr, right: Expr, precision: Option[String]) extends BinaryExpr {
  val elmType = "CalculateAgeAt"; protected def rebuild(l: Expr, r: Expr) = CalculateAgeAt(l, r, precision)
}

final case class Exists(operand: Expr) extends UnaryExpr {
  val elmType = "Exists"; protected def rebuild(o: Expr) = Exists(o)
}
final case class SingletonFrom(operand: Expr) extends UnaryExpr {
  val elmType = "SingletonFrom"; protected def rebuild(o: Expr) = SingletonFrom(o)
}
/** ELM `ToList` (LANG-LIST): coerce a single value to a list — `null` → an empty list, otherwise a
  * one-element list. The translator inserts it where a singleton is used in a list position. */
final case class ToList(operand: Expr) extends UnaryExpr {
  val elmType = "ToList"; protected def rebuild(o: Expr) = ToList(o)
}
final case class First(operand: Expr) extends UnaryExpr {
  val elmType = "First"; protected def rebuild(o: Expr) = First(o)
}
final case class Last(operand: Expr) extends UnaryExpr {
  val elmType = "Last"; protected def rebuild(o: Expr) = Last(o)
}
/** ELM `Count` — list cardinality (ignores nulls). Source-based like First/Last. */
final case class Count(operand: Expr) extends UnaryExpr {
  val elmType = "Count"; protected def rebuild(o: Expr) = Count(o)
}
/** ELM `Indexer` — `list[index]`, 0-based per CQL (LANG-LIST); out-of-range is null. */
final case class Indexer(list: Expr, index: Expr) extends BinaryExpr {
  val elmType = "Indexer"; def left = list; def right = index
  protected def rebuild(l: Expr, r: Expr) = Indexer(l, r)
}
/** ELM `Split` — split a string into a `List<String>` on a separator. Used by the
  * QI-Core reference helpers (`Last(Split(reference.reference, '/'))` to pull a resource id out of a
  * `ResourceType/id` reference). Named ELM operands `stringToSplit`/`separator`. */
final case class Split(stringToSplit: Expr, separator: Expr) extends BinaryExpr {
  val elmType = "Split"; def left = stringToSplit; def right = separator
  protected def rebuild(l: Expr, r: Expr) = Split(l, r)
}
/** ELM `In` — list membership `element in list` (3VL: null element -> null). */
final case class In(element: Expr, list: Expr) extends BinaryExpr {
  val elmType = "In"; def left = element; def right = list
  protected def rebuild(l: Expr, r: Expr) = In(l, r)
}

/** ELM `Distinct` — `distinct X`, the STANDALONE list operator. Eliminates duplicates using EQUALITY
  * semantics, keeping the first occurrence (LANG-EQUIV); `return distinct` is the query-clause form of
  * the same rule. NOT equivalence — see `language-semantics.md` section 1.1. */
final case class Distinct(operand: Expr) extends UnaryExpr {
  val elmType = "Distinct"; protected def rebuild(o: Expr) = Distinct(o)
}

// List set operators (LANG-LIST): combine two lists, eliminating duplicates by equality.
final case class Union(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Union"; protected def rebuild(l: Expr, r: Expr) = Union(l, r)
}
final case class Intersect(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Intersect"; protected def rebuild(l: Expr, r: Expr) = Intersect(l, r)
}
final case class Except(left: Expr, right: Expr) extends BinaryExpr {
  val elmType = "Except"; protected def rebuild(l: Expr, r: Expr) = Except(l, r)
}

// ---------------------------------------------------------------------------
// Conditionals (ARCH-CONDITIONAL) — Phase 0 models them; UNION lowering is Phase 4.
// ---------------------------------------------------------------------------

final case class If(condition: Expr, thenExpr: Expr, elseExpr: Expr) extends Expr {
  val elmType = "If"
  def children: Seq[IrNode] = Seq(condition, thenExpr, elseExpr)
  def withNewChildren(n: Seq[IrNode]): IrNode =
    If(Expr.coerce(n(0)), Expr.coerce(n(1)), Expr.coerce(n(2)))
}

final case class CaseItem(when: Expr, thenExpr: Expr)
final case class Case(comparand: Option[Expr], items: Seq[CaseItem], elseExpr: Expr) extends Expr {
  val elmType = "Case"
  def children: Seq[IrNode] =
    comparand.toSeq ++ items.flatMap(i => Seq(i.when, i.thenExpr)) ++ Seq(elseExpr)
  def withNewChildren(n: Seq[IrNode]): IrNode = {
    var i = 0
    val nc = if (comparand.isDefined) { val v = Some(Expr.coerce(n(i))); i += 1; v } else None
    val nItems = items.map { _ =>
      val w = Expr.coerce(n(i)); val t = Expr.coerce(n(i + 1)); i += 2
      CaseItem(w, t)
    }
    val nElse = Expr.coerce(n(i))
    Case(nc, nItems, nElse)
  }
}

final case class Coalesce(operands: Seq[Expr]) extends NaryExpr {
  val elmType = "Coalesce"
  protected def rebuild(os: Seq[Expr]) = Coalesce(os)
}

// ---------------------------------------------------------------------------
// Query (LANG-QUERY) — source(s) bound to aliases, optional `where`/`return`.
// Phase 2 lowers the single-source case (filter/map); multi-source + with/without
// + sort are Phase 4 (ARCH-PIPELINE).
// ---------------------------------------------------------------------------

/** One query source: an `alias` bound to the elements of `expr` (a list-valued expression). */
final case class QuerySource(alias: String, expr: Expr)

/** A `sort` item (LANG-QUERY-SORT). `direction` is `"asc"`/`"desc"`. Three shapes:
  *   - `ByDirection` sorts by the element itself (`expr = None`, `path = None`);
  *   - `ByColumn` sorts by a named field of the (returned) element (`path = Some(name)`);
  *   - `ByExpression` sorts by a key expression over the element (`expr = Some(...)`). */
final case class SortItem(direction: String, expr: Option[Expr] = None, path: Option[String] = None)

/** The `aggregate` clause (LANG-QUERY-AGGREGATE): fold `body` across the (ordered) elements,
  * threading `identifier` as the accumulator seeded by `starting`. CQL's bounded recursion. */
final case class AggregateClause(identifier: String, starting: Expr, body: Expr, distinct: Boolean = false)

/** A relationship clause (LANG-QUERY-WITH): `with` (keep source rows that HAVE a related row
  * satisfying `suchThat` — a semi-join) or `without` (the negation — an anti-join). `negated` is
  * true for `without`. `expr` is the related source bound to `alias`; `suchThat` is 3VL. */
final case class RelationshipClause(alias: String, expr: Expr, suchThat: Expr, negated: Boolean)

/** One `let` binding (LANG-QUERY-LET): a named value in QUERY-ITERATION scope. `expr` is evaluated
  * per source-element row and may reference the source alias(es) and EARLIER lets in the same clause
  * (dependency-ordered). `where`/`return`/`sort`/`aggregate` reference it via [[QueryLetRef]]. It is a
  * computed column on the query's working row — NOT a define-scoped expression (which is evaluated
  * once per context, blind to the row alias). */
final case class LetClause(identifier: String, expr: Expr)

/** ELM `Query` (LANG-QUERY). `where` filters the aliased source(s); then EITHER `returnExpr`
  * shapes each element (with `returnDistinct` deduping by equivalence) OR `aggregate` folds them;
  * `sort` (re)orders the result. Multi-source and relationship clauses arrive with the relation
  * realization. */
final case class Query(
    sources: Seq[QuerySource],
    where: Option[Expr],
    returnExpr: Option[Expr],
    returnDistinct: Boolean = false,
    sort: Seq[SortItem] = Nil,
    aggregate: Option[AggregateClause] = None,
    relationships: Seq[RelationshipClause] = Nil,
    lets: Seq[LetClause] = Nil) extends Expr {
  val elmType = "Query"
  // Child order: sources, lets, where, return, sort keys, aggregate (start, body), then each
  // relationship's (expr, suchThat). Lets sit right after sources (they are query-iteration-scoped
  // and every later clause may reference them). withNewChildren rebuilds in the same order.
  def children: Seq[IrNode] =
    sources.map(_.expr) ++ lets.map(_.expr) ++ where.toSeq ++ returnExpr.toSeq ++
      sort.flatMap(_.expr) ++ aggregate.toSeq.flatMap(a => Seq(a.starting, a.body)) ++
      relationships.flatMap(r => Seq(r.expr, r.suchThat))
  def withNewChildren(n: Seq[IrNode]): IrNode = {
    var i = 0
    val newSources = sources.map { s => val v = Expr.coerce(n(i)); i += 1; s.copy(expr = v) }
    val newLets = lets.map { l => val v = Expr.coerce(n(i)); i += 1; l.copy(expr = v) }
    val nWhere  = if (where.isDefined)      { val v = Some(Expr.coerce(n(i))); i += 1; v } else None
    val nReturn = if (returnExpr.isDefined) { val v = Some(Expr.coerce(n(i))); i += 1; v } else None
    val nSort   = sort.map { s =>
      if (s.expr.isDefined) { val v = Some(Expr.coerce(n(i))); i += 1; s.copy(expr = v) } else s }
    val nAgg    = aggregate.map { a =>
      val st = Expr.coerce(n(i)); val bd = Expr.coerce(n(i + 1)); i += 2; a.copy(starting = st, body = bd) }
    val nRels   = relationships.map { r =>
      val e = Expr.coerce(n(i)); val st = Expr.coerce(n(i + 1)); i += 2; r.copy(expr = e, suchThat = st) }
    Query(newSources, nWhere, nReturn, returnDistinct, nSort, nAgg, nRels, newLets)
  }
}

// ---------------------------------------------------------------------------
// Functions (LANG-FUNCTION) — inlined in Normalize (Phase 1); modeled here.
// ---------------------------------------------------------------------------

final case class FunctionRef(name: String, libraryName: Option[String],
                             operands: Seq[Expr]) extends NaryExpr {
  val elmType = "FunctionRef"
  protected def rebuild(os: Seq[Expr]) = FunctionRef(name, libraryName, os)
}

// ---------------------------------------------------------------------------
// Type ops & navigation (LANG-CHOICE) — `as`/`is` projection; property navigation.
// ---------------------------------------------------------------------------

/** ELM `As` (LANG-CHOICE). A cast to a named type (`asType`) projects a value; a cast to a
  * `ChoiceTypeSpecifier` (`choiceTypes` non-empty — the translator wraps each heterogeneous
  * branch this way) CONSTRUCTS a choice value. The two lower differently. */
final case class As(operand: Expr, asType: String, strict: Boolean,
                    choiceTypes: Seq[String] = Nil) extends UnaryExpr {
  val elmType = "As"; protected def rebuild(o: Expr) = As(o, asType, strict, choiceTypes)
}
final case class Is(operand: Expr, isType: String) extends UnaryExpr {
  val elmType = "Is"; protected def rebuild(o: Expr) = Is(o, isType)
}

/** One element of a [[TupleExpr]]: a `name`d slot bound to `value`. */
final case class TupleElement(name: String, value: Expr)

/** ELM `Tuple` (LANG-QUERY-MULTISOURCE) — a structurally-typed record of named slots. The
  * dominant source is a multi-source query: the translator desugars `from A X, B Y` into a
  * `return` of `Tuple { X: X, Y: Y }`, so the alias tuple IS the query's element type. Lowered to
  * a Spark `struct` of the named fields (the "tuple encoding", DATA-LIST-ENCODING) — a struct per
  * row. */
final case class TupleExpr(elements: Seq[TupleElement]) extends NaryExpr {
  val elmType = "Tuple"
  def operands: Seq[Expr] = elements.map(_.value)
  protected def rebuild(os: Seq[Expr]) = TupleExpr(elements.zip(os).map { case (e, v) => e.copy(value = v) })
}

/** ELM `Instance` — construct a value of a declared `classType` from named `element` slots (a TYPED
  * tuple constructor). The dominant source is FHIRHelpers' conversions building a System value from
  * parts (`ToQuantity` → `Instance Quantity { value: …, unit: … }`, `ToRatio`, `ToCode`, …). Unlike a
  * bare [[TupleExpr]] the declared type is carried, so it types as its System type (Quantity/Ratio/…,
  * via `CqlType.parse(classType)`) and the quantity/ratio operators route on it. Lowered to the
  * matching canonical struct (e.g. Quantity's `named_struct('value', …, 'unit', …)`). */
final case class InstanceExpr(classType: String, elements: Seq[TupleElement]) extends NaryExpr {
  val elmType = "Instance"
  def operands: Seq[Expr] = elements.map(_.value)
  protected def rebuild(os: Seq[Expr]) = InstanceExpr(classType, elements.zip(os).map { case (e, v) => e.copy(value = v) })
}

/** ELM type conversion (`Convert` and the implicit `ToBoolean`/`ToInteger`/`ToLong`/
  * `ToDecimal`/`ToString`/`ToQuantity` operators the translator inserts to satisfy operator
  * signatures — e.g. `22 / 7` becomes `Divide(ToDecimal(22), ToDecimal(7))`). `toType` is the
  * CQL system type name. Lowered to a typed Spark cast (LANG-3VL: null converts to null). */
final case class Convert(operand: Expr, toType: String) extends UnaryExpr {
  val elmType = "Convert"; protected def rebuild(o: Expr) = Convert(o, toType)
}

/** ELM `ConvertQuantity` (LANG-QUANTITY) — `convert <quantity> to <unit>`: rescale a Quantity's value
  * into the target UCUM `unit` (result unit = `toUnit`). Lowered via the UCUM table (`Ucum.conversion`):
  * a compile-time factor when the source unit is static, else a runtime `CASE` over the units
  * commensurable with the target. `toUnit` is a compile-time literal (the ELM second operand). */
final case class ConvertQuantity(operand: Expr, toUnit: String) extends UnaryExpr {
  val elmType = "ConvertQuantity"; protected def rebuild(o: Expr) = ConvertQuantity(o, toUnit)
}

/** ELM `Property` navigation. `source` is the navigated expression; `scope` names a
  * query alias when navigation is rooted at a source alias rather than an expression. */
final case class Property(source: Option[Expr], scope: Option[String], path: String) extends Expr {
  val elmType = "Property"
  def children: Seq[IrNode] = source.toSeq
  def withNewChildren(n: Seq[IrNode]): IrNode =
    Property(if (source.isDefined) Some(Expr.coerce(n(0))) else None, scope, path)
}

// ---------------------------------------------------------------------------
// Terminology membership (LANG-TERMINOLOGY) — lowered to a broadcast semi-join later.
// ---------------------------------------------------------------------------

final case class InValueSet(code: Expr, valueset: Expr) extends Expr {
  val elmType = "InValueSet"
  def children: Seq[IrNode] = Seq(code, valueset)
  def withNewChildren(n: Seq[IrNode]): IrNode =
    InValueSet(Expr.coerce(n(0)), Expr.coerce(n(1)))
}

// ---------------------------------------------------------------------------
// Retrieve (DATA-RETRIEVER) — data access. Phase 2 lowers it; Phase 0 models shape.
// ---------------------------------------------------------------------------

final case class Retrieve(dataType: String,
                          templateId: Option[String],
                          codeProperty: Option[String],
                          codes: Option[Expr],
                          dateProperty: Option[String],
                          dateRange: Option[Expr],
                          /** The FHIRHelpers converter for the pushed `dateProperty` point
                            * (`"ToDateTime"`/`"ToDate"`), recorded by the date pushdown so the scan
                            * filter reconstructs an IDENTICAL predicate — and thus the SAME precision
                            * (DateTime vs Date) — to the unoptimized `where` (exact self-diff,
                            * CONF-GATE-SELFDIFF, ARCH-PUSHDOWN). `None` until a date range is folded. */
                          datePointConverter: Option[String] = None,
                          /** A COARSE, non-lossy scan PRE-FILTER for a Period-property temporal `where`
                            * (`[X] C where C.<period> during <literal interval>`), set by the
                            * `PeriodRangePushdown` rule (ARCH-PUSHDOWN, `engine-design.md` section 7
                            * step 6). `(periodPath, lowInclusive, highExclusive)` is a half-open YEAR
                            * window on the RAW ISO strings — `C.<period>.end >= lowInclusive AND
                            * C.<period>.start < highExclusive` — applied at the scan so Delta/Parquet
                            * min/max stats prune files BEFORE the precise (precision-aware) `during`
                            * runs. Lexicographic string order = chronological for zero-padded ISO, and a
                            * bare-year prefix sorts before its dated values, so partial dates (`"2023"`)
                            * are included correctly. Unlike the exact point-`dateRange` fold this KEEPS
                            * the precise `during` conjunct downstream (it is only a superset gate), so
                            * results are identical with the rule on or off (CONF-GATE-SELFDIFF). `None`
                            * until folded. */
                          periodScanFilter: Option[(String, String, String)] = None) extends Expr {
  val elmType = "Retrieve"

  /** `LANG-RETRIEVE-PROFILE`: the SPECIALISED profile canonical this retrieve constrains to, or `None`
    * when it constrains nothing beyond the resource type.
    *
    * The translator stamps a `templateId` on EVERY retrieve, so its presence alone means nothing. For a
    * plain `using FHIR` library it is the BASE `StructureDefinition/<ResourceType>` — which real data
    * never lists in `meta.profile` (that array claims SPECIALISED profiles), so treating it as a
    * constraint would drop every row. Conformance to the base definition is implied by `resourceType`.
    * A QI-Core / US-Core profile (`…/qicore-observationcancelled`, `…/us-core-bmi`) IS a constraint.
    *
    * Single source of truth for the rule: `RetrieveDemand` uses it to keep `meta` in the projection and
    * `SqlQueryLowering.filteredRetrieve` uses it to emit the predicate — they cannot disagree about
    * whether a given retrieve is profile-constrained. */
  def profileConstraint: Option[String] = {
    val local = dataType.split('}').last
    templateId.filterNot(_ == s"http://hl7.org/fhir/StructureDefinition/$local")
  }

  def children: Seq[IrNode] = codes.toSeq ++ dateRange.toSeq
  def withNewChildren(n: Seq[IrNode]): IrNode = {
    var i = 0
    val nc = if (codes.isDefined)     { val v = Some(Expr.coerce(n(i))); i += 1; v } else None
    val nd = if (dateRange.isDefined) { val v = Some(Expr.coerce(n(i))); i += 1; v } else None
    copy(codes = nc, dateRange = nd)
  }
}

// ---------------------------------------------------------------------------
// The explicit "not yet modeled" escape (CORE-NO-SILENT-MISLOWER).
// Round-trips losslessly via `rawJson`; REJECTED at Lower-time as UnsupportedNode.
// ---------------------------------------------------------------------------

/** An ELM expression type the IR does not yet model. Carries the verbatim ELM JSON so
  * Load->Emit is lossless, and the Normalize/Lower verifiers can point at exactly what
  * is unsupported. NEVER lowered to a plausible plan. */
final case class UnknownExpr(elmType: String, rawJson: String) extends LeafExpr

// ---------------------------------------------------------------------------
// Library structure
// ---------------------------------------------------------------------------

sealed trait Definition extends IrNode

/** A library statement: an `ExpressionDef` or a `FunctionDef` (or an unmodeled one). */
sealed trait Statement extends Definition

final case class UsingDef(localIdentifier: Option[String], uri: String, version: Option[String])
    extends Definition {
  def children: Seq[IrNode] = Nil
  def withNewChildren(n: Seq[IrNode]): IrNode = this
}
final case class IncludeDef(localIdentifier: String, path: String, version: Option[String])
    extends Definition {
  def children: Seq[IrNode] = Nil
  def withNewChildren(n: Seq[IrNode]): IrNode = this
}
final case class ParameterDef(name: String, parameterTypeName: Option[String],
                              default: Option[Expr]) extends Definition {
  def children: Seq[IrNode] = default.toSeq
  def withNewChildren(n: Seq[IrNode]): IrNode =
    ParameterDef(name, parameterTypeName, if (default.isDefined) Some(Expr.coerce(n(0))) else None)
}
final case class CodeSystemDef(name: String, id: String, version: Option[String])
    extends Definition {
  def children: Seq[IrNode] = Nil
  def withNewChildren(n: Seq[IrNode]): IrNode = this
}
final case class ValueSetDef(name: String, id: String, version: Option[String])
    extends Definition {
  def children: Seq[IrNode] = Nil
  def withNewChildren(n: Seq[IrNode]): IrNode = this
}
final case class CodeDef(name: String, id: String, codeSystem: Option[String])
    extends Definition {
  def children: Seq[IrNode] = Nil
  def withNewChildren(n: Seq[IrNode]): IrNode = this
}
final case class ConceptDef(name: String, codeNames: Seq[String], display: Option[String])
    extends Definition {
  def children: Seq[IrNode] = Nil
  def withNewChildren(n: Seq[IrNode]): IrNode = this
}
final case class ContextDef(name: String) extends Definition {
  def children: Seq[IrNode] = Nil
  def withNewChildren(n: Seq[IrNode]): IrNode = this
}

/** A named CQL expression (`define`). The unit of the dependency DAG; each lowers to
  * a DataFrame keyed by `__subject_id` (ARCH-SET-EVAL, DATA-DEFINE-DF). */
final case class ExpressionDef(name: String, context: Option[String], expression: Expr)
    extends Statement {
  def children: Seq[IrNode] = Seq(expression)
  def withNewChildren(n: Seq[IrNode]): IrNode =
    ExpressionDef(name, context, Expr.coerce(n(0)))
}

final case class OperandDef(name: String, operandTypeName: Option[String])

/** A user-defined function. CQL forbids recursion (LANG-FUNCTION); these are inlined
  * in Normalize, never a runtime call mechanism. */
final case class FunctionDef(name: String, context: Option[String], operands: Seq[OperandDef],
                             body: Expr, fluent: Boolean, external: Boolean) extends Statement {
  def children: Seq[IrNode] = Seq(body)
  def withNewChildren(n: Seq[IrNode]): IrNode =
    FunctionDef(name, context, operands, Expr.coerce(n(0)), fluent, external)
}

/** A statement type not yet modeled. Round-trips losslessly; rejected at Lower-time. */
final case class UnknownStatement(elmType: String, name: Option[String], rawJson: String)
    extends Statement {
  def children: Seq[IrNode] = Nil
  def withNewChildren(n: Seq[IrNode]): IrNode = this
}

/** EMIT-ROOT-INDEPENDENT — the library that declares a statement or parameter, and its name there. A linked
  * library carries one per merged name, so a define keeps its own library's identity after linking. */
final case class StatementOwner(library: String, version: String, name: String)

/** The top of the IR: a CQL/ELM library.
  *
  * `owners` (EMIT-ROOT-INDEPENDENT) maps a merged statement or parameter name to the library that declares it
  * ([[com.databricks.cql.passes.LibraryLinker]]). A name it does not list is this library's own. */
final case class Library(id: String,
                         version: Option[String],
                         usings: Seq[UsingDef],
                         includes: Seq[IncludeDef],
                         parameters: Seq[ParameterDef],
                         codeSystems: Seq[CodeSystemDef],
                         valueSets: Seq[ValueSetDef],
                         codes: Seq[CodeDef],
                         concepts: Seq[ConceptDef],
                         contexts: Seq[ContextDef],
                         statements: Seq[Statement],
                         owners: Map[String, StatementOwner] = Map.empty) extends IrNode {
  def children: Seq[IrNode] =
    usings ++ includes ++ parameters ++ codeSystems ++ valueSets ++ codes ++ concepts ++
      contexts ++ statements

  /** EMIT-ROOT-INDEPENDENT — the library that declares `name`, and its name there. */
  def ownerOf(name: String): StatementOwner = owners.getOrElse(name, StatementOwner(id, version.getOrElse(""), name))

  def withNewChildren(n: Seq[IrNode]): IrNode = Library(
    id, version,
    usings      = n.collect { case x: UsingDef      => x },
    includes    = n.collect { case x: IncludeDef    => x },
    parameters  = n.collect { case x: ParameterDef  => x },
    codeSystems = n.collect { case x: CodeSystemDef => x },
    valueSets   = n.collect { case x: ValueSetDef   => x },
    codes       = n.collect { case x: CodeDef       => x },
    concepts    = n.collect { case x: ConceptDef    => x },
    contexts    = n.collect { case x: ContextDef    => x },
    statements  = n.collect { case x: Statement     => x },
    owners      = owners
  )
}
