library Logic3VL version '1.0.0'

// Three-valued logic (LANG-3VL): CQL booleans are true | false | null, and null
// propagation is the single most common source of subtle bugs. A null condition is
// "not true". These defines pin the truth tables the Bool3 library realizes.

define "TrueAndTrue": true and true
define "TrueAndFalse": true and false
define "TrueAndNull": true and null
define "FalseAndNull": false and null
define "TrueOrNull": true or null
define "FalseOrNull": false or null
define "NullOrNull": null or null
define "NotTrue": not true
define "NotNull": not null
define "TrueXorFalse": true xor false
define "NullXorTrue": null xor true
define "TrueImpliesFalse": true implies false
define "NullImpliesTrue": null implies true
define "FalseImpliesNull": false implies null
