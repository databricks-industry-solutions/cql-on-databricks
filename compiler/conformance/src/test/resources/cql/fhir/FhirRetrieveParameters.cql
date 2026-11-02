library FhirRetrieveParameters version '1.0.0'

// BIND-RETRIEVE-REQUEST: a retrieve's request carries every parameter with a constant default that its define
// uses, under its own library's name, so a resolver can choose a relation by it. The parameter scope is read
// before parameters are inlined: read after, no define references one and every request's `parameters` was
// empty. The harness checks each retrieve site's request against the parameters its define reaches, here
// directly ("Recorded In Period") and through another define ("Has Recorded"); "Any Condition" uses none.
//
// Data discriminates: p1, p5 and p6 have a Condition recorded on a day in 2023.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2023-01-01T00:00:00.0, @2024-01-01T00:00:00.0)

context Patient

define "Recorded In Period":
  [Condition] C where C.recordedDate during "Measurement Period"

define "Has Recorded":
  exists "Recorded In Period"

define "Any Condition":
  exists [Condition]
