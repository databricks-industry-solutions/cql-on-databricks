library Lists version '1.0.0'

// Lists are ORDERED and allow DUPLICATES (LANG-LIST). The default physical encoding is
// exploded with a mandatory __ordinal column (DATA-LIST-ENCODING) — the difference
// between passing and silently failing First/Last/indexed access. These fixtures pin the
// ordering-sensitive operators.

define "NumberList": { 1, 2, 3, 4, 5 }
define "ListWithDuplicates": { 1, 1, 2, 2, 3 }
define "NonEmptyExists": exists { 1, 2 }
define "EmptyExists": exists ({})
define "FirstElement": First({ 10, 20, 30 })
define "LastElement": Last({ 10, 20, 30 })
define "IndexedAccess": ({ 10, 20, 30 })[1]
define "Membership": 3 in { 1, 2, 3 }
define "ListCount": Count({ 1, 2, 3 })
define "Singleton": singleton from { 42 }

// `distinct` and `in` use EQUALITY semantics (LANG-EQUIV), NOT equivalence — so they are CASE-SENSITIVE
// over strings even though `~` is not. These pin that boundary in both directions: a fix to `~` must
// not leak into them, and equivalence-based membership stays a translator compatibility option we do not enable.
define "DistinctIsCaseSensitive": distinct { 'Diabetes', 'diabetes' }
define "DistinctDedupesExactMatches": distinct { 'a', 'b', 'a' }
define "ReturnDistinctIsCaseSensitive": ({ 'Diabetes', 'diabetes' }) X return distinct X
define "MembershipIsCaseSensitive": 'diabetes' in { 'Diabetes' }
define "MembershipExact": 'Diabetes' in { 'Diabetes' }
define "MembershipAbsent": 'asthma' in { 'Diabetes' }
define "MembershipDecimalIsExact": 1.001 in { 1.0 }

// Empty-list behavior (LANG-LIST-NULL / 01 section 2.1 deferral): pin that empty lists match the
// reference for the ordering-sensitive and aggregate operators (First/Last/indexer -> null, Count -> 0).
define "EmptyListFirst": First({})
define "EmptyListLast": Last({})
define "EmptyListCount": Count({})
define "EmptyListIndexer": ({ })[0]
