library FhirNestedQuantityValue version '1.0.0'

// ISOLATES nested quantity-VALUE reads (LANG-CHOICE-MODEL / LANG-QUANTITY):
// `( comp.value as Quantity ).value` where `comp` is a `let` = `singleton from R.component`. Exercises
// three coupled lowering fixes together, all needed by a medication period's `dose.value` /
// `quantity.value`:
//  - scope-aware body typing (the `let` chain types correctly, so operators/`.value` dispatch),
//  - `.value` on a System.Quantity reads the numeric FIELD (not the FHIR-primitive `.value` unwrap),
//  - `as Quantity` on an already-quantity slot NORMALIZES to `{value,unit}` (not a scalar promotion).
//
// o1's component quantity is 120, o3's 135; o2/o4 have no component (filtered by `where exists`). So
// p1->[120], p2->[135], p3->[]. Before these fixes the read returned the whole Quantity struct / failed
// to cast, so the diff caught it.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "ComponentQuantityHigh":
  [Observation] O
    where exists O.component
    return O.firstComponentQtyHigh()

define fluent function firstComponentQtyHigh(obs Observation):
  obs R
    let comp: singleton from R.component
    return ( comp.value as Quantity ).value > 130.0
