library Quantities version '1.0.0'

// Quantities carry a UNIT; arithmetic and comparison require UCUM conversion
// (LANG-QUANTITY) — comparing mg to g is a conversion, not a raw number compare. A
// quantity is encoded as a (value, unit) struct. These fixtures pin the surface a UCUM conversion must satisfy.

define "WeightInKg": 70 'kg'
define "HeightInCm": 180 'cm'
define "QuantityEqualAcrossUnits": 1 'kg' = 1000 'g'
define "QuantityLessAcrossUnits": 500 'mg' < 1 'g'
define "QuantitySumAcrossUnits": 1 'kg' + 500 'g'
define "QuantityGreater": 2 'm' > 150 'cm'
