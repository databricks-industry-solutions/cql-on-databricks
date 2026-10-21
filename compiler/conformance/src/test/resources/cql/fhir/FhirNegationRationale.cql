library FhirNegationRationale version '1.0.0'

// Negation rationale: a procedure recorded as NOT done, with the reason it was not done. Reads the
// negation profile's retrieve (`ProcedureNotDone`), its `statusReason` against a value set, and its
// QI-Core `recorded` element (an extension, `FhirModel.profiledExtensionValueType`) against the period.
//
// The profile filter keeps resources that claim no profile (LANG-RETRIEVE-PROFILE), so the status test is
// what tells a procedure not done from one that was done.
//
// DATA (`data/qicore/Procedure.ndjson`): q1 not done for patient refusal, recorded in 2026; q2 not done
// for another reason; q3 completed; q4 not done for patient refusal, recorded in 2025. Only q1 counts.
using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Screening Procedure": 'http://example.org/fhir/ValueSet/cql-test-screening-procedure'
valueset "Patient Refusal": 'http://example.org/fhir/ValueSet/cql-test-patient-refusal'

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2026-01-01T00:00:00.000, @2026-12-31T23:59:59.999]

context Patient

define "Screening Refused During Period":
  [ProcedureNotDone: "Screening Procedure"] NotDone
    where NotDone.status = 'not-done'
      and NotDone.statusReason in "Patient Refusal"
      and NotDone.recorded during "Measurement Period"
    return NotDone.id

define "Has Refused Screening":
  exists "Screening Refused During Period"
