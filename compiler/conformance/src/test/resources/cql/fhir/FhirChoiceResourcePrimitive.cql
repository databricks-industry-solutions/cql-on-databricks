library FhirChoiceResourcePrimitive version '1.0.0'

// LANG-CHOICE-RESOURCE (extends ARCH-COND-UNION / LANG-CHOICE): a list-valued `if` whose
// branches mix a FHIR RESOURCE body with a PRIMITIVE (or tuple) — the most heterogeneous Choice shape.
// FhirConditionalUnionHet mixed two SCALAR element types (string vs integer); here one arm is a whole
// `Encounter` struct and the other is an `Integer` / `String` / anonymous `Tuple`, so the Choice-tagged
// relation must align a deep resource schema against a flat scalar column. Gated vs the Java reference.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "HasObservation": exists [Observation]

// Resource body vs Integer: Choice<Encounter, Integer>.
//   p1/p2/p3 -> their Encounters; p4/p5 -> [1] (one Integer per Condition: c4 -> [1], c5 -> [1]).
define "EncountersOrCount":
  if "HasObservation"
    then [Encounter]
    else ([Condition] C return 1)

// Resource body vs String: Choice<Encounter, String>.
define "EncountersOrLabel":
  if "HasObservation"
    then [Encounter]
    else ([Condition] C return 'no-observation')

// Resource body vs anonymous Tuple: Choice<Encounter, Tuple{ kind String, n Integer }>. The tuple
// fields are literals on purpose — `C.id` would surface an UNRELATED FHIR `.id` navigation quirk (the
// Java reference renders a resource `.id` as the relative reference `Condition/c4`, the engine as the
// bare `c4`), which is orthogonal to the Choice<resource, tuple> alignment this fixture exercises.
define "EncountersOrTuple":
  if "HasObservation"
    then [Encounter]
    else ([Condition] C return Tuple { kind: 'condition', n: 1 })
