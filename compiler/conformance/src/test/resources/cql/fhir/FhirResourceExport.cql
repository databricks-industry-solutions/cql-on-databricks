library FhirResourceExport version '1.0.0'

// DATA-EXPORT: a result that surfaces the RAW FHIR resource (not a projection). The engine
// exports each resource by its identity, its declared type and its own id (→ "Type/id");
// the Java reference renders the same HAPI resource to the same "Type/id". So a raw-resource list
// result diffs clean by IDENTITY (which source instances fed the result) rather than by un-normalized
// resource shape — the engine's owned traceability problem. Full resource serialization (MeasureReport)
// is downstream/out of scope.
//
// Data discriminates: p1 has two encounters (e1, e1b) so order + multiplicity are exercised, p2 one
// (e2), p3 one (e4). A wrong order or a dropped/extra instance changes the per-subject identity list.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "EncounterResources": [Encounter] E return E
