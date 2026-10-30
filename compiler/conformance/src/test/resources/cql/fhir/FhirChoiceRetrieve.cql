library FhirChoiceRetrieve version '1.0.0'

// FHIR value[x] is a CHOICE at the source (LANG-CHOICE): an Observation's `value` is one of
// Quantity, string, CodeableConcept, … stored physically as `valueQuantity`/`valueString`/…
// `is` tests which member is populated; `as` projects the matching member (null otherwise).
// (Phase 4d.)
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

// Project the string-valued observations' values (skip the Quantity-valued ones).
define "StringObservationValues":
  [Observation] O where O.value is FHIR.string return all O.value as FHIR.string

// Per patient: has at least one Quantity-valued observation (the `is` discriminator test).
define "HasQuantityObservation":
  exists ([Observation] O where O.value is FHIR.Quantity)
