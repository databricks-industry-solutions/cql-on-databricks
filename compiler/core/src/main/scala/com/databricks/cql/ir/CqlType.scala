package com.databricks.cql.ir

/** A resolved CQL type — the synthesized "type" attribute computed by the Type/Encoding
  * step (ARCH-TYPE-ATTR, `engine-design.md` section 2.1).
  *
  * Types are a RECOMPUTABLE attribute, not state stored on the IR: `TypeEncoding.typeOf`
  * derives a node's type bottom-up on demand, so it survives every Normalize rewrite for
  * free (the Catalyst `dataType` model the IR already mirrors, ARCH-IR). This ADT is the
  * lattice that computation ranges over.
  *
  * Breadth is laid down now so later phases ADD operators, not type kinds: temporal types
  * carry a precision and quantities carry a unit (the load-bearing inputs for Phase 3's
  * interval/quantity algebra, LANG-INTERVAL/LANG-QUANTITY), even though their lowering is
  * not built yet (ARCH-SEAMS). `Choice` is deliberately deferred to Phase 4 (LANG-CHOICE).
  */
sealed trait CqlType {
  /** A short, stable rendering for diagnostics and verifier messages. */
  def show: String
}

object CqlType {

  // -- CQL System scalars -----------------------------------------------------
  case object BooleanType extends CqlType { val show = "Boolean" }
  case object IntegerType extends CqlType { val show = "Integer" }
  case object LongType    extends CqlType { val show = "Long" }
  case object DecimalType extends CqlType { val show = "Decimal" }
  case object StringType  extends CqlType { val show = "String" }

  /** CQL date/time precision (LANG-INTERVAL): comparisons and `same … as` are precision-aware.
    * `None` precision means "unspecified". The `rank` is the significance order (coarser = smaller)
    * used by precision-aware comparison: two values are compared down to the lesser rank, and a tie
    * there with differing ranks is undecidable (null). */
  sealed abstract class DateTimePrecision(val rank: Int)
  object DateTimePrecision {
    case object Year        extends DateTimePrecision(0)
    case object Month       extends DateTimePrecision(1)
    case object Day         extends DateTimePrecision(2)
    case object Hour        extends DateTimePrecision(3)
    case object Minute      extends DateTimePrecision(4)
    case object Second      extends DateTimePrecision(5)
    case object Millisecond extends DateTimePrecision(6)

    /** Significance order; index = [[rank]]. The first `n` are the precision of a Date/DateTime
      * literal carrying `n` components (year-only → Year, …). */
    val ordered: Seq[DateTimePrecision] = Seq(Year, Month, Day, Hour, Minute, Second, Millisecond)

    /** The precision a literal with `n` components carries (clamped to the finest). */
    def ofComponentCount(n: Int): DateTimePrecision = ordered(math.max(0, math.min(n - 1, ordered.size - 1)))

    /** LANG-INTERVAL-PRECISION: resolve an ELM precision NAME (`"Day"`, `"Millisecond"`, …) to its
      * precision. Case-insensitive. `None` for an absent/unknown name — an unnamed relation compares at
      * the operands' own per-row precisions, which is the pre-existing behaviour. */
    def byName(name: String): Option[DateTimePrecision] =
      ordered.find(_.toString.equalsIgnoreCase(name))
  }

  // -- Temporal (precision-aware, Phase 3) ------------------------------------
  final case class DateType(precision: Option[DateTimePrecision] = None) extends CqlType {
    def show = "Date"
  }
  final case class DateTimeType(precision: Option[DateTimePrecision] = None) extends CqlType {
    def show = "DateTime"
  }
  final case class TimeType(precision: Option[DateTimePrecision] = None) extends CqlType {
    def show = "Time"
  }
  /** A CQL `Ratio` — a numerator:denominator pair of quantities (LANG-QUANTITY). */
  case object RatioType extends CqlType { val show = "Ratio" }

  /** A UCUM quantity (LANG-QUANTITY). `unit` is `None` when unspecified; unit-aware
    * comparison/arithmetic (cross-unit normalization) arrives in Phase 3. */
  final case class QuantityType(unit: Option[String] = None) extends CqlType {
    def show = unit.fold("Quantity")(u => s"Quantity('$u')")
  }

  // -- Composite --------------------------------------------------------------
  final case class IntervalType(pointType: CqlType) extends CqlType {
    def show = s"Interval<${pointType.show}>"
  }
  final case class ListType(elementType: CqlType) extends CqlType {
    def show = s"List<${elementType.show}>"
  }

  /** A `Choice<T1, T2, …>` — a value of one of several statically-known types (LANG-CHOICE).
    * Physically a discriminated struct (`__choice` + a slot per member). Members are kept in
    * declaration order. */
  final case class ChoiceType(members: Seq[CqlType]) extends CqlType {
    def show = members.map(_.show).mkString("Choice<", ", ", ">")
  }

  /** A `Tuple{ name: T, … }` — a structurally-typed record (LANG-QUERY-MULTISOURCE). The
    * element type of a multi-source query (the alias tuple) and any authored `Tuple{…}`.
    * Physically a Spark struct of the named slots. Slots are kept in declaration order. */
  final case class TupleType(elements: Seq[(String, CqlType)]) extends CqlType {
    def show = elements.map { case (n, t) => s"$n: ${t.show}" }.mkString("Tuple{", ", ", "}")
  }

  /** A named model/terminology type not refined into a system type — a FHIR resource
    * (`Condition`, `Encounter`) or a terminology handle (`ValueSet`, `Code`). Precise FHIR
    * structure typing arrives with model info in later phases (ARCH-SEAMS). */
  final case class NamedType(name: String) extends CqlType { def show = name }

  /** Top of the lattice: a type not yet refined — the null literal (typeless in CQL), or a
    * model-dependent navigation whose precise type needs information a later phase supplies.
    * NOT an error in itself: the loud failure is a concrete operator that REQUIRES a
    * refinement (e.g. an interval point type) meeting `AnyType` (introduced in Phase 3).
    * Acts as the identity under [[join]] so a null branch takes its sibling's type. */
  case object AnyType extends CqlType { val show = "Any" }

  private val numericRank: Map[CqlType, Int] =
    Map(IntegerType -> 1, LongType -> 2, DecimalType -> 3)

  /** Parse a CQL/ELM type name into a `CqlType`. Accepts the qualified ELM forms
    * (`{urn:hl7-org:elm-types:r1}Integer`, `System.Integer`, `FHIR.Condition`) and bare
    * names. Unknown names become a [[NamedType]] (never silently dropped). */
  def parse(typeName: String): CqlType = {
    val t = typeName.trim
    // Composite specifiers render as `Interval<point>` / `List<element>` (ElmCodec.typeSpecName);
    // the point/element may still carry an ELM brace prefix, so recurse on the raw inner text BEFORE
    // the brace strip (which would otherwise split inside the `<…>`).
    if (t.startsWith("Interval<") && t.endsWith(">"))
      return IntervalType(parse(t.substring("Interval<".length, t.length - 1)))
    if (t.startsWith("List<") && t.endsWith(">"))
      return ListType(parse(t.substring("List<".length, t.length - 1)))
    val afterBrace = t.substring(t.lastIndexOf('}') + 1)
    val bare       = afterBrace.substring(afterBrace.lastIndexOf('.') + 1)
    bare match {
      case "Boolean"  => BooleanType
      case "Integer"  => IntegerType
      case "Long"     => LongType
      case "Decimal"  => DecimalType
      case "String"   => StringType
      case "Date"     => DateType()
      case "DateTime" => DateTimeType()
      case "Time"     => TimeType()
      case "Quantity" => QuantityType()
      case "Ratio"    => RatioType
      case other      => NamedType(other)
    }
  }

  /** A conservative least-upper-bound used to unify branch/coalesce/element types.
    * `AnyType` is the identity (a null branch takes its sibling's type); numerics widen
    * (Integer < Long < Decimal); equal types unify; quantities in different units unify to a quantity
    * of unknown unit, and temporals of one kind at different precisions to that kind of unknown precision;
    * lists/intervals unify pointwise;
    * anything else falls to `AnyType` rather than guessing. */
  def join(a: CqlType, b: CqlType): CqlType = (a, b) match {
    case _ if a == b                                => a
    case (AnyType, x)                               => x
    case (x, AnyType)                               => x
    case (x, y) if numericRank.contains(x) && numericRank.contains(y) =>
      if (numericRank(x) >= numericRank(y)) x else y
    // Quantities in different units are still quantities; the unit is then known only per row (LANG-QUANTITY).
    // Likewise a date, date-time or time at different precisions, whose precision is then per row (LANG-INTERVAL).
    case (QuantityType(_), QuantityType(_))         => QuantityType(None)
    case (DateType(_), DateType(_))                 => DateType(None)
    case (DateTimeType(_), DateTimeType(_))         => DateTimeType(None)
    case (TimeType(_), TimeType(_))                 => TimeType(None)
    case (ListType(x), ListType(y))                 => ListType(join(x, y))
    case (IntervalType(x), IntervalType(y))         => IntervalType(join(x, y))
    case _                                          => AnyType
  }
}
