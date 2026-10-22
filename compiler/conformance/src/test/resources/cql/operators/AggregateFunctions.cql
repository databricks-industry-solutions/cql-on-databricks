library AggregateFunctions version '1.0.0'

// LANG-AGGREGATE-FUNCTIONS: the CQL aggregate functions (CQL Reference, Aggregate Functions). Each ignores null
// elements. All but Count, AllTrue and AnyTrue are null for a null list, an empty list, or a list of nulls.
// Expected values are in the comments.

// Count: non-null elements only; a null list counts 0.
define "CountIgnoresNulls": Count({ 1, null, 3 })                                   // 2
define "CountNullList": Count(null as List<Integer>)                                // 0
define "CountEmpty": Count(List<Integer>{})                                         // 0

// Sum
define "SumIntegers": Sum({ 1, 2, null, 4 })                                        // 7
define "SumDecimals": Sum({ 1.5, 2.25 })                                            // 3.75
define "SumEmpty": Sum(List<Integer>{})                                             // null
define "SumAllNull": Sum({ null as Integer, null as Integer })                      // null
define "SumNullList": Sum(null as List<Decimal>)                                    // null
// Quantities convert to the coarsest unit in the list (LANG-QUANTITY), in either order.
define "SumQuantitySameUnit": Sum({ 1 'mg', 2 'mg' })                               // 3 'mg'
define "SumQuantityMixedUnits": Sum({ 1 'g', 500 'mg' })                            // 1.5 'g'
define "SumQuantityCoarseLast": Sum({ 500 'mg', 1 'g' })                            // 1.5 'g'

// Min and Max compare with CQL ordering.
define "MaxIntegers": Max({ 3, null, 7, 5 })                                        // 7
define "MinDecimals": Min({ 2.5, 1.25, null })                                      // 1.25
define "MaxStrings": Max({ 'b', 'a', 'c' })                                         // 'c'
define "MaxDates": Max({ @2020-01-15, @2021-03-01, null })                          // @2021-03-01
define "MinDateTimes": Min({ @2020-01-15T10:00:00, @2020-01-15T09:30:00 })          // @2020-01-15T09:30:00
define "MaxTimes": Max({ @T10:00, @T23:15 })                                        // @T23:15
define "MaxEmpty": Max(List<Date>{})                                                // null
// Mixed precision: @2012 vs @2012-05-01 is uncertain, so the running value stays; @2013-05-01 is decided later.
define "MaxMixedPrecisionUncertain": Max({ @2012, @2012-05-01 })                    // @2012
define "MinMixedPrecisionUncertain": Min({ @2012-05-01, @2012 })                    // @2012-05-01
define "MaxMixedPrecisionDecided": Max({ @2012, @2013-05-01 })                      // @2013-05-01
// A quantity Min/Max returns the element itself, in its own unit.
define "MaxQuantityMixedUnits": Max({ 2000 'mg', 1 'g' })                           // 2000 'mg'
define "MinQuantityMixedUnits": Min({ 1 'g', 500 'mg' })                            // 500 'mg'

// Avg
define "AvgDecimals": Avg({ 1.0, 2.0, null, 6.0 })                                  // 3.0
define "AvgIntegers": Avg({ 1, 2 })                                                 // 1.5
define "AvgQuantityMixedUnits": Avg({ 1 'g', 500 'mg' })                            // 0.75 'g'
define "AvgEmpty": Avg(List<Decimal>{})                                             // null

// Median: an even count averages the middle two.
define "MedianOdd": Median({ 3.0, 1.0, 2.0 })                                       // 2.0
define "MedianEven": Median({ 4.0, 1.0, 3.0, 2.0 })                                 // 2.5
define "MedianIgnoresNulls": Median({ null, 5.0, 1.0 })                             // 3.0
define "MedianQuantity": Median({ 1 'cm', 3 'cm', 2 'cm' })                         // 2 'cm'

// Mode: a tie goes to the least element.
define "ModeIntegers": Mode({ 2, 1, 2, 3, null, null, null })                       // 2
define "ModeTieLeast": Mode({ 3, 1, 3, 1 })                                         // 1
define "ModeStrings": Mode({ 'b', 'a', 'b' })                                       // 'b'
define "ModeDates": Mode({ @2020-01-01, @2020-01-02, @2020-01-02 })                 // @2020-01-02

// Variance and standard deviation: sample (n - 1) and population (n).
define "VarianceDecimals": Variance({ 2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0 })     // 4.57142857
define "PopulationVarianceDecimals": PopulationVariance({ 2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0 }) // 4.0
define "StdDevDecimals": StdDev({ 2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0 })         // 2.13808993
define "PopulationStdDevDecimals": PopulationStdDev({ 2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0 }) // 2.0
define "VarianceIgnoresNulls": Variance({ 1.0, null, 3.0 })                         // 2.0
define "VarianceSingle": Variance({ 5.0 })                                          // null
define "PopulationVarianceSingle": PopulationVariance({ 5.0 })                      // 0.0
define "VarianceQuantity": Variance({ 1 'cm', 2 'cm', 3 'cm' })                     // 1.0 'cm2'
define "StdDevQuantity": StdDev({ 1 'cm', 2 'cm', 3 'cm' })                         // 1.0 'cm'

// Product and GeometricMean
define "ProductIntegers": Product({ 2, 3, 4 })                                      // 24
define "ProductDecimals": Product({ 1.5, 2.0 })                                     // 3.0
define "ProductIgnoresNulls": Product({ 2, null, 5 })                               // 10
define "GeometricMeanDecimals": GeometricMean({ 2.0, 8.0 })                         // 4.0
define "GeometricMeanEmpty": GeometricMean(List<Decimal>{})                         // null

// AllTrue is true and AnyTrue false for a null or empty list.
define "AllTrueIgnoresNulls": AllTrue({ true, null })                               // true
define "AllTrueOneFalse": AllTrue({ true, false, null })                            // false
define "AllTrueEmpty": AllTrue(List<Boolean>{})                                     // true
define "AllTrueNullList": AllTrue(null as List<Boolean>)                            // true
define "AnyTrueIgnoresNulls": AnyTrue({ false, null })                              // false
define "AnyTrueOneTrue": AnyTrue({ null, true })                                    // true
define "AnyTrueEmpty": AnyTrue(List<Boolean>{})                                     // false
define "AnyTrueNullList": AnyTrue(null as List<Boolean>)                            // false
