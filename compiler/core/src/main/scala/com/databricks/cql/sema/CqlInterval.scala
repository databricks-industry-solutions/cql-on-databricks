package com.databricks.cql.sema

/** CQL interval (Allen-relation) algebra over DISCRETE integer points (LANG-INTERVAL, `01`
  * section 3).
  *
  * This is the pure, plan-independent source of truth (TECH-SEMANTIC-LIB), the sibling of [[Bool3]]: the
  * unit of per-operator conformance testing (Gate 1, CONF-GATE-OPERATOR) and the auditable home
  * of the boundary rules that [[com.databricks.cql.lower.ColumnLowering]] must match on
  * Spark columns. Results are [[Bool3]] because a relation is null when a needed boundary is null
  * (3VL is pervasive — `01` section 1).
  *
  * **Boundary handling.** Intervals are typed and may be open/closed at either end. For a DISCRETE
  * point type (Integer/Long) CQL normalizes an open boundary to its closed neighbour — `Interval(1,
  * 10)` ≡ `Interval[2, 9]` — because every point has a predecessor/successor. Normalizing to a
  * closed `[lo, hi]` once (in [[closed]]) collapses the open/closed cross-product so every relation
  * is a plain point comparison, exactly as the maintained Java reference evaluates integer
  * intervals. Continuous point types (Decimal/DateTime), which have no successor and need
  * strict-vs-non-strict boundary comparisons, are a later refinement (ARCH-SEAMS) and are rejected
  * loudly at lowering until built (CORE-NO-SILENT-MISLOWER).
  */
object CqlInterval {

  /** A normalized CLOSED interval over `Long` points. `None` on a boundary is a NULL point
    * (distinct from an absent/unbounded boundary), which makes dependent relations null (3VL). */
  final case class Closed(low: Option[Long], high: Option[Long])

  /** Normalize a raw open/closed integer interval to closed form via discrete predecessor /
    * successor: an open low becomes `low + 1`, an open high becomes `high - 1`. */
  def closed(low: Option[Long], high: Option[Long],
             lowClosed: Boolean, highClosed: Boolean): Closed =
    Closed(low.map(l => if (lowClosed) l else l + 1),
           high.map(h => if (highClosed) h else h - 1))

  // -- Point accessors (null boundary → null result). --------------------------------------

  /** `start of` — the (normalized) low point. */
  def start(i: Closed): Option[Long] = i.low
  /** `end of` — the (normalized) high point. */
  def end(i: Closed): Option[Long] = i.high
  /** `width of` — `end - start` (CQL Reference: the difference of the boundaries). */
  def width(i: Closed): Option[Long] = for (l <- i.low; h <- i.high) yield h - l

  // -- Relations (all 3VL: any null boundary the relation reads → Null). --------------------

  private def lift(xs: Option[Long]*)(f: => Boolean): Bool3 =
    if (xs.exists(_.isEmpty)) Bool3.Null else Bool3.fromBoolean(f)

  /** `a before b` — a ends strictly before b begins. */
  def before(a: Closed, b: Closed): Bool3 = lift(a.high, b.low)(a.high.get < b.low.get)
  /** `a after b` — a begins strictly after b ends. */
  def after(a: Closed, b: Closed): Bool3 = lift(a.low, b.high)(a.low.get > b.high.get)

  /** `a overlaps b` — they share at least one point. */
  def overlaps(a: Closed, b: Closed): Bool3 =
    lift(a.low, a.high, b.low, b.high)(a.low.get <= b.high.get && b.low.get <= a.high.get)

  /** `a includes b` — every point of b is in a. */
  def includes(a: Closed, b: Closed): Bool3 =
    lift(a.low, a.high, b.low, b.high)(a.low.get <= b.low.get && b.high.get <= a.high.get)
  /** `a included in b` — every point of a is in b (the converse of [[includes]]). */
  def includedIn(a: Closed, b: Closed): Bool3 = includes(b, a)

  /** `a starts b` — a shares b's start and ends no later. */
  def starts(a: Closed, b: Closed): Bool3 =
    lift(a.low, a.high, b.low, b.high)(a.low.get == b.low.get && a.high.get <= b.high.get)
  /** `a ends b` — a shares b's end and starts no earlier. */
  def ends(a: Closed, b: Closed): Bool3 =
    lift(a.low, a.high, b.low, b.high)(a.high.get == b.high.get && a.low.get >= b.low.get)

  /** `point during/in interval` — the point lies within the closed interval. */
  def contains(i: Closed, point: Option[Long]): Bool3 =
    lift(i.low, i.high, point)(i.low.get <= point.get && point.get <= i.high.get)
}
