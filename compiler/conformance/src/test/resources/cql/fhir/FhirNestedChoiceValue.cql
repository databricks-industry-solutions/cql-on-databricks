library FhirNestedChoiceValue version '1.0.0'

// ISOLATES the NESTED value[x] shape (LANG-CHOICE-MODEL): a `value[x]`
// choice reached through a `let`/navigation CHAIN, not an alias-rooted `ToValue(alias.value)`. Here
// `comp` is a `let` bound to `singleton from R.component`, so `comp.value as Quantity` has no alias root
// the by-name value[x] rewrites key on — `SqlQueryLowering.rewriteNestedChoiceValueX` resolves the base
// type (`Observation.Component`) via the query-scope resolver and dispatches `value as Quantity` to the
// `valueQuantity` slot. (A medication period's `timing.repeat.bounds as Interval<DateTime>` is the same rewrite,
// but at FHIR depth 5 — beyond the schema nesting cap — so it is tracked separately.)
//
// `component.value[x]` sits at FHIR depth 3 (within the maxNesting cap). o1's component reads 120 mm[Hg],
// o3's 135; o2/o4 have no component and are filtered by `where exists O.component`. The follow tests each
// component quantity against a 130 mm[Hg] threshold: o1 120<130 -> false, o3 135>130 -> true. So
// p1->[false], p2->[true], p3->[]. A missing dispatch would navigate a literal `value` field
// (field-not-found) or mis-slot. (The comparison form avoids `(… as Quantity).value`, a separate
// FHIR-primitive-`.value`-unwrap concern.)
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "HighComponentReadings":
  [Observation] O
    where exists O.component
    return O.componentAbove130()

define fluent function componentAbove130(obs Observation):
  obs R
    let comp: singleton from R.component
    return ( comp.value as Quantity ) > 130 'mm[Hg]'
