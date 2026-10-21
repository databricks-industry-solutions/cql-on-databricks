library FhirChoiceUnionLabsMeds version '1.0.0'

// LANG-CHOICE-RESOURCE (extends ARCH-COND-UNION / LANG-CHOICE): a SECOND distinct resource-type pair
// — Labs (`Observation`) and Meds (`MedicationRequest`) — to prove the whole-resource heterogeneous
// Choice path is not Encounter/Condition-specific. `[Observation] union [MedicationRequest]` is typed
// `List<Choice<Observation, MedicationRequest>>`; both members are whole resource bodies with distinct
// schemas (Observation carries value[x], MedicationRequest carries medication[x]/intent). Gated vs the
// Java reference (no boxed baseline). Requires MedicationRequest.ndjson test data.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

// Whole-resource heterogeneous union across the Labs/Meds pair.
//   p1 -> {o1,o2,m1}, p2 -> {o3,m2}, p3 -> {o4}, p4 -> {m4}, p5 -> {m5}.
define "LabsOrMeds": [Observation] union [MedicationRequest]

// Conditional one-or-the-other across the same pair: p1/p2/p3 have a finished/final Observation ->
// their Observations; p4/p5 have none -> their MedicationRequests.
define "LabsOrMedsConditional":
  if exists [Observation]
    then [Observation]
    else [MedicationRequest]
