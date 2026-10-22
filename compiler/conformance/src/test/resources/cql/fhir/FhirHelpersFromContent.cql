library FhirHelpersFromContent version '1.0.0'

// LANG-FHIRHELPERS-FROM-CONTENT: FHIRHelpers is an included library like any other. It resolves from the
// FHIRHelpers.cql beside this file; the compiler bundles none. `Patient.gender` is a FHIR code, so comparing
// it to a String converts it with FHIRHelpers.ToString.
//   p1, p3, p5, p6 female -> true ;  p2, p4, p7, p8 male -> false
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "Is Female": Patient.gender = 'female'
