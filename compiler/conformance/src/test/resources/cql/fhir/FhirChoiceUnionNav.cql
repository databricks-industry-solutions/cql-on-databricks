library FhirChoiceUnionNav version '1.0.0'

// PERFORMANCE / plan-shape reproducer for the heterogeneous-union blow-up (LANG-CHOICE-RESOURCE).
//
// `[Observation] union [MedicationRequest]` is typed `List<Choice<Observation, MedicationRequest>>` —
// a heterogeneous Choice whose members are whole resource BODIES. The sibling FhirChoiceUnionLabsMeds
// fixture unions the SAME pair and lowers fine because it only EXPORTS the mixed list by resource
// identity (never reads a member field). This fixture reproduces the ONE extra thing a measure does: it
// consumes the union through a `with` relationship and NAVIGATES a field on the Choice element in the
// `such that` — the
//     … with ( A union B ) I such that <field-of>(I)
// shape, where the field is a Coalesce over a timing element that each arm names differently.
//
// The het union is realized (FrameLowering union-of-relations → tagChoiceArms → choiceStructOfTyped)
// as a discriminated struct where each arm's `__val` embeds the COMPLETE schema of BOTH resource
// types and casts null to the full nested struct for the dead arm (ColumnLowering.dataTypeOf →
// ResourceStruct.structType). Navigating a field then dispatches per arm over that giant struct. That
// is the encoding whose plan explodes when materialized (blocker #8). Isolated from the rest of a measure
// (no value sets, no medication reference join, no correlated-exists-in-disjunction) so the generated
// plan is small enough to read. Requires grain-first + the heterogeneous-Choice union path.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

// The heterogeneous resource union, as its OWN define (whole body) — the shape that routes to the
// grain-first union-of-relations / Choice-tagged relation path (FrameLowering line ~345).
define "MixedInterventions": [Observation] union [MedicationRequest]

// Consume it as a query source and NAVIGATE a field off the Choice element (`I.status`, present on
// both arms), returning it. This is the incremental factor over FhirChoiceUnionLabsMeds's identity
// export: reading a member field forces per-arm dispatch over the full-resource discriminated struct.
define "MixedStatuses":
  "MixedInterventions" I
    return I.status
