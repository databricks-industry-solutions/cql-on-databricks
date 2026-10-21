library ChoiceTypes version '1.0.0'

// Choice types: a value of one of several statically-known types (LANG-CHOICE). Sources here are
// conditionals whose branches differ in type and a Union of differently-typed lists. Encoded as a
// discriminated struct; `as`/`is` read the discriminator and project to a concrete type. (Phase 4.)

define "HetIfInt": if true then 1 else 'x'
define "HetIfStr": if false then 1 else 'x'
define "HetCase": case when 1 = 2 then 1 when 1 = 1 then 'two' else 3.0 end
define "AsMatch": (if true then 1 else 'x') as Integer
define "AsMismatch": (if true then 1 else 'x') as String
define "IsMatch": (if true then 1 else 'x') is Integer
define "IsMismatch": (if true then 1 else 'x') is String
// NB: Union of differently-typed lists (List<Choice>) arrives with the query/list set-ops slice.
