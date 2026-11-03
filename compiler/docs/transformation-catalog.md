# Transformation catalog

Each transformation the compiler applies, by stage, with the requirement behind it. For one program traced
through every stage, see [pipeline-walkthrough.md](pipeline-walkthrough.md).

## Normalize — IR rewrites

1. Library linking (`CONF-MEASURE-LINK`): flatten the dependency closure into one library, qualify
   cross-library references, drop unreachable definitions (`LibraryLinker`).
2. Function inlining (`LANG-FUNCTION`): replace each function call with its body, arguments bound
   (`FunctionInliner`).
3. Let-query inlining (`LANG-QUERY-LET`): collapse a correlated query over a single source into its body
   (`LetQueryInliner`).
4. Parameter binding (`LANG-PARAMETER`): substitute the default or the caller's override, at the
   parameter's declared precision (`ParameterResolver`).
5. Code and concept resolution (`LANG-TERMINOLOGY`): resolve codes and concepts to literals
   (`ConceptResolver`).
6. Type and encoding attribution (`ARCH-TYPE-ATTR`): each node's type, nullability and list encoding,
   computed on demand (`TypeEncoding`).

## Optimize — retrieve pushdown

7. Terminology pushdown (`ARCH-PUSHDOWN`): move value-set predicates from consumers onto the retrieve
   (`TerminologyPushdown`).
8. Date-range pushdown (`ARCH-PUSHDOWN`): fold date predicates into a literal range on the retrieve
   (`DateRangePushdown`).
9. Period-range pushdown (`ARCH-PUSHDOWN`): a coarse Period pre-filter as string comparisons, a
   non-lossy superset (`PeriodRangePushdown`).

## Lower — IR to Spark SQL

10. Schema from the model (`ARCH-NO-RUNTIME-SCHEMA`): every schema comes from types and the FHIR model,
    never from Spark.
11. Relation or array (`DATA-LIST-ENCODING`, `ARCH-GRAIN`): keep lists as rows with `__ordinal`. Box to
    an array only at forced boundaries.
12. Boxing boundaries (`ARCH-CONDITIONAL`): convert between relation and array where the type forces it.
13. Empty, not null, by default (`LANG-LIST-NULL`): a list with no rows is empty. The `__is_null` sidecar
    marks a null list where it can be observed.
14. Multi-source order (`LANG-QUERY-MULTISOURCE`): carry `__ordinal` through joins and unions.
15. 3VL (`LANG-3VL`): boolean operations follow CQL's three-valued logic. `WHERE null` drops the row.
16. Conditionals (`ARCH-CONDITIONAL`): `if` and `case` lower to `UNION ALL` of branches filtered by
    `cqlTrue(condition)` and `not cqlTrue(condition)`.
17. Singleton query source (`LANG-QUERY`): a query over a one-row source (the context resource)
    projects columns, with no scan or join.
18. Choice type (`LANG-CHOICE`): typed slots, as in FHIR `value[x]`.
19. `value[x]` projection (`LANG-CHOICE-MODEL`): only for genuine FHIR choice elements.
20. Typed-slot dispatch (`LANG-CHOICE-RESOURCE`): bare `value[x]` and `extension(url).value` read the
    slot for the declared type. A heterogeneous choice can range over whole resources.
21. Lineage (`DATA-EXPORT`): a resource's identity is its type and its own `id`; a mixed list's handle carries
    `__resource_type` and `__resource_id`.
22. Code cardinality: read from the model whether a code element is single or a
    list, and lower membership to match.
23. Static type tests (`LANG-CHOICE-MODEL`): resolve `as` and `is` on `value[x]` against the model and
    fold the resulting literal tests, so no dead branch reaches the SQL.
24. Conditional as union over relations (`ARCH-COND-UNION`): a list-valued conditional with
    relation-valued branches lowers to `UNION ALL` of filtered branches, with no boxing.
25. Value-set membership (`LANG-TERMINOLOGY`, `DATA-ENVIRONMENT-LAYOUT`): `in ValueSet(…)` is a semi-join
    against the value-set table.
26. `with` and `without` (`LANG-QUERY-WITH`): a semi-join or anti-join filtered by `cqlTrue(such that)`.
27. `return distinct` (`LANG-QUERY-RETURN`): dedupe with equality semantics.
28. `aggregate` (`LANG-QUERY-AGGREGATE`): fold the starting, step and result expressions in `__ordinal`
    order.
29. Named precision (`LANG-INTERVAL-PRECISION`): truncate both operands to the named precision before
    comparing.
30. Discrete interval bounds (`LANG-INTERVAL`): turn open bounds on discrete types into closed ones, so
    every relation compares closed intervals (`sema/CqlInterval.scala`).
31. Quantities (`LANG-QUANTITY`): a `(value, unit)` struct. Arithmetic and comparison convert units.
32. Scan sharing (`ARCH-SCAN-SHARE`): each retrieve and the context frame are built once and read by
    name.
33. Hoisting (`ARCH-HOIST`): reused sub-expressions become named columns or CTEs, which keeps the SQL
    small (`SqlHoist`).
34. Context frame projection (`ARCH-GRAIN`): a define that reads the context resource projects only the
    fields it needs.

## Schedule

35. Deterministic order (`CORE-DETERMINISM`): sort the define graph in a stable order, ties broken by
    name.
36. Materialization (`DATA-MATERIALIZE`): pick which defines become `CACHE TABLE` barriers and which stay
    views ([data-format.md](data-format.md) section 6).
