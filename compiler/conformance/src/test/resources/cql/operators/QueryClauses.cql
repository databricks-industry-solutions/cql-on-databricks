library QueryClauses version '1.0.0'

// List set operators (LANG-LIST) and query shaping/ordering/aggregate clauses (LANG-QUERY),
// over System lists (Array-encoded). Set ops are equivalence-based; sort rewrites order;
// return distinct dedups by equivalence; aggregate folds in order. (Phase 4b.1.)

define "UnionList": { 1, 2, 3 } union { 3, 4, 5 }
define "IntersectList": { 1, 2, 3 } intersect { 2, 3, 4 }
define "ExceptList": { 1, 2, 3, 4 } except { 2, 4 }
define "UnionTyped": { 1, 2 } union { 'a' }
define "SortedAsc": ({ 3, 1, 2 }) N return N sort asc
define "SortedDesc": ({ 3, 1, 2 }) N return N sort desc
define "DistinctList": ({ 1, 2, 2, 3, 3, 3 }) N return distinct N
define "AggregateSum": ({ 1, 2, 3, 4 }) N aggregate Result starting 0: Result + N
