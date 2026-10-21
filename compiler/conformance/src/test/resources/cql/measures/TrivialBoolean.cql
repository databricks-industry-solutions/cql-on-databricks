library TrivialBoolean version '1.0.0'

// PHASE-0 trivial measure (PHASE-0 exit). Scalar/boolean defines with a small dependency
// DAG of ExpressionRefs — exactly the "single-population, scalar-only measure"
// shape Phase 1 will evaluate set-at-a-time and diff against the JS reference. In Phase 0
// it is the measure driven end-to-end through the ground-truth harness (CONF-GROUND-TRUTH)
// and the round-trip (ELM->IR->ELM) target.

define "AgeThreshold": 18
define "PatientAgeForTest": 42

define "MeetsThreshold": PatientAgeForTest >= AgeThreshold
define "InInitialPopulation": MeetsThreshold and true
define "InDenominator": InInitialPopulation
define "InNumerator": InInitialPopulation and (PatientAgeForTest < 65)
