library FhirListCount version '1.0.0'

// ARCH-RELATION-REDUCE: a list-valued define consumed by `Count`. Under grain-first the list stays a
// Relation (rows + __ordinal); `Count` is realized as a SUBJECT-GRAIN aggregate (no collect_list box):
// CQL `Count` is the number of NON-NULL elements, and `return` defaults to distinct, so it is
// `countDistinct(__val)` per subject. A subject with no rows (empty OR null list) is absent from the
// grouping → coalesced to 0, which is CQL `Count` of both empty and null.
//   p1 [finished, arrived] -> 2 ;  p2 [planned] -> 1 ;  p3 [finished] -> 1 ;  p4,p5 (no Encounter) -> 0
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "Statuses": [Encounter] E return E.status

define "StatusCount": Count("Statuses")
