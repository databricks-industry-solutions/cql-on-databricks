package com.databricks.cql.runtime.eval

import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/** The struct a `cql_to_interval` operator returns — `{low, high}` canonical-ISO endpoints. A top-level
  * case class so `functions.udf` (in [[com.databricks.cql.runtime.udf.CqlRuntimeUdfs]]) derives
  * the exact `struct<low:string,high:string>` return schema the downstream `.low`/`.high` navigation
  * reads (matching the transpiler's native `named_struct('low', …, 'high', …)`). */
final case class CqlInterval(low: String, high: String)

/** The struct a `cql_convert_quantity` operator returns — a System.Quantity `{value, unit}`. A top-level
  * case class so `functions.udf` derives the `struct<value:decimal(38,18),unit:string>` return schema the
  * downstream `.value`/`.unit` navigation reads (matching the transpiler's native
  * `named_struct('value', …, 'unit', …)`; a `.value` consumer re-casts to `decimal(28,8)` where it needs
  * the tight step precision). */
final case class CqlQuantity(value: java.math.BigDecimal, unit: String)

/** EVAL-OPS-CORE (compiler/docs/execution-model.md) — the SEMANTIC CORE. CQL operator semantics live HERE,
  * once, over host-neutral representations (canonical-ISO temporal strings, the `CqlQuantity` case class for
  * a Quantity struct, boxed scalars for three-valued nullability). Two callers share this one source of
  * truth: the granular catalog UDFs ([[com.databricks.cql.runtime.udf.CqlRuntimeUdfs]]) wrap these
  * functions (`cql_temporal_cmp = udf(CqlOps.temporalCmp _)`), and — should the fallback ever be built — the
  * coarse per-define ELM tree-walker (`EVAL-DEFINE-EVALUATOR`) calls the SAME functions. Neither
  * re-implements semantics: granularity is a dial, not a fork (correctness preserved by construction).
  *
  * ==Spark-free by construction — UC-hostable (`ARCH-PURITY`, EXEC-UC-STRUCT-ARG)==
  * This object references NO `org.apache.spark.*` type: every operator takes/returns scalars, canonical-ISO
  * strings, `scala.collection.immutable.Map`, and the case classes `CqlInterval`/`CqlQuantity`. So besides
  * the OSS `spark.udf.register` path, each method is directly a **Unity Catalog Scala UDF handler**
  * (`HANDLER 'com.databricks.cql.runtime.eval.CqlOps.<method>'`) — it runs in the sessionless UC
  * sandbox unchanged (choice navigation is lowered NATIVELY, so no `Row`-navigation op lives here anymore).
  * It imports NOTHING from the compiler; being a Scala `object`, a UDF lambda that calls `CqlOps.temporalCmp`
  * references this shipped singleton, not a captured compiler class (`UdfFeasibilitySpec`).
  *
  * ==Correctness by 1:1 port + self-diff (EXEC-SELFDIFF)==
  * The temporal functions reproduce `SqlColumnLowering`'s SQL semantics EXACTLY — the ISO-string precision
  * model (precision = string length), the three-valued comparison, the `make_interval` calendar step at an
  * operand's own precision. `SqlColumnLowering` is the source of truth; drift is caught mechanically by
  * `TemporalOpsSpec` (per-op, Spark-free) plus the corpus SQL differential.
  *
  * ==The canonical-ISO / precision model (mirrors `SqlColumnLowering`)==
  * A temporal value is a canonical ISO string whose LENGTH encodes precision — ranks 0..6 =
  * Year(4)/Month(7)/Day(10)/Hour(13)/Minute(16)/Second(19)/Millisecond(23) (`PrecLen`). A comparison aligns
  * both operands to their common (shorter) precision; if the aligned prefixes are equal but the precisions
  * differ, the order is UNCERTAIN → `null` (CQL three-valued semantics). Arithmetic pads the string to a
  * full timestamp, steps in the proleptic-Gregorian calendar, reformats, and truncates back to the
  * operand's own precision (`parse`/`stepRank`/`trunc`). */
object CqlOps {

  // ============================================================ temporal operators (mirror SqlColumnLowering)

  // Precision-length ladder (SqlColumnLowering.PrecLen): rank 0..6 -> canonical ISO length.
  private val PrecLen = Array(4, 7, 10, 13, 16, 19, 23)
  private def lenOfRank(r: Int): Int = if (r >= 6) 23 else PrecLen(r)
  // rankOfLen (SqlColumnLowering.rankOfLen): len<=4->0, <=7->1, <=10->2, <=13->3, <=16->4, <=19->5, else 6.
  private def rankOfLen(len: Int): Int =
    if (len <= 4) 0 else if (len <= 7) 1 else if (len <= 10) 2 else if (len <= 13) 3
    else if (len <= 16) 4 else if (len <= 19) 5 else 6

  private val FullTemplate = "0000-01-01T00:00:00.000" // SqlColumnLowering.FullTemplate
  // Format back to the canonical ISO string (mirrors fmtTrunc's date_format + ' '->'T'). Proleptic-year
  // patterns agree byte-for-byte with Spark's date_format for the real clinical date range (years 1..9999).
  private val Fmt = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS")

  // -------------------------------------------------------------------- comparison (cmpPoints + temporalCompare)

  /** `cql_temporal_cmp(a, aInf, b, bInf, op)` — precision-aware temporal comparison with per-operand
    * infinity handling (the SQL twin of `cmpPoints(dateMode = true)` wrapping `temporalCompare`). `op` is
    * `lt`/`gt`/`le`/`ge`/`eq`/`ne`/`equiv`. `aInf`/`bInf` encode an interval bound's open-end infinity: `0`
    * = finite operand (a scalar compare, or a foldable literal — no infinity substitution, a genuine `null`
    * propagates three-valued); `-1` = a null operand is −infinity (an absent LOW bound); `+1` = +infinity
    * (an absent HIGH bound). Returns the boxed three-valued result (`null` = uncertain). */
  def temporalCmp(a: String, aInf: Int, b: String, bInf: Int, op: String): java.lang.Boolean = {
    def decide(pa: Int, pb: Int): java.lang.Boolean = java.lang.Boolean.valueOf(op match {
      case "lt"          => pa < pb
      case "le"          => pa <= pb
      case "gt"          => pa > pb
      case "ge"          => pa >= pb
      case "eq" | "equiv" => pa == pb
      case "ne"          => pa != pb
      case _             => throw new IllegalArgumentException(s"cql_temporal_cmp: unknown op '$op'")
    })
    val aTrack = aInf != 0
    val bTrack = bInf != 0
    val aNull  = a == null
    val bNull  = b == null
    if (aTrack && bTrack) {
      if (aNull && bNull) decide(aInf, bInf)
      else if (aNull)     decide(aInf, 0)
      else if (bNull)     decide(0, bInf)
      else                cmpCore(a, b, op)
    } else if (aTrack) {
      if (aNull) decide(aInf, 0) else cmpCore(a, b, op)
    } else if (bTrack) {
      if (bNull) decide(0, bInf) else cmpCore(a, b, op)
    } else cmpCore(a, b, op)
  }

  /** `temporalCompare` core: align both operands to the common (shorter) precision, compare prefixes, and
    * return `null` when prefixes tie at DIFFERENT precisions (uncertain). `equiv` is null-safe (both null
    * = true, one null = false); every other op is `null` when either operand is null. */
  private def cmpCore(a: String, b: String, op: String): java.lang.Boolean = op match {
    case "equiv" =>
      if (a == null && b == null) return java.lang.Boolean.TRUE
      if (a == null || b == null) return java.lang.Boolean.FALSE
      val l = math.min(a.length, b.length)
      java.lang.Boolean.valueOf(a.regionMatches(0, b, 0, l))
    case "eq" =>
      if (a == null || b == null) return null
      val l = math.min(a.length, b.length)
      if (a.regionMatches(0, b, 0, l)) (if (a.length == b.length) java.lang.Boolean.TRUE else null)
      else java.lang.Boolean.FALSE
    case "ne" =>
      if (a == null || b == null) return null
      val l = math.min(a.length, b.length)
      if (a.regionMatches(0, b, 0, l)) (if (a.length == b.length) java.lang.Boolean.FALSE else null)
      else java.lang.Boolean.TRUE
    case "lt" | "le" | "gt" | "ge" =>
      if (a == null || b == null) return null
      val l = math.min(a.length, b.length)
      val c = a.substring(0, l).compareTo(b.substring(0, l))
      val tie = op == "le" || op == "ge" // tie(true) for Le/Ge, tie(false) for Lt/Gt
      val lessWins = op == "lt" || op == "le"
      if (c < 0) java.lang.Boolean.valueOf(lessWins)
      else if (c > 0) java.lang.Boolean.valueOf(!lessWins)
      else if (a.length == b.length) java.lang.Boolean.valueOf(tie)
      else null
    case _ => throw new IllegalArgumentException(s"cql_temporal_cmp: unknown op '$op'")
  }

  /** `cql_temporal_same_prec(a, b, precLen)` — `same as` at an explicit precision: equal iff both reach
    * `precLen` chars and their `precLen`-prefixes match; `null` if either is null or shorter than
    * `precLen`. Mirrors `SqlColumnLowering.sameAs`'s explicit-precision branch. */
  def temporalSamePrec(a: String, b: String, precLen: Int): java.lang.Boolean = {
    if (a == null || b == null) return null
    if (a.length < precLen || b.length < precLen) return null
    java.lang.Boolean.valueOf(a.substring(0, precLen) == b.substring(0, precLen))
  }

  // -------------------------------------------------------------------- arithmetic (tsOf + addUnit + fmtTrunc)

  /** Pad a canonical ISO string to a full timestamp and parse (mirrors `tsOf`: `concat(s, template-tail)`
    * then cast `TIMESTAMP_NTZ`). A `> 23`-char input (a timezone suffix) is capped at millisecond width,
    * matching the operands that reach these ops (value[x] normalization already truncates to 23). */
  private def parse(s: String): LocalDateTime = {
    val full =
      if (s.length >= FullTemplate.length) s.substring(0, FullTemplate.length)
      else s + FullTemplate.substring(s.length)
    LocalDateTime.parse(full, Fmt)
  }

  /** Reformat a stepped timestamp back to the canonical ISO string, truncated to `len` (mirrors `fmtTrunc`
    * — `date_format` yields exactly 23 chars, then `substr(…, 1, len)`). */
  private def trunc(ldt: LocalDateTime, len: Int): String = {
    val f = Fmt.format(ldt)
    f.substring(0, math.min(len, f.length))
  }

  /** Add (or subtract) ONE calendar unit at precision `rank` (mirrors `addUnit`'s `make_interval` step). */
  private def stepRank(ldt: LocalDateTime, rank: Int, minus: Boolean): LocalDateTime = {
    val n = if (minus) -1L else 1L
    rank match {
      case 0 => ldt.plusYears(n)
      case 1 => ldt.plusMonths(n)
      case 2 => ldt.plusDays(n)
      case 3 => ldt.plusHours(n)
      case 4 => ldt.plusMinutes(n)
      case 5 => ldt.plusSeconds(n)
      case _ => ldt.plusNanos(n * 1000000L) // rank >= 6 = millisecond
    }
  }

  /** `cql_temporal_step(s, minus)` — the `successor`/`predecessor` of a temporal value, and the open-interval
    * bound close (`closeTemporal`): step ONE unit at `s`'s own precision, truncated back to that precision.
    * `minus = false` = successor (add), `minus = true` = predecessor (subtract). */
  def temporalStep(s: String, minus: Boolean): String = {
    if (s == null) return null
    val len = s.length
    trunc(stepRank(parse(s), rankOfLen(len), minus), len)
  }

  /** `cql_date_add(s, n, unit)` — Date/DateTime ± a calendar Quantity (`SqlColumnLowering.dateArith`): add
    * `n` (already signed for subtraction) units of `unit` to `s`, truncated to `s`'s precision. The
    * dimensionless unit `"1"` yields `null` (mirrors `make_interval(null, …)`); a null count yields `null`. */
  def dateAdd(s: String, n: java.lang.Integer, unit: String): String = {
    if (s == null || n == null) return null
    val len = s.length
    val ldt = parse(s)
    val v = n.longValue
    val stepped = unit match {
      case "year" | "years" | "a"                 => ldt.plusYears(v)
      case "month" | "months" | "mo"              => ldt.plusMonths(v)
      case "week" | "weeks" | "wk"                => ldt.plusWeeks(v)
      case "day" | "days" | "d"                   => ldt.plusDays(v)
      case "hour" | "hours" | "h"                 => ldt.plusHours(v)
      case "minute" | "minutes" | "min"           => ldt.plusMinutes(v)
      case "second" | "seconds" | "s"             => ldt.plusSeconds(v)
      case "millisecond" | "milliseconds" | "ms"  => ldt.plusNanos(v * 1000000L)
      case "1"                                    => return null
      case other => throw new IllegalArgumentException(s"cql_date_add: unmodeled duration unit '$other'")
    }
    trunc(stepped, len)
  }

  /** `cql_age_at(birth, asOf)` — whole YEARS between two temporal points (`CalculateAgeAt` at Year
    * precision): the year difference, minus one if `asOf`'s month/day has not yet reached `birth`'s. `null`
    * if either operand is null. */
  def ageAt(birth: String, asOf: String): java.lang.Integer = {
    if (birth == null || asOf == null) return null
    val bt = parse(birth); val at = parse(asOf)
    val notYet = at.getMonthValue < bt.getMonthValue ||
      (at.getMonthValue == bt.getMonthValue && at.getDayOfMonth < bt.getDayOfMonth)
    java.lang.Integer.valueOf(at.getYear - bt.getYear - (if (notYet) 1 else 0))
  }

  /** `cql_temporal_boundary(s, targetRank, high)` — `LowBoundary`/`HighBoundary` at a target precision. Low
    * = truncate to `targetRank`'s length. High = step +1 at `s`'s own precision then −1 at `targetRank`,
    * then truncate (the largest value sharing `s`'s known prefix). Mirrors `SqlColumnLowering.boundary`. */
  def temporalBoundary(s: String, targetRank: Int, high: Boolean): String = {
    if (s == null) return null
    var ldt = parse(s)
    if (high) {
      ldt = stepRank(ldt, rankOfLen(s.length), minus = false)
      ldt = stepRank(ldt, targetRank, minus = true)
    }
    trunc(ldt, lenOfRank(targetRank))
  }

  // -------------------------------------------------------------------- interval construction (ToInterval)

  /** `cql_to_interval(start, end)` — `FHIRHelpers.ToInterval` over a FHIR Period: normalize each endpoint
    * to millisecond width (`fhirTemporal`, i.e. cap at 23 chars) into a `{low, high}` struct. Always a
    * non-null struct (null endpoints become null FIELDS), matching the native `named_struct`. */
  def toInterval(start: String, end: String): CqlInterval = CqlInterval(fhirMs(start), fhirMs(end))
  private def fhirMs(s: String): String = if (s == null || s.length <= 23) s else s.substring(0, 23)

  // -------------------------------------------------------------------- quantity conversion (ConvertQuantity)

  /** `cql_convert_quantity(q, factors, target)` — ELM `ConvertQuantity` with a RUNTIME (dynamic) source
    * unit (EXEC-OPAQUE-UDF, EMIT-SQL-QUANTITY-UDF). The COMPILER owns UCUM (it cannot ship the `sema.Ucum`
    * table into this compiler-free module, `ARCH-PURITY`), so it passes `factors` = a compile-time literal
    * map of each unit COMMENSURABLE with `target` to its conversion factor; the UDF is a pure map-lookup ×
    * multiply. Collapses the ~20-arm UCUM CASE ladder — each arm of which re-embedded the operand quantity
    * struct — to ONE opaque call over ONE struct operand. An unmodeled/incommensurable source unit (absent
    * from `factors`) or a null operand yields NULL.
    *
    * The operand is the Spark-free [[CqlQuantity]] case class (SQL `STRUCT<value: DECIMAL(38,18), unit:
    * STRING>`) and `factors` is a `scala.collection.immutable.Map` — the exact types the UC Scala UDF
    * encoder binds (`EXEC-UC-STRUCT-ARG`); NOT an `org.apache.spark.sql.Row`, so this handler is UC-clean
    * and identical on the OSS `spark.udf.register` path. */
  def convertQuantity(q: CqlQuantity, factors: scala.collection.immutable.Map[String, java.math.BigDecimal],
                      target: String): CqlQuantity = {
    if (q == null || factors == null) return null
    val f = if (q.unit == null) null else factors.getOrElse(q.unit, null)
    if (f == null || q.value == null) null else CqlQuantity(q.value.multiply(f), target)
  }

  // -------------------------------------------------------------------- variant schema validation (QA)

  /** `cql_variant_valid(actualSchemaDdl, expectedDdl)` — a JSON-FREE, UC-safe QA check that what LANDED in
    * a VARIANT field conforms to an expected model schema (a general aid — usable on any VARIANT, e.g. the
    * retrieve UDTF's `parameters` map). `actualSchemaDdl` is the
    * built-in `schema_of_variant(carrier.__variant)` — NOT `to_json` (no JSON round trip) and NOT the raw
    * VARIANT value (no `org.apache.spark.*` type crosses into this sandbox-safe handler, `ARCH-PURITY`);
    * `expectedDdl` is the FHIR-model schema for the demanded slots (`FhirSchema.rawResourceSchema`-derived).
    *
    * VARIANT payloads are SPARSE — each union arm carries only its OWN present slots — so the contract is
    * CONFORMANCE, not equality: every field PRESENT in the actual schema must be DECLARED in the expected
    * schema with a COMPATIBLE type (a demanded-but-absent field is fine; an UNDECLARED field that landed, or
    * a type clash, is a violation → false). Pure recursive DDL parsing: deterministic, no Spark, no JSON, so
    * it is a UC Scala UDF handler as-is (`EXEC-UC-STRUCT-ARG`). Any parse failure yields `false` (fail-closed).
    * Tolerates the two object keywords `schema_of_variant` may emit (`STRUCT<…>` / `OBJECT<…>`),
    * backtick-quoted field names, `DECIMAL(p,s)` precision suffixes, and `VOID` (a null field conforms). */
  def variantValid(actualSchemaDdl: String, expectedDdl: String): Boolean = {
    if (actualSchemaDdl == null || expectedDdl == null) false
    else try DdlType.conforms(DdlType.parse(actualSchemaDdl), DdlType.parse(expectedDdl))
    catch { case _: Throwable => false }
  }

  private sealed trait DdlType
  private object DdlType {
    final case class Obj(fields: scala.collection.immutable.Map[String, DdlType]) extends DdlType
    final case class Arr(elem: DdlType) extends DdlType
    final case class Prim(name: String) extends DdlType

    private val Numeric = Set("TINYINT", "SMALLINT", "INT", "INTEGER", "BIGINT", "LONG", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC")

    def parse(s: String): DdlType = parseType(s.trim)._1

    /** Compatible iff equal, or the expected slot is a `STRING`/`VARIANT` catch-all (a variant infers dates,
      * times and codes as strings, and `VOID` for a null field), or both sit in the numeric family. */
    def primCompatible(actual: String, expected: String): Boolean =
      actual == expected || expected == "STRING" || expected == "VARIANT" ||
        (Numeric(actual) && Numeric(expected)) || actual == "VOID"

    def conforms(actual: DdlType, expected: DdlType): Boolean = (actual, expected) match {
      case (_, Prim("VARIANT"))     => true
      case (Obj(af), Obj(ef))       => af.forall { case (n, at) => ef.get(n).exists(conforms(at, _)) }
      case (Arr(ae), Arr(ee))       => conforms(ae, ee)
      case (Prim(a), Prim(e))       => primCompatible(a, e)
      case _                        => false
    }

    private def normalizePrim(tok: String): String = {
      val t = tok.trim.toUpperCase
      val cut = t.indexOf('(')
      (if (cut < 0) t else t.substring(0, cut)).trim
    }

    // Returns the parsed type and the UNCONSUMED remainder (for the recursive field walk).
    private def parseType(s0: String): (DdlType, String) = {
      val s = s0.trim
      val up = s.toUpperCase
      if (up.startsWith("STRUCT<") || up.startsWith("OBJECT<")) parseFields(s.substring(s.indexOf('<') + 1))
      else if (up.startsWith("ARRAY<")) {
        val (elem, rest) = parseType(s.substring(s.indexOf('<') + 1))
        (Arr(elem), dropClose(rest))
      } else if (up.startsWith("MAP<")) {
        val (_, r1) = parseType(s.substring(s.indexOf('<') + 1)) // key
        val (v, r2)  = parseType(dropComma(r1))                  // value
        (Arr(v), dropClose(r2)) // model a MAP as an array of its value type (no map carriers in practice)
      } else {
        val end = s.indexWhere(c => c == ',' || c == '>')
        val (tok, rest) = if (end < 0) (s, "") else (s.substring(0, end), s.substring(end))
        (Prim(normalizePrim(tok)), rest)
      }
    }

    private def dropComma(s: String): String = { val t = s.trim; if (t.startsWith(",")) t.substring(1) else t }
    private def dropClose(s: String): String = { val t = s.trim; if (t.startsWith(">")) t.substring(1) else t }

    private def parseFields(s0: String): (DdlType, String) = {
      var s = s0.trim
      val fields = scala.collection.mutable.LinkedHashMap.empty[String, DdlType]
      if (s.startsWith(">")) return (Obj(Map.empty), s.substring(1))
      var done = false
      while (!done) {
        s = s.trim
        val colon = s.indexOf(':')
        val name  = s.substring(0, colon).trim.stripPrefix("`").stripSuffix("`").toLowerCase
        val (t, rest) = parseType(s.substring(colon + 1))
        fields += (name -> t)
        s = rest.trim
        if (s.startsWith(",")) s = s.substring(1)
        else if (s.startsWith(">")) { s = s.substring(1); done = true }
        else done = true
      }
      (Obj(fields.toMap), s)
    }
  }
}
