library FhirCorrelatedExistsDisjunction version '1.0.0'

// Isolates the rule that CSE and hoisting must not cross nested-query alias scope: a query whose `where`
// is a DISJUNCTION with a CORRELATED `exists` sub-query, and whose inner `where` REPEATS an expensive
// value[x] `Case` referencing the INNER alias. The shape:
//     (source) X where <cond> or exists ([Y] O where <Case(O)> … X.<field> …)
//
// The outer `where`'s CSE (`materializeCSE`) and nested-query hoist (`hoistWhereQueries`) must NOT
// descend into the correlated `exists` and materialize the inner-alias-scoped `Case`/query in the
// OUTER frame; that lowers a clause with an unbound alias (`AliasRef 'O'`). The CSE count stops at
// Query, and the hoist walk leaves a relation-source (correlated exists) query whole.
//
// The `case when O.onset is FHIR.dateTime then … end` Case appears TWICE inside the exists (so the CSE
// count reaches 2 and fires on it — a `Case` node, the expensive-CSE target) and the exists correlates the
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
