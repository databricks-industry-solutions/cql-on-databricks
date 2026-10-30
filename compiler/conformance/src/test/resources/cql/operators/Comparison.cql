library Comparison version '1.0.0'

// Equality (=) vs equivalence (~) are DISTINCT operators and differ around nulls and
// precision (LANG-3VL); they must never be collapsed. Comparisons follow CQL 3VL, so a
// comparison involving null is null, not false.

define "IntEqual": 1 = 1
define "IntNotEqual": 1 != 2
define "DecimalLess": 1.5 < 2.0
define "IntGreater": 5 > 3
define "IntLessOrEqual": 3 <= 3
define "IntGreaterOrEqual": 4 >= 5
define "StringEquivalent": 'abc' ~ 'abc'
define "StringEqual": 'abc' = 'abc'
define "EqualityWithNull": 1 = null
define "EquivalenceWithNull": 1 ~ null

// String equivalence (LANG-EQUIV) is case-insensitive AND normalizes whitespace PER CHARACTER: the
// spec's "all whitespace characters are treated as equivalent" over `[ \t\r\n]`. Runs are NOT
// collapsed and the ends are NOT trimmed.
define "StringEquivIgnoresCase": 'Diabetes' ~ 'diabetes'
define "StringEquivTabIsSpace": 'Diabetes\tmellitus' ~ 'Diabetes mellitus'
define "StringEquivRunNotCollapsed": 'Diabetes mellitus' ~ 'Diabetes  mellitus'
define "StringEquivNotTrimmed": 'Diabetes ' ~ 'Diabetes'
