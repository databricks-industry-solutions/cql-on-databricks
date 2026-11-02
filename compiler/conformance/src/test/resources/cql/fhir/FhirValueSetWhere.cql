library FhirValueSetWhere version '1.0.0'

// LANG-TERMINOLOGY, ARCH-PUSHDOWN: the UNOPTIMIZED twin of FhirValueSetQuery — an
// UNFILTERED retrieve plus a value-set-membership `where`. This is exactly what a measure author
// writes and what the terminology pushdown rewrites into `[Condition: "Diabetes"]` (the optimized
// form 5b lowers). Lowering the membership `where` (a row-level semi-join on the combined frame) is
// the unoptimized ground truth; the self-diff proves the pushdown rule preserves the result.
// Same data discrimination as FhirValueSetQuery: p1 -> [c2], p2 -> [], p3 -> [].
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'

context Patient

define "DiabetesConditionSubjects": [Condition] C where C.code in "Diabetes" return C.subject.reference
