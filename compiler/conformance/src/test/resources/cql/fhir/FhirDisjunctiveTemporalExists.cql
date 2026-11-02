library FhirDisjunctiveTemporalExists version '1.0.0'

// ISOLATES the disjunctive temporal-exclusion plan shape — the barrier whose final localCheckpoint OOMed
// the Connect server (blocker #8 perf).
//
// A boolean define that ORs several CORRELATED temporal `exists`, each over a retrieve with a
// value[x]/Period temporal filter (`… overlaps/during "Measurement Period"`): an exclusion with about
// six arms (an inpatient discharge, an encounter, an assessment, an order, a procedure and a diagnosis,
// each tested against the period its own way).
//
// Each `exists` lifts to a per-subject boolean flag; the DATA-MATERIALIZE rule (FrameLowering.presenceRef)
// stages each as its OWN barrier leaf, so the define barrier only ORs flag columns. Without it
// all the temporal `Case` ladders inline into one ~1.9 MB / 1137-unresolved-function barrier. Run with
//     -Dcqldatabricks.barrierTrace=<file>
// to see each staged barrier's proto size (bytes + unresolved_function count).
//
// FINDING — the driver is PLAN COMPLEXITY, NOT patient count. A barrier's proto size is the UNRESOLVED
// PLAN, independent of data volume. Here each staged `exists` is ~30–90 KB / ~40 unresolved-functions; a
// helper-library prevalence-interval arm over the FULL onset/effective choice was a 716 KB / 602-fn
// barrier — a ~13× heavier PLAN at a comparable data scale. Cranking THIS fixture to six heavier
// interval-`overlaps` arms at the same ~8 patients keeps every barrier ~44 fns and does NOT OOM — arm
// COUNT and patient COUNT don't move the needle. The weight comes from `value[x].valueSpan()` over the
// full choice (≈5 members × interval construction × the Allen re-reads). So this fixture isolates the
// disjunctive-exists SHAPE and proves the exists-barrier-splitting fix. Free of value sets / deps so
// barriers attribute to the shape.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

parameter "Measurement Period" Interval<DateTime> default Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]

context Patient

define "HasAnyTimely":
  exists ([Observation] O where (O.effective as FHIR.dateTime) during "Measurement Period")
    or exists ([Encounter] E where E.period during "Measurement Period")
    or exists ([Condition] C where (C.onset as FHIR.dateTime) during "Measurement Period")
    or exists ([MedicationRequest] M where M.authoredOn during "Measurement Period")
    // A diagnosis-style arm: a temporal exists over a HETEROGENEOUS union — combines the
    // disjunctive-exists barrier with the het-union realization + per-member value[x] dispatch, the
    // combination that blows the barrier up.
    or exists (([Observation] union [MedicationRequest]) X
        where Coalesce(X.effective as FHIR.dateTime, X.authoredOn) during "Measurement Period")
