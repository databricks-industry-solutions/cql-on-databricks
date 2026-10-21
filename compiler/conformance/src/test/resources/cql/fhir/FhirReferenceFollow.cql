library FhirReferenceFollow version '1.0.0'

// ISOLATES the #8b fix (LANG-REFERENCE-FOLLOW): a `with` relationship whose related source is a
// CONTEXT-LESS resource — `Medication` has no patient `subject`, so it is not patient-scoped and is
// reached only by FOLLOWING a reference from another resource. `MedicationRequest.medication[x]` may be a
// `Reference(Medication)`; matching it joins the referenced Medication BY ITS id, not by patient subject.
//
// This is the translator's `[MedicationRequest: "vs"]` reference-arm expansion, isolated:
//     [MedicationRequest] MR with [Medication] M such that M.id = Last(Split(MR.medication.reference.value,'/'))
// Exercises FhirModel.isPatientContext, combined's context-less relationship join (correlate on the
// such-that reference equality ALONE, not `__subject_id`), and SparkFhirData's context-less frame with a
// null subject key. m6 (patient p6) has `medicationReference = Medication/med1`, so it follows to med1 and
// is kept; the other MedicationRequests carry `medicationCodeableConcept` (no reference) and drop out.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "MedRequestsWithFollowedMedication":
  [MedicationRequest] MR
    with [Medication] M
      such that M.id = Last(Split(MR.medication.reference.value, '/'))
    return MR.status
