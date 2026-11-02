library FhirValueSet version '1.0.0'

// LANG-TERMINOLOGY, DATA-TERMINOLOGY-ADAPTER: the dominant measure pattern —
// a value-set-filtered retrieve. `[Condition: "Diabetes"]` keeps only Conditions whose code is
// in the (pre-expanded, version-pinned) Diabetes value set; membership is a broadcast semi-join.
// Proves code-filtering, not just existence: a patient with a non-Diabetes Condition is false.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'

context Patient

define "HasDiabetes": exists ([Condition: "Diabetes"])
