library FhirReturnList version '1.0.0'

// PHASE-2 (DATA-LIST-ENCODING): a per-subject ORDERED list materialized from a retrieve. Retrieve
// rows are exploded per subject, the return projects each, and the result is collected back into a
// list whose order is pinned by __ordinal (stable resource order) — the explode/collect_list
// boundary the encoding exists to make correct. p1 has two encounters, so order is exercised.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "EncounterStatuses": [Encounter] E return E.status
