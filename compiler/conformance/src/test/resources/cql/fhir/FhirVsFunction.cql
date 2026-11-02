library FhirVsFunction version '1.0.0'

// LANG-FUNCTION-TERMINOLOGY: a value set passed as a FUNCTION ARGUMENT carries its
// resolved terminology handle through inlining. The function tests membership of a retrieved
// Condition's code against its value-set operand; inlining substitutes the operand with the
// caller's ValueSetRef, then membership lowers as a broadcast semi-join.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'

context Patient

define function "HasConditionIn"(vs System.ValueSet):
  exists ([Condition] C where C.code in vs)

define "HasDiabetesViaFn": "HasConditionIn"("Diabetes")
