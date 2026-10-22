# Language semantics

The CQL semantics the compiler must keep. Mistakes with nulls, dates and units give wrong rates without
any error. The authority is the CQL Reference (the operator definitions), not the Author's Guide.

CQL semantics go through the compiler's own lowering rules, never straight onto Spark operators, because
Spark's defaults differ from CQL on nulls, dates and units.

Sections carry `LANG-*` IDs. The registry is
[requirements.md](requirements.md).

## 1. Three-valued logic — `LANG-3VL`

CQL booleans are `true`, `false` or `null`.

- CQL's 3VL resembles SQL's but is not the same. Don't assume Spark's boolean semantics match.
- A conditional treats a null condition as not true and takes the `else` branch. The truth test is
  `cqlTrue(x)`, true only when `x` is exactly `true`. Its negation, `not cqlTrue(x)`, covers `false` and
  `null`.
- `=` (equality) and `~` (equivalence) are different operators, especially around nulls and precision.

Every boolean-producing CQL operator lowers through the 3VL rules (`sema/Bool3.scala`, the lowering in
`SqlColumnLowering`).

### 1.1 Equivalence — `LANG-EQUIV`

Equivalence (`~`) is total: it never returns null. The per-type rules live in
`SqlColumnLowering.equivAt`:

- `String`: lower-cased, and each `[ \t\r\n]` treated as a space. `'a\tb' ~ 'a b'` is true. `'a b' ~ 'a  b'`
  is false: runs aren't collapsed and ends aren't trimmed.
- `Decimal`: rounded to 2 places.
- Other types: null-safe equality on the canonical encoding.

`distinct` and `in` use equality, not equivalence (CQL Reference). `'diabetes' in { 'Diabetes' }` is
false.

The CQF reference (4.8.0) does no whitespace normalization in string equivalence. The compiler follows the
spec and declares the difference on the fixture (`CONF-REF-DEVIATION`).

## 2. Lists — `LANG-LIST`

CQL lists are ordered and allow duplicates.

- The default encoding is a relation, one row per element. Spark has no row order across a shuffle, so
  every relation-encoded list carries `__ordinal`, kept by every operator that makes or reads a list.
- Some constructs force the array encoding ([data-format.md](data-format.md) section 3). The encoding is
  tracked and converted at boundaries with `explode` and `collect_list`.

### 2.1 Null list and empty list — `LANG-LIST-NULL`, `LANG-LIST-NULL-STATE`

A retrieve never yields a null list, only an empty one. A list can still be null, for example from
`if C then [Encounter] else null`. Only some operators can tell null from empty (`IsNull`, list `=`
against null, `Coalesce`, output). For those, the compiler carries a per-subject `__is_null` sidecar.
Every other consumer treats null and empty the same. See [list-null-tristate.md](list-null-tristate.md).

### 2.2 Aggregate functions — `LANG-AGGREGATE-FUNCTIONS`

The CQL aggregate functions reduce a list to one value: `Count`, `Sum`, `Min`, `Max`, `Avg`, `Median`, `Mode`,
`Variance`, `StdDev`, `PopulationVariance`, `PopulationStdDev`, `Product`, `GeometricMean`, `AllTrue` and
`AnyTrue`. They ignore null elements, and all but `Count`, `AllTrue` and `AnyTrue` return null for an empty list
or one with only nulls (CQL Reference, Aggregate Functions). `Count` is then 0, `AllTrue` true and `AnyTrue`
false. `Min` and `Max` compare with CQL ordering, so they work on dates, times and quantities, not only numbers.
An uncertain comparison (`@2012` against `@2012-05-01`) keeps the running value, as the reference does.

A list of quantities in one unit keeps that unit. A list in mixed units goes to its coarsest unit
(`Sum({ 1 'g', 500 'mg' })` is `1.5 'g'`) through the UCUM table (`sema/Ucum.scala`). A unit the table does not
list gives null, unless the whole list shares it. `Product` and `GeometricMean` over quantities fail loudly
(`UnsupportedNode`): they need compound-unit algebra, which is not built.

## 3. Intervals and temporal precision — `LANG-INTERVAL`

- CQL dates and times carry a precision (year, month, day, time). A comparison can be null when the
  precision is too coarse to decide. Spark `DATE` and `TIMESTAMP` carry no precision.
- The compiler carries precision as data and implements temporal comparison and the interval algebra
  (`during`, `overlaps`, `meets`, `before`/`after`, `same X as`, `included in` and the rest) itself, in
  SQL and the runtime UDFs. It doesn't map them onto native Spark date operators.
- Intervals are typed and can be open or closed at either end.

### 3.1 Named precision — `LANG-INTERVAL-PRECISION`

An interval operator can name a precision: `X during day of Y`. ELM carries it as a `precision`
attribute. The precision belongs to the comparison, so both operands are truncated to it before
comparing. Without that, a coarse bound against a fine value returns null.

### 3.2 Date and time components — `LANG-DATETIME-COMPONENT`

`year from X`, `month from X` and the other component extractors return one component of a date, time or
date-time as an Integer, or null when `X` is null or less precise than the component (CQL Reference, Date and
Time Operators, "Component From").

Not built: `DateTimeComponentFrom` fails at Normalize (`UnknownExpr`), loudly.

### 3.3 Date-time offsets — `LANG-DATETIME-OFFSET`

A date-time with no offset takes the offset of the evaluation request (CQL Reference, Types, DateTime). A
date-time the compiler returns must carry that offset, as the reference does.

Not built: a date-time computed from an offset-less parameter, such as `start of "Measurement Period" - 5
months`, is returned with no offset, while the reference returns `+00:00`. Comparisons agree because both run in
UTC; the returned value differs.

## 4. Quantities and units — `LANG-QUANTITY`

A CQL quantity has a unit. Arithmetic and comparison need UCUM conversion (`sema/Ucum.scala`, the
`cql_convert_quantity` UDF). A quantity is a struct of `value` and `unit`.

`LANG-INSTANCE`: ELM's `Instance` builds a typed value from named slots, usually in FHIRHelpers
(`ToQuantity`, `ToRatio`, `ToCode`). The IR node keeps the declared type, so it types as the System type
and lowers to the canonical struct.

## 5. Terminology — `LANG-TERMINOLOGY`

Value sets are expanded outside the compiler and read as semi-joins against the value-set table, never as
per-row service calls (`DATA-TERMINOLOGY-ADAPTER`). Value sets can be passed as function arguments; the
binding survives inlining (`LANG-FUNCTION-TERMINOLOGY`).

## 6. Choice types — `LANG-CHOICE`

A `Choice<T1, T2, …>` holds a value of one of several known types. Choices come from conditionals with
different branch types, from `union` of differently typed lists, and from FHIR `value[x]`.

A choice is encoded as typed slots, one per type, with one populated per row. `as`, `is` and implicit
conversions read the slot for the target type and give null on a mismatch.

- `LANG-CHOICE-MODEL`: `value[x]` slots are used only for genuine FHIR choice elements, as the model says.
  On a single-type element (`Encounter.status`), `as` and `is` are ordinary navigation.
- `LANG-CHOICE-RESOURCE`: a choice member can be a whole resource. `[Encounter] union [Condition]` stays
  relation-encoded with tagged rows.

## 7. Contexts — `LANG-CONTEXT`

Defines evaluate in a context (Patient, Practitioner, Unfiltered), mapped to a grouping key
(`__subject_id`).

### 7.1 Default context — `LANG-CONTEXT-DEFAULT`

The CQL spec says: "When no context is specified, the default is Unfiltered." Precedence:

1. The caller's explicit context.
2. The library's declared context (`context Patient`).
3. The model's default context. Not built.
4. `Unfiltered`.

A context-free define (`define "One": 1 + 1`) compiles to a one-subject frame with no data, never a
`retrieve_Patient` read.

## 8. Runtime conditions — `LANG-RUNTIME-COND`

CQL defines runtime results for conditions such as a `singleton from` over many elements, an invalid unit
conversion, or division by zero. These depend on the row and give the result the spec defines (often
null). They become per-row results, never a job failure. One patient's data can't fail a population run.
Compile-time failures are different: they fail the whole measure ([engine-design.md](engine-design.md)
section 8).

## 9. Queries — `LANG-QUERY`

A query has a primary source, optional relationship clauses, a filter, a shaping clause, and ordering or
aggregation. Every clause lowers to set-at-a-time operations that keep `__ordinal`.

- `where` (`LANG-QUERY-WHERE`): a filter through 3VL, so null excludes the row.
- Define references (`LANG-QUERY-DEFREF`): a clause can read another define. A per-subject scalar define
  joins onto the query's frame by `__subject_id`.
- `with` / `without` (`LANG-QUERY-WITH`): semi- and anti-joins with a 3VL `such that`.
- Reference follow (`LANG-REFERENCE-FOLLOW`): resources with no subject (`Medication`) join on reference
  equality in `such that`, not on `__subject_id`. `LANG-REFERENCE-FOLLOW-VALUE`:
  `singleton from ([Encounter] E where E.id = <ref>)` lowers to a left outer join.
- Profiles (`LANG-RETRIEVE-PROFILE`): a retrieve can name a profile. The constraint is tested at the scan
  against `meta.profile`. Resources that claim no profile are kept.
- `return` (`LANG-QUERY-RETURN`): projection. `return distinct` deduplicates by equality.
- `sort` (`LANG-QUERY-SORT`): reorders and rewrites `__ordinal`, with CQL ordering semantics.
- `aggregate` (`LANG-QUERY-AGGREGATE`): a fold over the ordered elements with an accumulator.
- Multiple sources (`LANG-QUERY-MULTISOURCE`): a Cartesian combination keyed by `__subject_id`.
- `let` (`LANG-QUERY-LET`): a value scoped to one source row, inlined at each reference.

## 10. Functions — `LANG-FUNCTION`

CQL doesn't allow recursive functions. Bounded recursion uses `aggregate` (CQL Developer's Guide).

- Functions are inlined in Normalize (`FunctionInliner`). The call graph is acyclic, so inlining ends.
- The ELM already resolves each `FunctionRef` to one `FunctionDef`. The compiler inlines that target.
- A circular reference is a compile error, found in Resolve.

## 11. No implicit includes — `LANG-NO-IMPLICIT-INCLUDE`

The compiler translates a library as written, and accepts it when cqframework `cql-to-elm` does. It never
adds a `using`, `include` or `library` header.

- A model is named, never inferred (CQL Developer's Guide). `[Encounter]` with no `using` is not a FHIR
  retrieve.
- FHIRHelpers is included by the author. `include FHIRHelpers version '4.0.1'` resolves to the bundled
  copy (`translate/FhirHelpersSource.scala`).
- A library with no header is legal. It can use a model but not FHIRHelpers.
