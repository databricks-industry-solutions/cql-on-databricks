library MultiSource version '1.0.0'

// Multi-source queries (LANG-QUERY-MULTISOURCE): multiple sources form a Cartesian/relational
// combination and every alias enters scope. Over System (Array-encoded) lists this is nested
// iteration, so a `where` correlating the aliases falls out for free (no join). The query's
// element is the `return` shape, defaulting to the alias Tuple the translator synthesizes — the
// tuple encoding (a struct per row).

// Cartesian product, projected to a scalar; `return all` keeps every (ordered) pair.
define "Sums": from ({ 1, 2 }) X, ({ 10, 20 }) Y return all X + Y
// Correlated filter referencing both aliases (the nested-iteration `where`).
define "Correlated": from ({ 1, 2, 3 }) X, ({ 2, 3, 4 }) Y where X = Y return X
// Explicit tuple shaping → the struct-per-row tuple encoding.
define "Tuples": from ({ 1, 2 }) X, ({ 10, 20 }) Y return all Tuple { x: X, y: Y }
// Implicit tuple (no return) → the synthesized alias Tuple, deduped (return defaults to distinct).
define "TupleDefault": from ({ 1, 2 }) X, ({ 10, 20 }) Y
// Three sources → n-ary Cartesian product.
define "ThreeSource": from ({ 1, 2 }) X, ({ 10, 20 }) Y, ({ 100 }) Z return all X + Y + Z
