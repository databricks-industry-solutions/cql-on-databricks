library FhirChoiceValueOffDefine version '1.0.0'

// LANG-CHOICE-MODEL: a `value[x]` read off a DEFINE that holds one resource, not off a query alias. The
// define's type is the profile, so `is T`, `is null` and `as T` on its `value` must dispatch to the typed
// slots (`valueQuantity`, `valueString`, ...) exactly as they do on an alias (`rewriteTypedChoiceValueX`).
// `is null` is true only when EVERY slot is empty, and `as Quantity` of an absent value is null, not a
// quantity with null fields, so the comparison is null too (CQL spec, Types, "Choice Types"; FHIRHelpers
// `ToQuantity(null)` is null).
//
// DATA (`data/qicore/Observation.ndjson`, profile qicore-observation-lab, one result each, so `First` is it): v1 a quantity of
// 4.2 %, v2 a quantity of 7.5 %, v3 a string, v4 no value. q3's Observation claims no profile, so the
// profile-only retrieve keeps it too (LANG-RETRIEVE-PROFILE), and it has no value either. Patients without a
// result have a null "Only Result", so every define below is null for them.
//
// CONF-REF-DEVIATION: on v4 and q3 the reference answers null for `is Quantity` and `is String`. The CQL spec says
// false: "the expression null is Integer returns false" (CQL Reference, Types Operators, "Is"). We follow
// the spec; `Corpus` declares those values.
using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "Only Result":
  First([LaboratoryResultObservation] Result
    sort by id)

// The `is` tests skip patients with no result, so only v4 and q3 (a result with no value) test `is` on a null.
define "Result Is Quantity":
  if "Only Result" is null then null else "Only Result".value is Quantity

define "Result Is String":
  if "Only Result" is null then null else "Only Result".value is String

define "Result Is Null":
  "Only Result".value is null

define "Result Quantity":
  "Only Result".value as Quantity

define "Result Below Five Percent":
  ("Only Result".value as Quantity) < 5 '%'
