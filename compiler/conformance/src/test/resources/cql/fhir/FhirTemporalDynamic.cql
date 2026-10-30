library FhirTemporalDynamic version '1.0.0'

// PHASE-8 (LANG-INTERVAL): precision-aware temporal comparison where BOTH operands are runtime data
// (neither is a compile-time bound) — a Condition.recordedDate (varying per-row precision) compared
// against an Encounter.period (a FHIR Period -> Interval<DateTime>). The static-bound lexical
// optimization does NOT apply here, so this gates the general precision-aware comparator against the
// Java reference (CONF-GATE-MEASURE): an undecidable comparison (a year-precision recordedDate vs a
// full-precision encounter window) drops the row exactly as the reference does, while a day/month
// recordedDate that sits decidably inside the window is kept.
//
// Data (data/fhir, patients p6/p7): ep6 = June 2023 full-precision window; ep7 = all-of-2023 window.
//   p6: c6a 2023-06-15 (day, inside -> kept) | c6b 2023 (year vs full -> undecidable, dropped)
//       | c6c 2024-06-15 (day, outside -> dropped)
//   p7: c7a 2023-06 (month, decidably inside -> kept) | c7b 2023 (year vs full -> undecidable, dropped)
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

// The recordedDates of conditions that fall DURING some encounter's period (point-in-interval over
// two runtime operands), via a `with` semi-join (LANG-QUERY-WITH). Undecidable (insufficient-
// precision) matches do not satisfy `such that` and are dropped, exactly as the reference drops them.
define "CondDatesDuringEnc":
  [Condition] C
    with [Encounter] E such that C.recordedDate during E.period
    return C.recordedDate

// Per-patient: does any condition fall during any encounter? (the boolean membership form)
define "HasCondDuringEnc":
  exists ([Condition] C with [Encounter] E such that C.recordedDate during E.period)
