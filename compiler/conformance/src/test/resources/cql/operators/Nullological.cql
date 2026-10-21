library Nullological version '1.0.0'

// Null-handling operators. Note the empty-vs-null-list deferral (LANG-LIST-NULL): for now
// empty and null lists are conflated, but Coalesce / IsNull over lists are exactly where
// that is false and must be decided by differential test against the reference.

define "CoalesceFirstNonNull": Coalesce(null, 2, 3)
define "CoalesceAllNull": Coalesce(null, null)
define "IsNullOnNull": IsNull(null)
define "IsNullOnValue": IsNull(5)
define "NullPropagationAdd": 1 + null
