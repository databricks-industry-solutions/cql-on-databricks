library FhirRetrieve version '1.0.0'

// PHASE-2 slice 1 (DATA-RETRIEVER): the thinnest FHIR retrieve. Patient context keys results
// by __subject_id; `exists [Condition]` is a retrieve + exists with no terminology yet.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "HasAnyCondition": exists [Condition]
