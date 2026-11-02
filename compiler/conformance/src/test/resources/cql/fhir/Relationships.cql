library Relationships version '1.0.0'

// Relationship clauses `with`/`without` (LANG-QUERY-WITH): `with` keeps source rows that HAVE a
// related row satisfying `such that` (semi-join); `without` is its negation (anti-join). Over FHIR
// retrieves these are correlated set operations on the patient-context key, not row UDFs.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "HasEncounterWithCondition": exists ([Encounter] E with [Condition] C such that true)
define "HasEncounterWithoutCondition": exists ([Encounter] E without [Condition] C such that true)
