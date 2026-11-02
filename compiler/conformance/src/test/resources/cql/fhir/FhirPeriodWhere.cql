library FhirPeriodWhere version '1.0.0'

// A FHIR Period-typed property in a temporal `where`. `Encounter.period`
// is a FHIR Period (a start/end interval), so `E.period during "Measurement Period"` is an
// interval-in-interval relation — the translator wraps the Period in FHIRHelpers.ToInterval. Lowering
// ToInterval to the engine's `struct(low, high)` temporal interval lets the existing Allen-relation
// algebra (LANG-INTERVAL) evaluate it.
//
// Data discriminates: p1 has e1 (period in MP 2023) + e1b (2022, out) → [finished]; p2 e2 (2023, in)
// → [planned]; p3 e4 (2021, out) → [].
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

parameter "Measurement Period" Interval<DateTime> default Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]

context Patient

define "EncountersDuringMP":
  [Encounter] E where E.period during "Measurement Period" return E.status
