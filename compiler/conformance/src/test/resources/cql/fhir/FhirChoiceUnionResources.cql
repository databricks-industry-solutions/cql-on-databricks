library FhirChoiceUnionResources version '1.0.0'

// LANG-CHOICE-RESOURCE (extends ARCH-COND-UNION / LANG-CHOICE): a `union` of two retrieves over
// DIFFERENT FHIR RESOURCE types. `[Encounter] union [Condition]` is typed by the translator as
// `List<Choice<Encounter, Condition>>` — a heterogeneous Choice whose members are whole resource
// BODIES (distinct schemas), not the scalar element types covered by FhirConditionalUnionHet.
// The union must keep each arm Relation-encoded and tag its rows with the active Choice member so
// the two resource schemas align into one Choice-tagged relation (no boxing). At the result boundary
// each row exports by resource identity (__resource_type/__resource_id, DATA-EXPORT), so the mixed
// list must match the Java reference element-for-element (CONF-GATE-MEASURE).
//
// Differential-only: there is no boxed baseline for a whole-resource heterogeneous union, so this is
// NOT in the on/off self-diff (like FhirConditionalUnionHet).
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

// Whole-resource heterogeneous union. Per patient the list interleaves Encounter rows then Condition
// rows: p1 -> {e1,e1b,c1,c2}, p2 -> {e2,c3}, p3 -> {e4}, p4 -> {c4}, p5 -> {c5}.
define "EncountersOrConditions": [Encounter] union [Condition]
