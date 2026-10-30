library FhirDayPrecisionDuring version '1.0.0'

// LANG-INTERVAL-PRECISION: `during day of` compares at DAY precision, so a visit on the window's first or
// last day counts even when it falls outside the window's time of day. Plain `during` compares the
// instants. The window runs noon to noon so the two disagree on the boundary days (CQL spec, Interval
// Operators, "during": the precision applies to both operands).
//
// DATA (`data/qicore/Encounter.ndjson`): q1 visits at 08:00 on the first day and q2 at 15:00 on the last
// day — in by day, out by instant. q3 visits mid-year and q4 at 13:00 on the first day — in both ways. q4
// also visits on the day before — out both ways.
//
// The `Visit Start` pair tests a POINT (the visit's start) rather than an interval. The translator emits `In`
// with a precision for a point `during` an interval, which reads the same way at day precision (CQL spec,
// Interval Operators, "in").
using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "Visit Ids By Day":
  [Encounter] Visit
    where Visit.period during day of Interval[@2026-01-01T12:00:00.000, @2026-12-31T12:00:00.000]
    return Visit.id

define "Visit Ids By Instant":
  [Encounter] Visit
    where Visit.period during Interval[@2026-01-01T12:00:00.000, @2026-12-31T12:00:00.000]
    return Visit.id

define "Visit Start Ids By Day":
  [Encounter] Visit
    where start of Visit.period during day of Interval[@2026-01-01T12:00:00.000, @2026-12-31T12:00:00.000]
    return Visit.id

define "Visit Start Ids By Instant":
  [Encounter] Visit
    where start of Visit.period during Interval[@2026-01-01T12:00:00.000, @2026-12-31T12:00:00.000]
    return Visit.id
