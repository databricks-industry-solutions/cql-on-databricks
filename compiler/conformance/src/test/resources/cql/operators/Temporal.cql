library Temporal version '1.0.0'

// Precision-aware date/time and Measurement-Period interval math (LANG-INTERVAL). Comparisons
// can legitimately return null when precision is insufficient to decide; the interval relations
// (during/overlaps/before) run over DateTime points. (Phase 3, temporal wave.)

define "YearOnly": @2012
define "YearMonth": @2012-06
define "FullDate": @2012-01-15
define "DateTimeVal": @2012-01-15T10:30:00

define "SameYear": @2012 = @2012
define "DiffYear": @2012 = @2013
define "UncertainEq": @2012 = @2012-06
define "YearLess": @2012 < @2013
define "DateLess": @2012-01-15 < @2012-03-20
define "DateBefore": @2012-01-15 before @2012-03-20
define "DateAfter": @2012-03-20 after @2012-01-15

define "MeasurementPeriod": Interval[@2012-01-01, @2012-12-31]
define "DateDuringMP": @2012-06-15 during Interval[@2012-01-01, @2012-12-31]
define "DateOutsideMP": @2013-06-15 during Interval[@2012-01-01, @2012-12-31]
define "MPStart": start of Interval[@2012-01-01, @2012-12-31]
