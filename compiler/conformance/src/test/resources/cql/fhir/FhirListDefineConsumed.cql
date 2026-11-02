library FhirListDefineConsumed version '1.0.0'

// DATA-SKIP-UNFILTERED, DATA-DEFINE-DF: the broad-define-then-filter measure pattern.
// A bare unfiltered-retrieve define `E` is realized as a Relation VIEW over the source scan; a
// downstream query consumes `E` as a Relation SOURCE (joined on __subject_id, carrying E's __ordinal),
// so the consumer's `where` folds per-consumer onto the scan instead of iterating a collect_list
// array. This is the whole point of relation-encoding list defines: no aggregation barrier between the
// broad define and its filtered consumers.
//
// Data discriminates: p1 has e1 (finished) + e1b (arrived) → [finished]; p2 e2 (planned) → []; p3 e4
// (finished) → [finished]. A lost __subject_id key or a wrong source shape changes the per-subject list.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "AllEncounters": [Encounter]

define "FinishedFromAll":
  AllEncounters EE where EE.status = 'finished' return EE.status
