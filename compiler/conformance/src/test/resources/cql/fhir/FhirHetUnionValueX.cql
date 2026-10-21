library FhirHetUnionValueX version '1.0.0'

// ISOLATES the #8 fix: a HETEROGENEOUS resource `union` used INLINE as a query SOURCE, with a
// value[x] navigated on ONLY ONE arm plus a plain field on the OTHER, Coalesced across them.
//
// Unlike FhirChoiceUnionNav (which unions in a DEFINE body and navigates a field common to both arms,
// routing through the define-body handle path), this unions INLINE in the query source and navigates a
// value[x] present on only one member — so it exercises the NEW code:
//   - QueryLowering.aliasedSource → hetUnionAlias  (realize the union as ONE demand-pruned discriminated
//     Choice struct, each member slot narrowed to the navigated fields);
//   - QueryLowering.rewriteHetUnionNav  (dispatch navigation PER MEMBER over that struct — the value[x]
//     slot on the Observation arm, the plain field on the MedicationRequest arm — since the single-type
//     value[x] rewrite keys on ONE resource type per alias and cannot).
// This is the shape `Coalesce(I.performed.performedSpan(), I.authoredOn.authoredSpan())`, where
// `performed` is a value[x] only on the Procedure arm and `authoredOn` only on the request arms.
//
// `Observation.effective` is a value[x] (effective[x] = dateTime|Period|…); `MedicationRequest.authoredOn`
// is a plain dateTime present only on the MedicationRequest arm. Coalesce picks whichever arm the row is.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "MixedEffectiveOrAuthored":
  ([Observation] union [MedicationRequest]) I
    return Coalesce(I.effective as FHIR.dateTime, I.authoredOn)
