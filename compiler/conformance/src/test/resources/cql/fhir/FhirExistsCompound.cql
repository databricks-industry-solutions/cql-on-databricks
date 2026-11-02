library FhirExistsCompound version '1.0.0'

// A compound `where` with a value-set membership conjunct INSIDE an `exists`. The `exists` fast path
// handles only a single `InValueSet` or a membership-free predicate; `code in vs AND <other>` falls
// through to the general Relation realization, which splits the membership semi-join from the residual
// filter (the same handling the non-exists query path uses, PROC-DRY).
//
// With pushdown ON both conjuncts fold into the [Condition] scan (whereOpt empty → fast path);
// with pushdown OFF the compound where falls through to relationExists+applyWhere — the self-diff
// proves the two agree, exactly as FhirNumerator does for the non-exists twin.
//
// Data discriminates: p1 has c2 (Diabetes, recordedDate 2023 in MP) → true; c1 (p1, non-Diabetes) and
// c3 (p2, non-Diabetes) do not qualify; p2/p3 → false.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'

parameter "Measurement Period" Interval<DateTime> default Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]

context Patient

define "HasDiabetesInMP":
  exists ([Condition] C where C.code in "Diabetes" and C.recordedDate during "Measurement Period")
