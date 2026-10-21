library FhirSubjectReferenceFields version '1.0.0'

// DATA-SUBJECT-KEY: not every patient-compartment resource names its patient in `subject`. A `Coverage`
// names the covered person in `beneficiary` and an `Immunization` in `patient`, so a retrieve in the
// Patient context must key them by those fields (`FhirModel.SubjectReferenceFields`). Keyed by `subject`
// they would match nobody, and every define here would be false or empty.
//
// "Coverage Types" returns the plans' `type` concepts, so the comparison also covers how both engines
// render a list of Concepts.
//
// DATA (`data/qicore/`): c1 has one coverage, c2 one immunization, c3 two coverages and an immunization.
// Every other patient has neither.
using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "Has Coverage":
  exists [Coverage]

define "Coverage Types":
  [Coverage] Plan
    return Plan.type

define "Has Immunization":
  exists [Immunization]
