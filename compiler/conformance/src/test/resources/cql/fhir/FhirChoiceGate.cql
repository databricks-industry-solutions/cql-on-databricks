library FhirChoiceGate version '1.0.0'
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers
context Patient
// genuine value[x]: is (where) + as (projection) over a real choice element — the model gate keeps it working
define "StringValues": [Observation] O where O.value is FHIR.string return O.value as FHIR.string
// monomorphic FHIR element: is → false (reference excludes all), as → null
define "EncountersTyped": exists([Encounter] E where E.status is FHIR.EncounterStatus)
define "StatusAsType": [Encounter] E return E.status as FHIR.EncounterStatus
