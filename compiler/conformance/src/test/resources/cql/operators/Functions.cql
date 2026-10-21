library Functions version '1.0.0'

// User-defined functions. CQL forbids recursion (LANG-FUNCTION): a function reference
// "must not result in a circular reference". Functions are eliminated in Normalize by
// INLINING the resolved body and binding arguments — never a runtime call mechanism.
// Overload/fluent dispatch is resolved upstream by the translator.

define function "Doubled"(x Integer): x * 2
define function "AddThem"(a Integer, b Integer): a + b

define "UseDoubled": Doubled(21)
define "UseAddThem": AddThem(40, 2)
define "NestedCalls": AddThem(Doubled(10), Doubled(11))
