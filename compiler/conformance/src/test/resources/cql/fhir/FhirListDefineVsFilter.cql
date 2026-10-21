library FhirListDefineVsFilter version '1.0.0'

// PHASE-6 step 2 (DATA-SKIP-UNFILTERED, ARCH-PUSHDOWN, LANG-TERMINOLOGY): the value-set fold composing
// PER-CONSUMER over a Relation define source. A bare define `AllConditions` is a Relation VIEW over the
// [Condition] scan; a downstream consumer's `where C.code in "Diabetes"` is realized as the row-level
// broadcast semi-join over that view (keyed by the define's __ordinal) — the same membership the
// terminology pushdown folds into a direct retrieve, now composing over the shared broad define instead
// of forcing a collect_list barrier. (The optimizer's retrieve-level fold does not fire on an
// ExpressionRef source, so optimized ≡ unoptimized here — the self-diff stays a meaningful gate.)
//
// Data discriminates: p1 has c1 (non-Diabetes, filtered OUT) + c2 (Diabetes) → [Patient/p1]; p2 has
// only a non-Diabetes condition → []; p3 has none → []. A broken semi-join would keep c1.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'

context Patient

define "AllConditions": [Condition]

define "DiabetesSubjects":
  AllConditions C where C.code in "Diabetes" return C.subject.reference
