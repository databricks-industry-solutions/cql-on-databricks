library SingletonSource version '1.0.0'

// transformation-catalog.md section 17 (LANG-QUERY): a query whose SOURCE is a single value (not a
// list) evaluates to that one value — NOT a one-element array. The alias binds the scalar and the
// `return` shape projects directly; a `where` that fails yields null (not an empty list). The lowering
// is the scalar dual of the Relation list path — no `posexplode`/`collect_list` enters the plan.

define "Scaled":  (40 + 2) v return v * 10        // singleton source 42 -> 420 (scalar)
define "Kept":    (42) v where v > 10  return v    // 42
define "Dropped": (42) v where v > 100 return v    // null (NOT an empty list)
