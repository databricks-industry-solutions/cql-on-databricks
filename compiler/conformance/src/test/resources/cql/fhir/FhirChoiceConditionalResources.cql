library FhirChoiceConditionalResources version '1.0.0'

// LANG-CHOICE-RESOURCE (extends ARCH-COND-UNION Phase B / LANG-CHOICE): a list-valued `if`/`case`
// whose branches return DIFFERENT FHIR RESOURCE types. `if C then [Encounter] else [Condition]` is
// typed `List<Choice<Encounter, Condition>>` — the heterogeneous-branch union (cqlTrue(C) pushed into
// the then-arm, its negation into the else-arm) but with whole resource BODIES as the Choice members
// rather than the scalar projections of FhirConditionalUnionHet. Only ONE arm is live per subject, so
// the result is a homogeneous slice of the Choice per patient; the engine must still align the two arm
// schemas into one Choice-tagged relation. Gated vs the Java reference (no boxed baseline).
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "HasObservation": exists [Observation]

// `if` over a per-subject boolean. p1/p2/p3 have an Observation -> their Encounters; p4/p5 do not
// -> their Conditions. Both arms are exercised across the patient set:
//   p1 -> {e1,e1b}, p2 -> {e2}, p3 -> {e4}, p4 -> {c4}, p5 -> {c5}.
define "EncountersOrConditions":
  if "HasObservation"
    then [Encounter]
    else [Condition]

// Conditionless `case`, same cross-resource branches, first-match semantics. LANG-3VL: a subject with
// no Observation (null/false condition) falls through to the Condition arm.
define "EncountersOrConditionsCase":
  case
    when exists [Observation] then [Encounter]
    else [Condition]
  end
