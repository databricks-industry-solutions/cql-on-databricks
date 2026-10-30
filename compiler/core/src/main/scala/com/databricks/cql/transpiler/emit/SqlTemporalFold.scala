package com.databricks.cql.transpiler.emit

import com.databricks.cql.transpiler.emit.SqlExpr._

import scala.collection.mutable

/** A PURE, EMIT-ONLY post-pass that refactors the partial-precision date TOWERS out of an already-lowered
  * [[SqlScript]] into calls to a handful of small SQL-body SCALAR UDFs (EMIT-SQL-TEMPORAL-UDF).
  * It is the "extract super-common code out of the emitted SQL" refactor: the
  * lowering ([[com.databricks.cql.transpiler.lower.SqlColumnLowering]]) is UNTOUCHED — it still
  * inlines the towers — and this pass, run only when `Config.temporalUdfs` is on, folds each recurring
  * tower into a named call and emits the used functions as `CREATE OR REPLACE TEMPORARY FUNCTION` at the
  * head of the script.
  *
  * WHY IT IS SAFE BY CONSTRUCTION. Each fold replaces a subtree `T(x…)` with `Call(f, x…)` where the
  * function body of `f` IS the same template `T` over its parameters — reconstructed by the SAME
  * [[bodyOf]] builder the matcher uses as its equality oracle (a candidate node folds ONLY if it is
  * byte-equal to `bodyOf(fn)` under the extracted holes). Because these are SQL-body functions, Catalyst
  * INLINES the body at analysis time, so `Call(f, x…)` expands back to exactly `T(x…)`: the analyzed and
  * optimized plans — and therefore results, constant-folding, and federated-retrieve arm elimination —
  * are unchanged. Only the emitted SQL TEXT shrinks and reads cleanly. A subtree that does not match is
  * left inline (a safe fallback: correct, just less pretty), so the pass can never mis-lower.
  *
  * SCOPE (first cut): the CONTEXT-FREE towers — `cql_to_ts` (pad an ISO string + cast `TIMESTAMP_NTZ`),
  * `cql_iso` (`date_format`/`replace`/`substr` back to the ISO string at a length), `cql_prec` (the 6-way
  * precision-from-length ladder), and `cql_add_unit`/`cql_sub_unit` (the `make_interval` ±1-calendar-unit
  * step at a precision rank). It ALSO folds the partial-precision COMPARATOR — `cql_cmp` (the tri-state
  * Lt/Gt/Le/Ge core) and `cql_equiv` (the null-safe `Equiv` core) — the `substr(…, 1, least(…))` blocks
  * that dominate temporal `WHERE`s. The `date ± calendar-quantity` unit-dispatch wrapper is still left
  * inline (the emitter knows the unit; a post-pass would re-derive it).
  *
  * TWO KINDS OF FOLD, TWO SAFETY ARGUMENTS. The context-free towers fold BY CONSTRUCTION: the function
  * body IS the matched subtree (same builder as the matcher's equality oracle), so inlining is a
  * byte-identity. The comparator folds are EQUIVALENCE-based: `cql_cmp(a,b) = -1` is provably equal to the
  * `Lt` CASE but not byte-identical (the UDF recomputes `L = least(length a, length b)`, which folds to the
  * emitted `L` for every const/runtime operand mix). Those two rest on the differential (results-identical),
  * not construction — the comparator CASE shape is unique to `temporalCompare`, so a false match is not
  * plausible, and a non-match is always a safe fallback to inline.
  *
  * The templates below MIRROR the private helpers in `SqlColumnLowering` (`tsOf`, `fmtTrunc`, `rankOfLen`,
  * `addUnit`, `temporalCompare`) — that object is the source of truth. Kept as an independent copy on
  * purpose: the fold stays fully DECOUPLED from the lowering (removable wholesale), and any drift merely
  * disables the affected fold (matching stops) rather than mis-lowering. */
object SqlTemporalFold {

  // The UDF names (the readable face of each tower in the emitted SQL).
  val ToTs    = "cql_to_ts"
  val Iso     = "cql_iso"
  val Prec    = "cql_prec"
  val AddUnit = "cql_add_unit"
  val SubUnit = "cql_sub_unit"
  val Cmp     = "cql_cmp"
  val Equiv   = "cql_equiv"

  /** Prelude emission order (no inter-function dependencies, so any order runs — this is just a stable,
    * readable order for the head of the script). */
  private val Order = Seq(Prec, ToTs, Iso, AddUnit, SubUnit, Cmp, Equiv)

  // Literal templates — the source of truth is `SqlColumnLowering.{FullTemplate, fmtTrunc, rankOfLen}`.
  private val FullTemplate = "0000-01-01T00:00:00.000"   // SqlColumnLowering.FullTemplate
  private val FmtMask       = "yyyy-MM-dd HH:mm:ss.SSS"   // SqlColumnLowering.fmtTrunc date_format mask
  // (length <= n) -> rank, for the 6-way `rankOfLen` ladder (SqlColumnLowering.PrecLen); ELSE rank 6.
  private val PrecLadder = Seq(4 -> 0, 7 -> 1, 10 -> 2, 13 -> 3, 16 -> 4, 19 -> 5)
  // DateTimePrecision ranks (CqlType.DateTimePrecision): Year..Second are 0..5, Millisecond 6.
  private val (yearR, monthR, dayR, hourR, minuteR, secondR) = (0, 1, 2, 3, 4, 5)

  // ---------------------------------------------------------------- UDF body templates (== the towers)

  /** `cql_to_ts(s)` — pad the ISO string `s` to a full timestamp and cast (mirrors `tsOf`). */
  private def toTsBody(s: SqlExpr): SqlExpr =
    Cast(Call("concat", Seq(s,
      Call("substr", Seq(str(FullTemplate), Infix("+", Call("length", Seq(s)), int(1)), int(23))))), "TIMESTAMP_NTZ")

  /** `cql_iso(ts, len)` — format a timestamp back to the canonical ISO string, truncated to `len`
    * (mirrors `fmtTrunc`). */
  private def isoBody(ts: SqlExpr, len: SqlExpr): SqlExpr =
    Call("substr", Seq(
      Call("replace", Seq(Call("date_format", Seq(ts, str(FmtMask))), str(" "), str("T"))),
      int(1), len))

  /** `cql_prec(s)` — the precision rank of an ISO string from its length (mirrors `rankOfLen(length(s))`). */
  private def precBody(s: SqlExpr): SqlExpr = rankLadder(Call("length", Seq(s)))
  private def rankLadder(len: SqlExpr): SqlExpr =
    Case(PrecLadder.map { case (n, r) => (Infix("<=", len, int(n)): SqlExpr) -> (int(r): SqlExpr) }, Some(int(6)))

  /** `cql_add_unit(ts, rank)` / `cql_sub_unit(ts, rank)` — add/subtract ONE calendar unit at precision
    * `rank` (mirrors `addUnit`, `minus = false`/`true`). */
  private def addUnitBody(ts: SqlExpr, rank: SqlExpr, minus: Boolean): SqlExpr = {
    val one = int(if (minus) -1 else 1); val z = int(0)
    val ms  = decimal(BigDecimal(if (minus) "-0.001" else "0.001"))
    def slot(r: Int): SqlExpr = Case(Seq(Infix("=", rank, int(r)) -> one), Some(z))
    val secs = Case(Seq(Infix("=", rank, int(secondR)) -> one, Infix("<", rank, int(secondR)) -> z), Some(ms))
    val iv = Call("make_interval", Seq(slot(yearR), slot(monthR), z, slot(dayR), slot(hourR), slot(minuteR), secs))
    Cast(Infix("+", ts, iv), "TIMESTAMP_NTZ")
  }

  /** `cql_cmp(a, b)` — the partial-precision temporal COMPARATOR as a tri-state int: -1 if a's common
    * prefix sorts before b's, 1 if after, 0 if equal at equal precision, NULL if the prefixes are equal but
    * the precisions differ (the CQL "uncertain" case). The tri-state that `temporalCompare`'s Lt/Gt/Le/Ge/
    * Eq/Ne cores all reduce to: `a < b` is `cql_cmp(a,b) = -1`, `a <= b` is `<= 0`, etc. */
  private def cmpBody(a: SqlExpr, b: SqlExpr): SqlExpr = {
    val L  = leastLen(a, b)
    val ta = Call("substr", Seq(a, int(1), L)); val tb = Call("substr", Seq(b, int(1), L))
    Case(Seq(
      Infix("<", ta, tb) -> int(-1),
      Infix(">", ta, tb) -> int(1),
      Infix("=", Call("length", Seq(a)), Call("length", Seq(b))) -> int(0)),
      Some(Null)) // equal prefix, different precision -> NULL (alignment-uncertain)
  }

  /** `cql_equiv(a, b)` — null-safe partial-precision equivalence: compare the common-length prefixes with
    * `<=>` (mirrors `temporalCompare`'s `Equiv` core; never NULL). */
  private def equivBody(a: SqlExpr, b: SqlExpr): SqlExpr = {
    val L = leastLen(a, b)
    Infix("<=>", Call("substr", Seq(a, int(1), L)), Call("substr", Seq(b, int(1), L)))
  }

  private def leastLen(a: SqlExpr, b: SqlExpr): SqlExpr =
    Call("least", Seq(Call("length", Seq(a)), Call("length", Seq(b))))

  /** The `CREATE OR REPLACE TEMPORARY FUNCTION` statement for one UDF name. */
  private def defOf(name: String): SqlStmt.CreateFunction = name match {
    case ToTs    => SqlStmt.CreateFunction(ToTs, Seq("s" -> "STRING"), "TIMESTAMP_NTZ", toTsBody(Col("s")))
    case Iso     => SqlStmt.CreateFunction(Iso, Seq("ts" -> "TIMESTAMP_NTZ", "len" -> "INT"), "STRING", isoBody(Col("ts"), Col("len")))
    case Prec    => SqlStmt.CreateFunction(Prec, Seq("s" -> "STRING"), "INT", precBody(Col("s")))
    case AddUnit => SqlStmt.CreateFunction(AddUnit, Seq("ts" -> "TIMESTAMP_NTZ", "rank" -> "INT"), "TIMESTAMP_NTZ", addUnitBody(Col("ts"), Col("rank"), minus = false))
    case SubUnit => SqlStmt.CreateFunction(SubUnit, Seq("ts" -> "TIMESTAMP_NTZ", "rank" -> "INT"), "TIMESTAMP_NTZ", addUnitBody(Col("ts"), Col("rank"), minus = true))
    case Cmp     => SqlStmt.CreateFunction(Cmp, Seq("a" -> "STRING", "b" -> "STRING"), "INT", cmpBody(Col("a"), Col("b")))
    case Equiv   => SqlStmt.CreateFunction(Equiv, Seq("a" -> "STRING", "b" -> "STRING"), "BOOLEAN", equivBody(Col("a"), Col("b")))
    case other   => throw new IllegalArgumentException(s"unknown temporal UDF '$other'")
  }

  /** The full prelude DDL text for EVERY temporal UDF, for a runtime that pre-registers them once instead
    * of carrying the prelude in each script (the deferred `runtime` deployment mode). */
  def preludeDdl: String = SqlRender.render(SqlScript(Order.map(defOf), ""))

  // ---------------------------------------------------------------- the fold

  /** Fold every context-free temporal tower in `script` into its UDF call, prepending the used function
    * definitions. Returns `script` unchanged if nothing folded (so a temporal-free script is untouched). */
  def apply(script: SqlScript): SqlScript = {
    val used = mutable.LinkedHashSet.empty[String]
    val folded = script.statements.map(mapStmt(_)(e => transform(e, used)))
    if (used.isEmpty) script
    else SqlScript(Order.filter(used.contains).map(defOf) ++ folded, script.target)
  }

  /** Bottom-up: fold the children first, then try to fold this node (so nested towers — `cql_iso` wrapping
    * `cql_add_unit` wrapping `cql_to_ts` — collapse in one pass). */
  private def transform(e: SqlExpr, used: mutable.Set[String]): SqlExpr =
    foldNode(mapChildren(e)(transform(_, used)), used)

  /** Try each tower matcher: extract the candidate holes from known positions, then fold ONLY if the node
    * is byte-equal to the reconstructed template (the body builder is the equality oracle). */
  private def foldNode(e: SqlExpr, used: mutable.Set[String]): SqlExpr = e match {
    // cql_to_ts: Cast(concat(s, substr('0000-…', length(s)+1, 23)), TIMESTAMP_NTZ)
    case Cast(Call("concat", Seq(s, _)), "TIMESTAMP_NTZ") if e == toTsBody(s) =>
      used += ToTs; Call(ToTs, Seq(s))

    // cql_iso: substr(replace(date_format(ts, '…'), ' ', 'T'), 1, len)
    case Call("substr", Seq(Call("replace", Seq(Call("date_format", Seq(ts, _)), _, _)), _, len)) if e == isoBody(ts, len) =>
      used += Iso; Call(Iso, Seq(ts, len))

    // cql_prec: the 6-way rankOfLen ladder over length(s)
    case Case((Infix("<=", Call("length", Seq(s)), _), _) +: _, _) if e == precBody(s) =>
      used += Prec; Call(Prec, Seq(s))

    // cql_add_unit / cql_sub_unit: Cast(ts + make_interval(<per-slot ±1 at rank>), TIMESTAMP_NTZ)
    case Cast(Infix("+", ts, Call("make_interval", slot0 +: _)), "TIMESTAMP_NTZ") =>
      slotRank(slot0) match {
        case Some((rank, minus)) if e == addUnitBody(ts, rank, minus) =>
          val fn = if (minus) SubUnit else AddUnit; used += fn; Call(fn, Seq(ts, rank))
        case _ => e
      }

    // cql_cmp: the partial-precision inequality comparator (temporalCompare Lt/Gt/Le/Ge core):
    //   CASE WHEN (substr(a,1,L) <op1> substr(b,1,L)) THEN true WHEN (<op2>) THEN false
    //        ELSE CASE WHEN (length(a) = length(b)) THEN <tie> ELSE CAST(NULL AS BOOLEAN) END END
    // EQUIVALENCE-based (not byte-reconstruction): cql_cmp(a,b) recomputes L = least(length a, length b),
    // which folds to the emitted L for every const/runtime operand mix, so the rewrite is result-identical
    // (validated by the SQL differential). The two-branch true/false CASE over paired substr-prefix
    // comparisons with a length-equality tie is unique to temporalCompare, so no false match is plausible.
    case Case(Seq((Infix(op1, ta1, tb1), Lit("true")), (Infix(op2, ta2, tb2), Lit("false"))),
              Some(Case(Seq((Infix("=", _, _), Lit(tie))), Some(nb))))
        if nb == nullBool && (tie == "true" || tie == "false") &&
           ((op1 == "<" && op2 == ">") || (op1 == ">" && op2 == "<")) && ta1 == ta2 && tb1 == tb2 =>
      (prefix(ta1), prefix(tb1)) match {
        case (Some((sa, la)), Some((sb, lb))) if la == lb => // same common-length L on both prefixes
          used += Cmp; val cmp = Call(Cmp, Seq(sa, sb))
          (op1, tie) match {
            case ("<", "false") => Infix("=",  cmp, int(-1)) // Lt
            case ("<", _)       => Infix("<=", cmp, int(0))  // Le
            case (">", "false") => Infix("=",  cmp, int(1))  // Gt
            case _              => Infix(">=", cmp, int(0))   // Ge
          }
        case _ => e
      }

    // cql_equiv: the null-safe partial-precision equivalence (temporalCompare Equiv core):
    //   substr(a,1,L) <=> substr(b,1,L). A bare `<=>` is not comparator-specific, so require L to be
    //   exactly least(length a, length b) (the runtime form) — a const-length `<=>` stays inline (safe).
    case Infix("<=>", ta, tb) =>
      (prefix(ta), prefix(tb)) match {
        case (Some((sa, la)), Some((sb, lb))) if la == lb && la == leastLen(sa, sb) =>
          used += Equiv; Call(Equiv, Seq(sa, sb))
        case _ => e
      }

    case _ => e
  }

  private val nullBool: SqlExpr = Cast(Null, "BOOLEAN")

  /** A partial-precision PREFIX `substr(s, 1, L)` — the operand `s` and the common-length `L`. */
  private def prefix(e: SqlExpr): Option[(SqlExpr, SqlExpr)] = e match {
    case Call("substr", Seq(s, Lit("1"), l)) => Some((s, l))
    case _                                   => None
  }

  /** Read `(rank, minus)` from a `make_interval` year slot `CASE WHEN (rank = 0) THEN (±1) ELSE 0 END` —
    * the seed the `cql_add_unit`/`cql_sub_unit` oracle needs; direction is the sign of the ±1. */
  private def slotRank(slot0: SqlExpr): Option[(SqlExpr, Boolean)] = slot0 match {
    case Case(Seq((Infix("=", rank, Lit("0")), one)), Some(Lit("0"))) if one == int(1) || one == int(-1) =>
      Some((rank, one == int(-1)))
    case _ => None
  }

  // ---------------------------------------------------------------- structural tree walk (rebuild)

  /** Apply `f` to every child expression of `e`, rebuilding the node (bottom-up recursion is the caller's). */
  private def mapChildren(e: SqlExpr)(f: SqlExpr => SqlExpr): SqlExpr = e match {
    case Col(_) | Lit(_)        => e
    case Field(b, n)            => Field(f(b), n)
    case Call(fn, as)           => Call(fn, as.map(f))
    case Infix(op, l, r)        => Infix(op, f(l), f(r))
    case Prefix(op, x)          => Prefix(op, f(x))
    case Postfix(x, op)         => Postfix(f(x), op)
    case Case(ws, oe)           => Case(ws.map { case (c, v) => f(c) -> f(v) }, oe.map(f))
    case Cast(x, ty)            => Cast(f(x), ty)
    case Lambda(ps, b)          => Lambda(ps, f(b))
    case Struct(fs)             => Struct(fs.map { case (n, v) => n -> f(v) })
    case Over(fn, part, ord)    => Over(f(fn), part.map(f), ord.map(k => k.copy(expr = f(k.expr))))
  }

  /** Apply `f` to every expression embedded in a relation (recursing into sub-relations). */
  private def mapRel(r: SqlRel)(f: SqlExpr => SqlExpr): SqlRel = r match {
    case SqlRel.TableRef(_) | SqlRel.OneRow => r
    case SqlRel.TableFunc(fn, args, catalog) => SqlRel.TableFunc(fn, args.map(f), catalog)
    case s: SqlRel.Select =>
      s.copy(projections = s.projections.map(p => p.copy(expr = f(p.expr))), from = mapRel(s.from)(f),
        where = s.where.map(f), groupBy = s.groupBy.map(f), having = s.having.map(f),
        orderBy = s.orderBy.map(k => k.copy(expr = f(k.expr))))
    case SqlRel.Join(l, rr, k, on, using) => SqlRel.Join(mapRel(l)(f), mapRel(rr)(f), k, on.map(f), using)
    case SqlRel.UnionAll(parts)           => SqlRel.UnionAll(parts.map(mapRel(_)(f)))
    case SqlRel.LateralView(in, gen, ta, cols, outer) => SqlRel.LateralView(mapRel(in)(f), f(gen), ta, cols, outer)
    case SqlRel.AliasRel(rel, a)          => SqlRel.AliasRel(mapRel(rel)(f), a)
    case SqlRel.With(ctes, body)          => SqlRel.With(ctes.map { case (n, rr) => n -> mapRel(rr)(f) }, mapRel(body)(f))
  }

  /** Apply `f` to every expression in a statement's relation body (a `CreateFunction` prelude, if any,
    * is left alone — its body is already a template). */
  private def mapStmt(s: SqlStmt)(f: SqlExpr => SqlExpr): SqlStmt = s match {
    case SqlStmt.CreateView(n, r)      => SqlStmt.CreateView(n, mapRel(r)(f))
    case SqlStmt.CacheTable(n, r)      => SqlStmt.CacheTable(n, mapRel(r)(f))
    case cf: SqlStmt.CreateFunction    => cf
  }
}
