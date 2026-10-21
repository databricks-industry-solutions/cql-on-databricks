library FhirConditional version '1.0.0'

// Conditional whose branches/condition reach into DIFFERENT sources (ARCH-CONDITIONAL): per patient,
// choose between list-valued queries based on a retrieve-derived condition. Each branch is
// materialized per subject and CASE-selected (co-located on __subject_id), the set-at-a-time form
// of the canonical UNION-ALL-of-filtered-branches. (Phase 4c.)
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

// `if` over a retrieve-existence condition, with list-valued branches from different sources. Both
// branches are List<Integer> (a finished-flag per encounter, vs a sentinel) so the conditional
// selects between two per-subject lists of the same type.
define "EncounterFlagsIfCondition":
  if exists [Condition]
    then ([Encounter] E return all (if E.status = 'finished' then 1 else 0))
    else { -1 }

// `case` (conditionless) selecting a sentinel list by retrieve-existence — both branches lists.
define "ConditionPresence":
  case
    when exists [Condition] then { 'has' }
    else { 'none' }
  end
