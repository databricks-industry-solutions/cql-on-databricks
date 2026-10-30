library FhirCorrelatedExistsDisjunction version '1.0.0'

// ISOLATES the #8c fix (CSE/hoist must not cross nested-query alias scope): a query whose `where`
// is a DISJUNCTION with a CORRELATED `exists` sub-query, and whose inner `where` REPEATS an expensive
// value[x] `Case` referencing the INNER alias. The shape:
//     (source) X where <cond> or exists ([Y] O where <Case(O)> … X.<field> …)
//
// The outer `where`'s CSE (materializeCSE PHASE 2) and nested-query hoist (hoistWhereQueries) must NOT
// descend into the correlated `exists` and try to materialize the inner-alias-scoped `Case`/query in the
// OUTER frame — that lowered a clause with an unbound alias (`AliasRef 'O'`). PHASE 2 now counts top-level
// only (stops at Query) and the hoist walk leaves a relation-source (correlated exists) query whole.
//
// The `case when O.onset is FHIR.dateTime then … end` Case appears TWICE inside the exists (so PHASE 2's
// count>=2 fires on it pre-fix — a `Case` node, the expensive-CSE target) and the exists correlates the
// inner Condition to the outer Observation.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "ObservationsWithRecentCondition":
  [Observation] X
    where X.status = 'final'
      or exists ([Condition] O
          where (case when O.onset is FHIR.dateTime then O.onset as FHIR.dateTime else null end) before X.issued
            and (case when O.onset is FHIR.dateTime then O.onset as FHIR.dateTime else null end) after @1900-01-01)
