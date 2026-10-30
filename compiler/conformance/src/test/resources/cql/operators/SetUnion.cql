library SetUnion version '1.0.0'

// transformation-catalog.md section 28 (LANG-LIST): CQL list `union` has SET semantics (no
// duplicates), unlike SQL `UNION ALL` (bag). The sources are unioned, then deduplicated by element
// identity — so the shared element 3 appears once.

define "A": { 1, 2, 3 }
define "B": { 3, 4, 5 }
define "Union": "A" union "B"        // { 1, 2, 3, 4, 5 } — 3 is NOT duplicated
