library TemporalPartialPrecision version '1.0.0'

// PHASE-8 (LANG-INTERVAL): the precision-aware comparison truth table for a partial-precision
// DateTime operand against a STATIC bound — the cells the length-dispatch lexical emission must
// reproduce. The normative oracle is the cqframework Java reference (CONF-GATE-MEASURE), pinned by
// SemanticsProbeSpec: the reference is COMPONENT-WISE-NULL, not uncertainty-interval. Two consequences
// this fixture locks against any future emitter:
//   (1) a coarse operand whose prefix EQUALS the bound at the operand's precision is NULL whenever the
//       operand is coarser than the bound — REGARDLESS of whether the bound is calendar-aligned (the
//       "totality under alignment" shortcut is unsound: see the *_AlignedEq_* cells, all NULL);
//   (2) equality at the lesser precision is DECIDED only when both operands carry the SAME precision
//       (the *VsDay_Eq_* / Full_Eq_* cells), else NULL.
// Bounds: FullNA = a full (second) bound NOT aligned to any coarser unit; YrAl = a full bound whose
// value IS year/month/day-aligned (to refute alignment-totality); DayB = a DAY-precision bound.

// ---- YEAR operand vs FullNA bound (@2023-06-15T10:30:45); bound's year-prefix = 2023 ----
// Below (2022 < 2023): decided at year.
define "Yr_Below_Lt": @2022T <  @2023-06-15T10:30:45
define "Yr_Below_Le": @2022T <= @2023-06-15T10:30:45
define "Yr_Below_Gt": @2022T >  @2023-06-15T10:30:45
define "Yr_Below_Ge": @2022T >= @2023-06-15T10:30:45
define "Yr_Below_Eq": @2022T =  @2023-06-15T10:30:45
// Prefix-equal at year (2023 == 2023), operand coarser than the full bound -> NULL for every op.
define "Yr_Eq_Lt": @2023T <  @2023-06-15T10:30:45
define "Yr_Eq_Le": @2023T <= @2023-06-15T10:30:45
define "Yr_Eq_Gt": @2023T >  @2023-06-15T10:30:45
define "Yr_Eq_Ge": @2023T >= @2023-06-15T10:30:45
define "Yr_Eq_Eq": @2023T =  @2023-06-15T10:30:45
// Above (2024 > 2023): decided at year.
define "Yr_Above_Lt": @2024T <  @2023-06-15T10:30:45
define "Yr_Above_Ge": @2024T >= @2023-06-15T10:30:45

// ---- YEAR operand vs YrAl bound (@2023-01-01T00:00:00, calendar-aligned): STILL NULL ----
// Refutes the "aligned bound -> total" shortcut: alignment never makes a coarse operand decidable.
define "Yr_AlignedEq_Lt": @2023T <  @2023-01-01T00:00:00
define "Yr_AlignedEq_Le": @2023T <= @2023-01-01T00:00:00
define "Yr_AlignedEq_Gt": @2023T >  @2023-01-01T00:00:00
define "Yr_AlignedEq_Ge": @2023T >= @2023-01-01T00:00:00

// ---- MONTH operand vs FullNA bound; bound's month-prefix = 2023-06 ----
define "Mo_Below_Lt": @2023-05T <  @2023-06-15T10:30:45
define "Mo_Eq_Lt":    @2023-06T <  @2023-06-15T10:30:45
define "Mo_Eq_Ge":    @2023-06T >= @2023-06-15T10:30:45
define "Mo_Above_Ge": @2023-07T >= @2023-06-15T10:30:45

// ---- DAY operand vs FullNA bound; bound's day-prefix = 2023-06-15 (day < full -> eq NULL) ----
define "Day_Below_Lt": @2023-06-14T <  @2023-06-15T10:30:45
define "Day_Eq_Lt":    @2023-06-15T <  @2023-06-15T10:30:45
define "Day_Eq_Ge":    @2023-06-15T >= @2023-06-15T10:30:45
define "Day_Above_Ge": @2023-06-16T >= @2023-06-15T10:30:45

// ---- DAY operand vs DayB bound (@2023-06-15T, SAME precision) -> equality DECIDED ----
define "DayVsDay_Eq_Lt": @2023-06-15T <  @2023-06-15T
define "DayVsDay_Eq_Le": @2023-06-15T <= @2023-06-15T
define "DayVsDay_Eq_Gt": @2023-06-15T >  @2023-06-15T
define "DayVsDay_Eq_Ge": @2023-06-15T >= @2023-06-15T
define "DayVsDay_Eq_Eq": @2023-06-15T =  @2023-06-15T

// ---- FULL operand vs FullNA bound (SAME precision) -> equality DECIDED ----
define "Full_Eq_Lt": @2023-06-15T10:30:45 <  @2023-06-15T10:30:45
define "Full_Eq_Le": @2023-06-15T10:30:45 <= @2023-06-15T10:30:45
define "Full_Eq_Gt": @2023-06-15T10:30:45 >  @2023-06-15T10:30:45
define "Full_Eq_Ge": @2023-06-15T10:30:45 >= @2023-06-15T10:30:45
define "Full_Eq_Eq": @2023-06-15T10:30:45 =  @2023-06-15T10:30:45
define "Full_Below_Lt": @2023-06-15T10:30:44 < @2023-06-15T10:30:45
define "Full_Above_Gt": @2023-06-15T10:30:46 > @2023-06-15T10:30:45

// ---- BOUNDARY / ALIGNED-BOUND cases (the alignment dispute). A day/year operand whose prefix EQUALS
//      an ALIGNED MP bound. cqframework returns NULL (equal at the lesser precision, differing precision
//      → undecidable); ALIGNMENT DOES NOT make these total/TRUE. The engine must MATCH the reference,
//      not "optimize" to TRUE — proven by the differential (engine vs reference), not just the oracle. ----
define "DayEdge_Lo_Le": @2023-01-01T00:00:00 <= @2023-01-01
define "DayEdge_Hi_Le": @2023-12-31 <= @2023-12-31T23:59:59
define "IvDayEdge_During_MPfull":
  Interval[@2023-01-01, @2023-12-31] during Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]
define "PtDayEdge_In_MPfull":
  @2023-01-01 in Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]

// ---- Interval 'during' / point 'in' forms: the operator shape over a full-precision MP ----
// A coarse interval/point whose value lands on the MP edge but spills sub-precision -> NULL.
define "IvYr_During_MPfull":
  Interval[@2023T, @2023T] during Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]
define "IvDay_During_MPfull":
  Interval[@2023-06-10T, @2023-06-20T] during Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]
define "IvYr_During_MPyr":
  Interval[@2023T, @2023T] during Interval[@2023T, @2023T]
define "PtYr_In_MPfull":
  @2023T in Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]
define "PtDay_In_MPfull":
  @2023-06-15T in Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]

// ---- NAMED-PRECISION Allen relations (LANG-INTERVAL-PRECISION) ----
// `during day of` / `overlaps day of` name the precision the COMPARISON runs at. The name belongs to the
// relation, not the operands, so BOTH sides truncate to it — and the coarse-vs-fine tie that makes the
// UNQUALIFIED siblings above NULL cannot arise. Each cell here is deliberately the same SHAPE as one
// above, differing only by the qualifier, so a regression shows up as a NULL where a decided value belongs.
//
// Regression under test: a measure asking `Encounter.period during day of "Measurement Period"`. The IR
// node had no field for the qualifier, so it was dropped at LOAD and the comparison ran at whatever
// precisions the operands arrived at. With the period bound at day precision and encounter periods
// starting exactly at `2026-01-01T00:00:00.000`, that compared 10 chars against 23, tied, and returned
// NULL for every qualifying encounter — an empty denominator, no error. Reverting either half of the
// fix (the IR/codec field, or the lowering truncation) must fail these.

// Fine operand vs COARSE bound — the exact measure shape. Unqualified this is NULL; at day it is decided.
define "IvFine_DuringDayOf_MPday":
  Interval[@2026-01-01T00:00:00.000, @2026-01-01T01:00:00.000] during day of Interval[@2026-01-01, @2026-12-31]
// The mirror: COARSE operand vs fine bound. Same tie, other direction; compare `IvDayEdge_During_MPfull`.
define "IvDay_DuringDayOf_MPfull":
  Interval[@2023-01-01, @2023-12-31] during day of Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]
// NEGATIVE CONTROL: truncation must DECIDE the tie, never blanket-approve. A day wholly outside the
// bound stays FALSE — without this, a fix that simply returned TRUE on any tie would pass the cells above.
define "IvOutside_DuringDayOf_MPday":
  Interval[@2027-01-01T00:00:00.000, @2027-01-02T00:00:00.000] during day of Interval[@2026-01-01, @2026-12-31]

// `overlaps day of` carries the qualifier identically (14 occurrences across the corpus vs 7 for during).
// Shares one day (2026-12-31) at day precision -> decided TRUE, though the fine operand spills past the bound.
define "IvFine_OverlapsDayOf_MPday":
  Interval[@2026-12-31T23:00:00.000, @2027-01-05T00:00:00.000] overlaps day of Interval[@2026-01-01, @2026-12-31]
// NEGATIVE CONTROL for overlaps: disjoint at day precision -> FALSE.
define "IvOutside_OverlapsDayOf_MPday":
  Interval[@2027-01-02T00:00:00.000, @2027-01-05T00:00:00.000] overlaps day of Interval[@2026-01-01, @2026-12-31]
