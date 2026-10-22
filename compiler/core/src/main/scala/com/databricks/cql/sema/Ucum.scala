package com.databricks.cql.sema

/** UCUM quantity unit conversion (LANG-QUANTITY, `01` section 4).
  *
  * CQL quantities carry a unit; arithmetic and comparison across units (`1 'kg' = 1000 'g'`)
  * require UCUM conversion. Spark has no quantity type, so a quantity lowers to a `(value, unit)`
  * struct and cross-unit operators normalize the values to a common base before comparing/adding.
  *
  * Per `04` section 3 (TECH-REUSE) the decision is *reuse a UCUM library if it integrates
  * cleanly, else reimplement the paths we need*. The conversion is resolved at PLAN-BUILD time
  * (units are statically known from the `CqlType`), so the lowered plan multiplies by a literal
  * factor — staying native/built-in and Photon-eligible (TECH-SEMANTIC-LIB), never a per-row UDF
  * call into a terminology/UCUM service. This is the reimplemented core: the metric-prefixed
  * families quality measures actually use, where every factor is an EXACT decimal so the engine cannot
  * diverge from the reference through float error.
  *
  * Two units are *commensurable* iff they share a [[Dimension]]; the factor is each unit's ratio
  * to its dimension's canonical base. A unit we do not model returns `None` — the caller fails
  * loudly (CORE-NO-SILENT-MISLOWER) rather than guessing a conversion.
  */
object Ucum {

  /** A physical dimension; units convert only within one. The `base` is the canonical unit the
    * factors are expressed against. */
  final case class Dimension(name: String, base: String)

  private val Mass        = Dimension("mass", "g")
  private val Length      = Dimension("length", "m")
  private val Time        = Dimension("time", "s")
  private val Volume      = Dimension("volume", "L")
  private val Substance   = Dimension("substance", "mol")
  private val Unity       = Dimension("unity", "1")

  /** Each known unit → (its dimension, its EXACT factor to the dimension base). UCUM annotation
    * units (`'1'`, `'%'`) and the metric-prefixed families cover the corpus and common quality-measure use;
    * extend per requirement (PROC-CITE-REQ) — never approximate a missing factor. */
  private val units: Map[String, (Dimension, BigDecimal)] = Map(
    // mass (base gram)
    "g"   -> (Mass, BigDecimal(1)),
    "mg"  -> (Mass, BigDecimal("0.001")),
    "ug"  -> (Mass, BigDecimal("0.000001")),
    "ng"  -> (Mass, BigDecimal("0.000000001")),
    "kg"  -> (Mass, BigDecimal(1000)),
    // length (base metre)
    "m"   -> (Length, BigDecimal(1)),
    "cm"  -> (Length, BigDecimal("0.01")),
    "mm"  -> (Length, BigDecimal("0.001")),
    "km"  -> (Length, BigDecimal(1000)),
    // time (base second); calendar factors are the UCUM definitional values
    "s"   -> (Time, BigDecimal(1)),
    "min" -> (Time, BigDecimal(60)),
    "h"   -> (Time, BigDecimal(3600)),
    "d"   -> (Time, BigDecimal(86400)),
    "wk"  -> (Time, BigDecimal(604800)),
    // volume (base litre)
    "L"   -> (Volume, BigDecimal(1)),
    "dL"  -> (Volume, BigDecimal("0.1")),
    "mL"  -> (Volume, BigDecimal("0.001")),
    // substance (base mole)
    "mol"  -> (Substance, BigDecimal(1)),
    "mmol" -> (Substance, BigDecimal("0.001")),
    "umol" -> (Substance, BigDecimal("0.000001")),
    // dimensionless
    "1"   -> (Unity, BigDecimal(1)),
    "%"   -> (Unity, BigDecimal("0.01"))
  )

  /** Is `unit` one the engine can convert? An unknown unit is not an error here — the caller
    * decides (a same-unit op needs no table entry). */
  def isKnown(unit: String): Boolean = units.contains(unit)

  /** The canonical-base factor of a unit, or `None` if not modeled. */
  def factor(unit: String): Option[BigDecimal] = units.get(unit).map(_._2)

  /** Every modeled unit commensurable with `unit` (same dimension), including `unit` itself when
    * modeled — the enumeration a runtime unit conversion needs to build a `CASE` over the possible
    * source units (`ConvertQuantity` on a dynamic-unit quantity). Empty if `unit` is unmodeled.
    * Deterministically ordered (TECH-DETERMINISM). */
  def commensurableUnits(unit: String): Seq[String] =
    units.get(unit).map(_._1) match {
      case Some(d) => units.iterator.collect { case (u, (d2, _)) if d2 == d => u }.toSeq.sorted
      case None    => Nil
    }

  /** Are two units commensurable (same dimension, hence convertible)? Identical units always are
    * (even unmodeled ones — a same-unit op never needs the table). */
  def commensurable(from: String, to: String): Boolean =
    from == to || ((units.get(from), units.get(to)) match {
      case (Some((d1, _)), Some((d2, _))) => d1 == d2
      case _                              => false
    })

  /** The multiplicative factor to convert a value in `from` to `to`
    * (`value_to = value_from * conversion(from, to)`), or `None` if incommensurable / unmodeled.
    * Identity for equal units, so an unmodeled unit still compares/equals itself. */
  def conversion(from: String, to: String): Option[BigDecimal] =
    if (from == to) Some(BigDecimal(1))
    else (units.get(from), units.get(to)) match {
      case (Some((d1, f1)), Some((d2, f2))) if d1 == d2 => Some(f1 / f2)
      case _                                            => None
    }

  /** LANG-AGGREGATE-FUNCTIONS: the unit of a quantity multiplied by itself, spelled as the engine's quantity
    * `Multiply` spells it (`cm` -> `cm2`). The dimensionless `'1'` stays `'1'`. */
  def squared(unit: String): String = if (unit == "1") "1" else unit + "2"

  /** LANG-AGGREGATE-FUNCTIONS: every modeled unit with its factor, for the runtime choice of the coarsest unit in
    * a list of quantities whose units are only known per row. Deterministically ordered (TECH-DETERMINISM). */
  def factors: Seq[(String, BigDecimal)] = units.toSeq.map { case (u, (_, f)) => u -> f }.sortBy(_._1)

  /** LANG-AGGREGATE-FUNCTIONS: `(from, to, factor)` for every pair of commensurable modeled units where `to` is
    * the coarser, so the factor is at most 1: the conversions a runtime-unit aggregate needs to bring each element
    * to the list's coarsest unit. A non-terminating factor (`min` to `wk`) is rounded half-up at `scale` places. */
  def toCoarserFactors(scale: Int): Seq[(String, String, BigDecimal)] =
    (for {
      (from, (d1, f1)) <- units.toSeq
      (to, (d2, f2))   <- units.toSeq
      if from != to && d1 == d2 && f1 < f2
    } yield (from, to, (f1 / f2).setScale(scale, BigDecimal.RoundingMode.HALF_UP))).sortBy(t => (t._1, t._2))

  // --- CQL calendar duration units (LANG-QUANTITY, temporal-duration comparison) -------------
  //
  // CQL duration KEYWORDS (`year`…`millisecond` and plurals) and their UCUM symbols compare under
  // bespoke rules (CQL Reference, "Comparing Quantities with Time-Valued Units"), distinct from the
  // dimensional UCUM table above:
  //   - PRECISE units (week and finer) have an EXACT millisecond factor and are fully convertible
  //     for both `=` and `~` (`1 day = 24 hours`, `1 second = 1 's'`).
  //   - IMPRECISE units (`year`, `month`) are nominal. Under `=`/`!=` a calendar keyword is NOT
  //     comparable to its UCUM symbol or a different unit → null (`1 year = 1 'a'` → null), but the
  //     same keyword compares by count (`1 years = 1 year` → true). Under `~` they ARE equivalent
  //     via the nominal relations `1 year ~ 12 months ~ 365 days`, `1 month ~ 30 days`,
  //     `1 year ~ 1 'a'`, `1 month ~ 1 'mo'` — pairwise, not a single linear factor (12·30 ≠ 365).

  private val MsPerDay = BigDecimal(86400000)
  private val durationPreciseMsTable: Map[String, BigDecimal] = Map(
    "millisecond" -> BigDecimal(1),       "milliseconds" -> BigDecimal(1),       "ms"  -> BigDecimal(1),
    "second"      -> BigDecimal(1000),    "seconds"      -> BigDecimal(1000),    "s"   -> BigDecimal(1000),
    "minute"      -> BigDecimal(60000),   "minutes"      -> BigDecimal(60000),   "min" -> BigDecimal(60000),
    "hour"        -> BigDecimal(3600000), "hours"        -> BigDecimal(3600000), "h"   -> BigDecimal(3600000),
    "day"         -> MsPerDay,            "days"         -> MsPerDay,            "d"   -> MsPerDay,
    "week"        -> BigDecimal(604800000), "weeks"      -> BigDecimal(604800000), "wk" -> BigDecimal(604800000))

  // (canonical field, isCalendarKeyword) — the keyword/symbol distinction matters only for `=`.
  private val durationImpreciseTable: Map[String, (String, Boolean)] = Map(
    "year"  -> ("year", true),  "years"  -> ("year", true),  "a"  -> ("year", false),
    "month" -> ("month", true), "months" -> ("month", true), "mo" -> ("month", false))

  /** The exact millisecond factor of a PRECISE duration unit (`None` for imprecise / non-duration). */
  def durationPreciseMs(unit: String): Option[BigDecimal] = durationPreciseMsTable.get(unit)

  /** An IMPRECISE duration unit's `(field, isCalendarKeyword)` (`None` for precise / non-duration). */
  def durationImprecise(unit: String): Option[(String, Boolean)] = durationImpreciseTable.get(unit)

  /** Is `unit` any CQL duration unit (precise or imprecise, keyword or UCUM symbol)? */
  def isDurationUnit(unit: String): Boolean =
    durationPreciseMsTable.contains(unit) || durationImpreciseTable.contains(unit)
}
