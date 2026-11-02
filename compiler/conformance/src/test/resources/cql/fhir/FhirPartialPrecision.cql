library FhirPartialPrecision version '1.0.0'

// LANG-INTERVAL, property-based precision hardening: a PARTIAL FHIR date compares at its
// own coarser precision, read PER ROW from the value (not the element's nominal type). The data
// discriminates by precision alone: p4's Condition has recordedDate "2023" (YEAR precision), p5's
// has "2023-06-15" (DAY precision) — the same calendar year. Against a DAY-precision interval the
// day date is decidable but the year date is undecidable (→ excluded); against a YEAR-precision
// interval it flips. Matching the Java reference on both is the gate (the per-row precision the
// reference derives from the string is what the engine must reproduce).
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

// DAY-precision window: only a date known to DAY precision can be placed in June — the year-only
// date is undecidable (insufficient precision) and excluded. → p5 true, p4 false.
define "DuringJune2023":
  exists ([Condition] C where C.recordedDate during Interval[@2023-06-01T, @2023-06-30T])

// YEAR-precision window: the year-only date is decidable here, but the day date is undecidable
// against year bounds. → p4 true, p5 false (the SAME two dates, flipped by the window's precision).
define "DuringYear2023":
  exists ([Condition] C where C.recordedDate during Interval[@2023T, @2024T])
