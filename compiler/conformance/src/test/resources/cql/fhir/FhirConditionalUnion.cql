library FhirConditionalUnion version '1.0.0'

// ARCH-COND-UNION (transformation-catalog.md section 24, engine-design.md section 6): a LIST-valued
// `if`/`case` whose branches are all relation-valued AND the SAME element type lowers to a UNION of
// the branches, with cqlTrue(condition) / its negation pushed into each branch as a per-subject gate.
// Every branch stays Relation-encoded, so the conditional contributes NO collect_list/posexplode to
// the plan (the boxed `when/otherwise` form does). Result rows are identical to the boxed ground
// truth (CONF-GATE-SELFDIFF, flag on vs off) and to the Java reference (CONF-GATE-MEASURE). Phase A
// (same-type) + Phase C (nested). LANG-3VL: a null/false condition routes to the else branch.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

// A per-subject boolean define used as a condition — computed ONCE and referenced by the branch
// gates (never recomputed per branch).
define "HasCondition": exists [Condition]

// Phase A — `if` over a boolean-define condition; same-type (List<Encounter>) relation branches.
define "EncountersByCondition":
  if "HasCondition"
    then [Encounter] E where E.status = 'finished'
    else [Encounter] E where E.status = 'planned'

// Phase A — `if` over an INLINE exists condition. A subject with no Observation has a null/false
// condition and so takes the else branch, pinning the cqlTrue(C) vs not-cqlTrue(C) partition.
define "EncountersByObservation":
  if exists [Observation]
    then [Encounter] E where E.status = 'finished'
    else [Encounter] E where E.status = 'arrived'

// Phase A — conditionless `case`, same-type relation branches, first-match semantics.
define "EncountersByCase":
  case
    when exists [Condition] then [Encounter] E where E.status = 'finished'
    when exists [Observation] then [Encounter] E where E.status = 'arrived'
    else [Encounter] E where E.status = 'planned'
  end

// Phase C — NESTED `if` (a nested UNION, 2^n leaves), same element type throughout.
define "NestedEncounters":
  if "HasCondition"
    then (if exists [Observation]
            then [Encounter] E where E.status = 'finished'
            else [Encounter] E where E.status = 'arrived')
    else [Encounter] E where E.status = 'planned'
