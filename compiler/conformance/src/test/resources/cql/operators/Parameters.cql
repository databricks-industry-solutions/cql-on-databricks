library Parameters version '1.0.0'

// Parameters are constants for a run — "literal/parameter scalars as broadcasts" (LANG-PARAMETER).
// With no engine-supplied value, a parameter binds to its declared default;
// Normalize (ParameterResolver) substitutes the default before lowering. The reference engine,
// given no parameter values, uses the same defaults — so these defines diff clean.

parameter "MeasurementYear" default 2024
parameter "Threshold" default 3.5

define "YearPlusOne": MeasurementYear + 1
define "AboveThreshold": 4.0 > Threshold
define "ThresholdNullCheck": IsNull(Threshold)
