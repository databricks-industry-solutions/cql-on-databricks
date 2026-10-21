library FhirWhere version '1.0.0'

// PHASE-2 (LANG-QUERY): a `where` over a FHIR retrieve. `E.status = 'finished'` navigates a FHIR
// primitive (code), which the translator converts via FHIRHelpers — so this also exercises the
// FHIRHelpers cross-library include.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "HasFinishedEncounter": exists ([Encounter] E where E.status = 'finished')
