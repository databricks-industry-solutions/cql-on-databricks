library FhirMultiSource version '1.0.0'

// Multi-source FHIR queries and correlated relationship clauses over the Relation encoding
// (LANG-QUERY-MULTISOURCE, LANG-QUERY-WITH). Sources are FHIR retrieves combined per patient
// (a __subject_id-keyed Cartesian product); `with`/`without` are semi-/anti-joins whose `such that`
// may reference the source alias (the two-sided join). Returns project clean FHIR primitives
// (a resource `id` renders type-prefixed in the reference, so the differential uses `status`).
// (Phase 4b.4.)
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

// Multi-source Cartesian combination per patient (encounters × conditions), scalar return. p3 has
// no condition, so the product is empty. (4b.4a)
define "StatusesPerConditionPair":
  from [Encounter] E, [Condition] C
  return all E.status

// Correlated `where` referencing the source aliases across the combination. (4b.4a)
define "FinishedStatusesWithCondition":
  from [Encounter] E, [Condition] C
  where E.status = 'finished'
  return all E.status

// Tuple shaping over a Relation list — the tuple encoding (a struct per row). (4b.4a)
define "EncounterShapes":
  [Encounter] E
  return Tuple { st: E.status, finished: (if E.status = 'finished' then 'yes' else 'no') }

// Correlated `such that` referencing the SOURCE alias — the two-sided semi-join: per patient, has a
// finished encounter AND at least one condition. (4b.4b)
define "HasFinishedEncounterWithCondition":
  exists ([Encounter] E with [Condition] C such that E.status = 'finished')

// `with` returning a LIST (not just exists): encounter statuses of patients that have a condition. (4b.4b)
define "StatusesWithCondition":
  [Encounter] E with [Condition] C such that true return all E.status

// `without` returning a LIST: encounter statuses of patients with NO condition. (4b.4b)
define "StatusesWithoutCondition":
  [Encounter] E without [Condition] C such that true return all E.status
