# Null list and empty list

How the compiler tells a null list from an empty one, and which list reductions stay at relation grain.
This doc is the source for three requirements:

- `LANG-LIST-NULL-STATE`: when null and empty can be told apart.
- `ARCH-LIST-NULL-SIDECAR`: the per-subject `__is_null` sidecar.
- `ARCH-RELATION-REDUCE`: whole-list reductions at relation grain, without an array box.

## 1. The problem

A relation-encoded list (rows keyed by `__subject_id` and `__ordinal`, `DATA-LIST-ENCODING`) shows two
different CQL values the same way: no rows for the subject.

- An empty list: the subject is there, the list has no elements.
- A null list: the define is null for that subject.

A retrieve never yields null, so this is usually harmless (`LANG-LIST-NULL`). But
`if C then [Encounter] else null` gives a null list, and a few operators care.

## 2. Three states without collecting

The three states can be recovered without collecting elements per subject:

- Empty or not comes from row presence: a semi- or anti-join against the relation's subjects, as `exists`
  does.
- Null or not comes from the define's condition, a per-subject boolean. It never needs the rows.

| Subject state | `__is_null` | Rows | List value |
|---|---|---|---|
| Condition false | `true` | none | null |
| Condition true, no elements | `false` | none | empty |
| Condition true, elements | `false` | one or more | non-empty |

## 3. Who can tell — `LANG-LIST-NULL-STATE`

Only these consumers tell a null list from an empty one:

- `IsNull` and `IsNotNull` over a list (`{} is null` is false).
- List `=` and `~` against a null operand.
- `Coalesce(list, …)`: a null list falls through to the next operand; `{}` does not.
- The output boundary, which renders null or `[]`.

Every other list consumer treats them the same and ignores the sidecar: `exists`, `Count`, `First`,
`Last`, `singleton from`, `in`, `Sum`, `Min`, `Max`, `Avg`. `Count` is 0 for both.

## 4. The sidecar — `ARCH-LIST-NULL-SIDECAR`

A list define's nullity is a narrow per-subject view, `null_<define>`, with columns
`(__subject_id, __is_null)`. The element relation is never reshaped. Three gates keep it cheap:

1. Static nullability (`passes/ListNullability.scala`, a recomputed attribute like `ARCH-TYPE-ATTR`). A
   define gets a sidecar only if its body can be null. Retrieves, `union`, a query with a `return`,
   `ToList` and list literals are never null and get none.
2. Lazy join. The sidecar is joined only by a consumer from section 3.
3. Built from the condition. `__is_null` comes from the define's gating condition, so it never forces a
   collect.

Every `if C then [X]` with no `else` is nullable, because ELM fills in `else null`. The gates keep that
free when nothing observes it.

## 5. Reductions at relation grain — `ARCH-RELATION-REDUCE`

Many whole-list operators are relational reductions. They can run at subject grain instead of boxing the
list into an array. A box is still needed for a list inside a tuple, a nested list, and positional
`Indexer`.

| Operator | At relation grain | Built |
|---|---|---|
| `Count` | `count(__val)` per subject (`count(DISTINCT __val)` after `return distinct`), 0 for a subject with no rows | Yes |
| `exists` | presence join | Yes |
| `First`, `Last` | arg-min or arg-max by `__ordinal` per subject | No |
| `Sum`, `Min`, `Max`, `Avg` | `GROUP BY __subject_id` | No |
| `in` | semi-join on `__val` | No |
| `union`, `intersect`, `except` | relation set operations | No |

## 6. Status

Built and tested:

- Static nullability, sidecar production and the `IsNull` consumer (`SqlFrameLowering`). Fixture
  `FhirNullableList` covers all three states and matches the Java reference.
- `Count` at relation grain. Fixture `FhirListCount` matches the reference and pins `Count` of an empty and
  a null list at 0.
- `FhirReturnList` runs at relation grain.
- The conformance harness renders a null-state subject as null and an absent subject as `[]`
  (`SqlDifferential.engineResults`).

Not built:

- List `=` and `~` against null, and `Coalesce(list, …)`. Both need a box that emits null, not `[]`, for
  null-state subjects.
- The other reductions in section 5. No corpus fixture uses them on a relation-grain list yet, so they
  would ship untested (`PROC-TDD`). `FhirListWholeListOp` stays boxed. It is the regression guard
  for the boxed path.
