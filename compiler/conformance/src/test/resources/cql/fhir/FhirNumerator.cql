library FhirNumerator version '1.0.0'

// PHASE-5: a realistic measure-shaped numerator combining BOTH retrieve pushdowns on one broad
// retrieve — a value-set code filter AND a Measurement-Period date filter. The terminology and
// date-range pushdown rules each fold their conjunct into the single `[Condition]` retrieve, so the
// OPTIMIZED kernel shows scan pruning (the retrieve carries `codes` + `dateProperty`/`dateRange`,
// the `where` gone — golden-plan evidence, CONF-GOLDEN-PLAN), while the UNOPTIMIZED form keeps a
// bare retrieve + a two-conjunct `where` (membership semi-join + date column filter). The self-diff
// proves the two are result-identical (CONF-GATE-SELFDIFF); physical data-skipping on the pushed
// filters is then Catalyst/Photon's job (ARCH-PUSHDOWN: domain rewrite here, physical pruning below).
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'

parameter "Measurement Period" Interval<DateTime> default Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]

context Patient

define "DiabetesInMP":
  [Condition] C
    where C.code in "Diabetes" and C.recordedDate during "Measurement Period"
    return C.subject.reference
