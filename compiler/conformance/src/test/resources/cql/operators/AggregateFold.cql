library AggregateFold version '1.0.0'

// transformation-catalog.md section 29 (LANG-QUERY-AGGREGATE): `aggregate R starting <init>: <body>`
// is a LEFT FOLD across the ordered elements, lowered to Spark's `aggregate` higher-order function
// (a scalar result, no Relation realization).

define "Sum": ({ 1, 2, 3, 4 }) N aggregate R starting 0: R + N      // 10

// A DECIMAL accumulator: decimal addition changes the SQL precision and scale, and Spark's `aggregate`
// requires the body to keep the seed's type, so the fold is pinned to CQL's Decimal (DECIMAL(28,8)).
define "DecimalSum": ({ 1.5, 2.25, 30.125 }) D aggregate R starting 0.0: R + D * 2   // 67.75
