library Arithmetic version '1.0.0'

// Arithmetic operators. Division semantics, truncated division, and modulo are
// CQL-runtime-defined on edge cases (e.g. divide by zero -> null per spec), which become
// per-row results, never job failures (LANG-RUNTIME-COND, CORE-ISOLATE-ANOMALY).

define "Sum": 2 + 3
define "Difference": 10 - 4
define "Product": 6 * 7
define "Quotient": 22 / 7
define "IntegerQuotient": 22 div 7
define "Remainder": 22 mod 7
define "PoweredUp": 2 ^ 10
define "Negated": -5
define "NullPropagation": 1 + null
define "DivideByZero": 1 / 0
