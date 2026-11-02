library FhirRelationClauses version '1.0.0'

// Shaping/ordering/aggregate clauses over a Relation-encoded retrieve list (LANG-QUERY-SORT,
// LANG-QUERY-AGGREGATE). `sort` re-orders the per-subject list; `aggregate` folds it (a subject
// with no rows folds to the seed).
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

// sort (ByDirection) over a Relation list. Project a System Integer per encounter (a FHIR `code`
// Enumeration is not directly orderable in the reference), then sort descending.
define "FinishedFlagsDesc": [Encounter] E return all (if E.status = 'finished' then 1 else 0) sort desc

// aggregate over a Relation list: count conditions per patient. p3 has none → folds to the seed 0.
define "ConditionCount": [Condition] C aggregate Result starting 0: Result + 1

// sort BY EXPRESSION over a Relation list: shape each encounter into a tuple, then order by a tuple
// field (a System Integer key, ascending). Exercises the by-key sort, not the ByDirection sort.
define "EncounterShapesByFinished":
  [Encounter] E
  return Tuple { st: E.status, rank: (if E.status = 'finished' then 0 else 1) }
  sort by rank
