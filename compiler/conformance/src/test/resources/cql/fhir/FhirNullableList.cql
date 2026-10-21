library FhirNullableList version '1.0.0'

// ARCH-LIST-NULL-SIDECAR / LANG-LIST-NULL-STATE: a list-valued `if` with a NULL else branch — the
// common measure "a list, or null when a condition fails" shape. At relation grain the list's
// null/empty/non-empty state is recovered WITHOUT collecting to subject grain: empty-vs-non-empty
// from row presence, null-ness from a one-bit `(__subject_id, __is_null)` sidecar derived from the
// condition (the realized escape hatch reserved by LANG-LIST-NULL). The grain-first conditional-union
// path builds the sidecar and renders null at the boundary; the boxed `when/otherwise` baseline carries
// a null array cell. Both must agree element-for-element (CONF-GATE-SELFDIFF on/off) AND match the Java
// reference (CONF-GATE-MEASURE) — the tripwire against a silent null→empty regression.
//
// With the shared FHIR data this exercises ALL THREE states:
//   p1,p2 — Condition + Encounter   -> non-empty list of statuses
//   p3    — no Condition            -> NULL list (the else branch)
//   p4,p5 — Condition, no Encounter -> EMPTY list (the then branch, zero rows)
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "MaybeStatuses":
  if exists [Condition]
    then [Encounter] E return E.status
    else null

// The observing operators that DISTINGUISH null from empty (LANG-LIST-NULL-STATE) — they read the
// nullity sidecar, never box the list. `is not null` is `Not(IsNull(...))`, so both exercise the path.
define "StatusesIsNull": "MaybeStatuses" is null
define "StatusesIsNotNull": "MaybeStatuses" is not null
