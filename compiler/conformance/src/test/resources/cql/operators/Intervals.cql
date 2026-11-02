library Intervals version '1.0.0'

// Intervals are typed and may be open/closed at either boundary; boundary semantics are
// part of every interval operator (LANG-INTERVAL). The Allen-relation algebra
// (during/overlaps/before/meets/...) is implemented precision-aware in the library, never
// on native Spark date ops. These fixtures use integers.

define "ClosedInterval": Interval[1, 10]
define "OpenInterval": Interval(1, 10)
define "LeftOpenInterval": Interval(1, 10]
define "IntervalWidth": width of Interval[1, 10]
define "IntervalStart": start of Interval[1, 10]
define "IntervalEnd": end of Interval[1, 10]
define "PointDuring": 5 during Interval[1, 10]
define "IntervalsOverlap": Interval[1, 5] overlaps Interval[3, 8]
define "IntervalBefore": Interval[1, 2] before Interval[5, 6]
define "IntervalIncludes": Interval[1, 10] includes Interval[3, 4]
