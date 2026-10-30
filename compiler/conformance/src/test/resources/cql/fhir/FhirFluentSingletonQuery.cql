library FhirFluentSingletonQuery version '1.0.0'

// ISOLATES a fluent singleton-query shape (LANG-CHOICE-MODEL / scope-aware clause typing):
// a fluent function whose parameter is a SINGLETON resource, whose body is a QUERY over that parameter,
// called on an OUTER query alias. When inlined, the inner query's source is an `AliasRef` to the outer
// singleton alias. The clause `typeOf` is context-free, so that outer-alias reference used to type as
// `Any` — which made the inner singleton-source query lower through the Array path's list `transform`
// (a datatype mismatch on the resource STRUCT). `SqlQueryLowering.combined` now augments `typeOf` with
// each source alias's ELEMENT type, so the inner query is correctly a singleton source.
//
// `describe(E)` follows the singleton Encounter `E` through a one-element query and returns its status;
// per-patient the result mirrors `[Encounter] E return E.status`. e1(finished)/e1b(arrived) for p1,
// e2(planned) for p2, e4(finished) for p3.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "EncounterStates":
  [Encounter] E
    return E.describe()

define fluent function describe(enc Encounter):
  enc R
    let st: R.status
    return st
