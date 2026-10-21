library IntervalUnbounded version '1.0.0'

// transformation-catalog.md section 31 (LANG-INTERVAL): a null interval boundary is open-ended; the
// Allen-relation algebra reads it as a +/- infinity sentinel, so relations don't fail on a null bound.

define "OngoingDuring":   100 during Interval[1, null]                  // true: unbounded high reads as +inf
define "BoundedDuring":   100 during Interval[1, 10]                    // false (bounded high)
define "OngoingIncludes": Interval[1, null] includes Interval[50, 60]   // true (unbounded high)
