library FhirScalarDefInWhere version '1.0.0'

// ISOLATES the shape where a RELATION query's `where` references a per-subject SCALAR sibling
// define (LANG-QUERY-WHERE over a define DAG): `… where Reading.effective.lastInstant() same day as
// "Latest Reading Day"` reads a scalar define inside a relation query. The relation path (SqlQueryLowering.combined) must LEFT-JOIN that scalar define's
// per-subject view (keyed by subject) and resolve the reference to its value column — not reject it.
//
// "ObsCount" is a per-subject scalar (Count of the patient's Observations); "ManyObsStatuses" is a
// relation query whose `where` gates on it. p1 has 2 Observations (kept), p2/p3 have 1 (dropped). A
// missing scalar join would fail loudly at compile time (the prior blocker).
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "Obs": [Observation]

define "ObsCount": Count("Obs")

define "ManyObsStatuses":
  [Observation] O
    where "ObsCount" >= 2
    return O.status
