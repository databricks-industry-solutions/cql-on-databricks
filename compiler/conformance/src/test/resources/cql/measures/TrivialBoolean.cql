library TrivialBoolean version '1.0.0'

// A trivial measure: scalar/boolean defines with a small dependency DAG of ExpressionRefs, the
// "single-population, scalar-only measure" shape. It is driven end-to-end through the ground-truth
// harness (CONF-GROUND-TRUTH) and the round-trip (ELM->IR->ELM) target.

define "AgeThreshold": 18
define "PatientAgeForTest": 42

define "MeetsThreshold": PatientAgeForTest >= AgeThreshold
define "InInitialPopulation": MeetsThreshold and true
define "InDenominator": InInitialPopulation
define "InNumerator": InInitialPopulation and (PatientAgeForTest < 65)
