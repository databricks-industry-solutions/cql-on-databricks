library IntervalOpenClosed version '1.0.0'

// transformation-catalog.md section 32 (LANG-INTERVAL): open boundaries normalize to closed via
// discrete successor/predecessor (open low -> low+1, open high -> high-1) before the Allen relations
// run, so every relation works on closed bounds.

define "ClosedStart": start of Interval[1, 10]      // 1
define "OpenStart":   start of Interval(1, 10]       // 2  (open low: 1 + 1)
define "OpenEnd":     end of Interval[1, 10)         // 9  (open high: 10 - 1)
