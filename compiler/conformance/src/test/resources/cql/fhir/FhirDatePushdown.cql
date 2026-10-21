library FhirDatePushdown version '1.0.0'

// PHASE-6 (Phase-5 carried item): date-range pushdown over a FHIR `date`-typed property. `birthDate`
// is a FHIR `date` (day precision), so the translator wraps the navigation in FHIRHelpers.ToDate (not
// ToDateTime). The date pushdown now folds a ToDate point too, RECORDING the converter on the retrieve
// so the scan filter reconstructs an IDENTICAL predicate — at Date (day) precision, not DateTime — to
// the unoptimized `where` (exact self-diff, CONF-GATE-SELFDIFF). Previously only ToDateTime folded.
//
// Data discriminates: p1 born 1980-05-01 (in range) and p3 1990-02-14 (in) → true; p2 1975-11-20
// (out) → false.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "BornInRange":
  exists ([Patient] P where P.birthDate during Interval[@1980-01-01, @1990-12-31])
