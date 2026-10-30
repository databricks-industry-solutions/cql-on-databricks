library FhirConditionalUnionHet version '1.0.0'

// ARCH-COND-UNION Phase B — HETEROGENEOUS list-valued `if`/`case`: the branches return DIFFERENT
// element types. The cqframework translator types `if C then List<A> else List<B>` as a scalar
// Choice<List<A>, List<B>> (each branch wrapped in `As(query, choiceTypes = [List<A>, List<B>])`).
// The union rewrite keeps each branch Relation-encoded and TAGS its elements with the active Choice
// member (catalog section 10), so the per-branch schemas align into one `List<Choice<A,B>>` relation
// — no boxing. `JsonNormalize` unwraps each Choice to its active value at the boundary, so the result
// matches the Java reference's mixed list element-for-element (CONF-GATE-MEASURE).
//
// Differential-only (vs the Java reference): there is NO boxed baseline for a heterogeneous *list*
// conditional (the boxed `when/otherwise` path cannot build a Choice over list branches and fails Spark
// analysis), so this fixture is NOT in the on/off self-diff (unlike the same-type FhirConditionalUnion).
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "HasCondition": exists [Condition]

// Heterogeneous `if`: then is List<EncounterStatus> (a FHIR code → string element), else is
// List<Integer>. Result element type is Choice<EncounterStatus, Integer>.
define "StatusesOrCount":
  if "HasCondition"
    then ([Encounter] E return E.status)
    else ([Observation] O return 1)

// Heterogeneous conditionless `case`, same shape — exercises the first-match gating with Choice tags.
define "StatusesOrCountCase":
  case
    when exists [Condition] then ([Encounter] E return E.status)
    else ([Observation] O return 1)
  end
