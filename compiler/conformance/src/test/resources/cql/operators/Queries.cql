library Queries version '1.0.0'

// Query clauses (LANG-QUERY): a query iterates a source bound to an alias, filters with `where`,
// and shapes with `return`. At the System level (no FHIR) this isolates the query mechanics.
define "EvensOnly": ({ 1, 2, 3, 4, 5, 6 }) N where N mod 2 = 0
define "Doubled": ({ 1, 2, 3 }) N return N * 2
define "WhereThenCount": Count(({ 5, 10, 15, 20 }) N where N > 10)
