library FhirListWholeListOp version '1.0.0'

// DATA-LIST-ENCODING, DATA-DEFINE-DF: a list-valued DEFINE consumed by a
// WHOLE-LIST operator. `E` is realized as a Relation frame (rows + __ordinal); a whole-list operator
// (`in`, `Count`) is an Array-forcing boundary, so `E` is boxed back via collect_list at the
// consumption site. This is the regression guard for the encoding split: the boxed list must be
// byte-identical to the pre-split Array column. The corpus otherwise only consumes list defines at
// the output boundary, so without this the collect-back coercion path is untested.
//
// Data discriminates: p1 has two encounters [finished, arrived] (mixed), p2 [planned], p3 [finished].
//   EHasFinished: p1 true, p2 false, p3 true (p2 proves the membership actually filters).
//   EncounterCount: p1 2, p2 1, p3 1 (p1 proves the whole list — not a singleton — is collected).
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "E": [Encounter] EE return EE.status

define "EHasFinished": 'finished' in "E"

define "EncounterCount": Count("E")
