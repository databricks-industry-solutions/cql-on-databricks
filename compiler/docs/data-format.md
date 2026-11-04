# Data format

How values are represented physically. The rule: never collapse cardinality early. Let CQL do it.

Requirement IDs here are `DATA-*` (for example `DATA-LIST-ENCODING`, `DATA-SKIP-UNFILTERED`).

## 1. Data and terminology seams — `DATA-RETRIEVER`, `DATA-TERMINOLOGY-ADAPTER`

Clinical data and terminology enter through two seams. [data-binding.md](data-binding.md) has the full
contract (`BIND-*`).

- `DATA-RETRIEVER`: each retrieve reads a relation shaped `(__subject_id, __resource)`: the context key
  and the resource struct. In emitted SQL this is a read of the relation the request binds to the retrieve's
  call site. The binding absorbs differences between sources. The resource stays a struct. CQL collapses cardinality
  where it says to (`where`, `singleton from`).
- `DATA-TERMINOLOGY-ADAPTER`: expanded value sets are a table of `(valueset_id, system, code)`. Expansion
  happens outside the compiler. The compiler reads expansions; it never expands.

In the JVM test harness these seams are the `DataProvider` and `TerminologyProvider` traits
(`retrieve/`).

## 2. Physical values — `DATA-VALUE-TAXONOMY`

Each value lives in one physical form. Lowering picks forms and inserts conversions at operator
boundaries.

| Form | Shape | Notes |
|---|---|---|
| Literal or parameter | literal | Not keyed by subject (the measurement period, thresholds) |
| Context scalar | one value per subject, a column keyed by `__subject_id` | |
| List, relation-encoded | one row per element, with `__ordinal` | The default |
| List, array-encoded | an array column | Only at forced boundaries (section 3) |
| Tuple | struct column | |
| Choice | typed slots per type | Mirrors FHIR `value[x]`. See [language-semantics.md](language-semantics.md) section 6 |

## 3. Lists: relation or array — `DATA-LIST-ENCODING`

A `Relation` list is rows keyed by `__subject_id` and `__ordinal`. An `Array` list is an array in one row.
`explode` converts an array to a relation. `collect_list` converts back.

- Relation is the default. Filters, projections, set operations and aggregates are native there.
- The encoding is a tracked property. Lowering converts only when forced.
- Array is forced when the list is a struct field, when lists nest, or when an operator takes the whole
  list as one argument (positional `Indexer`, for example).
- `__ordinal` is required. CQL lists are ordered and Spark has no row order across a shuffle.

### 3.1 Subject key — `DATA-SUBJECT-KEY`

`__subject_id` holds a row's patient_id, the grouping and join key for every set-at-a-time operation. The engine
sets it where it reads a data relation, never mid-plan (`BIND-CONTRACT`). On a Patient or a Patient-compartment
type it is the relation's root `patient_id` when it returns one, a stored key the warehouse filters and clusters
on; else the FHIR logical `id` for a Patient, or the id after the last `/` of the subject reference. A type with no
patient context has a null key, whatever `patient_id` its relation returns.

### 3.2 Value carrier — `DATA-VALUE-CARRIER`

Every define's output column is `__val` (`EMIT-SQL-VALUE-COLUMN`). It is a scalar for a primitive, a
struct for a tuple or resource, and an array only when boxed at a forced boundary.

## 4. System columns — `DATA-SYSTEM-COLUMNS`

The double-underscore namespace `__*` is reserved (`spark/SystemColumns.scala`). FHIR JSON uses a single
underscore for primitive extensions (`_birthDate`), so there is no collision. pandas-on-Spark uses some
`__*` names (`__index_level_0__`); strip them before passing such a frame in.

| Column | Purpose |
|---|---|
| `__subject_id` | The context subject's identifier. The grouping and join key |
| `__ordinal` | Element order in a relation-encoded list |
| `__val` | The one payload column of every define (section 5.1) |
| `__resource` | The resource struct a retrieve returns: the view's `resource`, passed through |
| `__resource_id`, `__resource_type` | A resource's identity on the engine's handle for an element of a mixed list (section 7). A resource itself carries none |
| `__choice` | The Choice discriminator |
| `__encoding` | A value's list encoding |
| `__is_null` | The per-subject list-null sidecar ([list-null-tristate.md](list-null-tristate.md)) |

## 5. Defines as relations — `DATA-DEFINE-DF`

Every `ExpressionDef` lowers to a SQL relation (a view or a cached table) that holds its result.

### 5.1 One value column — `EMIT-SQL-VALUE-COLUMN`

The payload is always `__val`. The define name names the object (`SqlNames.obj`), not a column.

| Row shape | Schema | Rows |
|---|---|---|
| `scalar` | `(__subject_id, __val)` | One per subject |
| `relation` | `(__subject_id, __ordinal, __val)` | One per element, in `__ordinal` order |

A consumer reads `SELECT __val FROM <object>` for any define. The row shape travels as metadata
(`ARCH-NO-RUNTIME-SCHEMA`).

## 6. Materialization — `DATA-MATERIALIZE`

Some defines become `CACHE TABLE` barriers. A barrier caches a node of the define graph and cuts the
Catalyst plan on deep graphs. Not every define is materialized: a measure has hundreds of them.
`transpiler/lower/SqlMaterialization.scala` maps each decision to a statement.

### 6.1 Skip bare retrieves — `DATA-SKIP-UNFILTERED`

A bare retrieve with no filter is never a barrier under the default policy (reason `passthru`).
`materialize.skip_unfiltered_retrieves` (default `true`) computes the set of such defines
(`passes.Materialize.skipUnfilteredNames`) and passes it to the SQL lowering as `SqlEvalEnv.skipUnfiltered`.
`materialize.mode = all` still makes every define a barrier.

### 6.2 Policy — `DATA-MATERIALIZE-POLICY`, `EXEC-MATERIALIZE-FILTER`

`materialize.mode` is `costly` (the default), `all` or `none`.

- `costly`: a define becomes a barrier when it is a measure population, or when its body prunes rows
  anywhere: a retrieve with a scan filter, or a query with `where`, `with` or `without`
  (`EXEC-MATERIALIZE-FILTER`). A define whose lowered SQL is larger than a size threshold also becomes a
  barrier (`-Dcqldatabricks.sqlBarrierBytes`, default 12,000). Everything else is a view.
- `all`: every define is a barrier.
- `none`: every define is a view. Results are the same.

The decision and its reason (`population`, `valueset`, `date`, `profile`, `filter`, `size`, `passthru`,
`pushdown`, `scalar`) are reported per define (`SqlMaterialization.Decision`).

See [technical-implementation.md](technical-implementation.md) section 8 for every configuration key.

## 7. Results and traceability — `DATA-EXPORT`

The compiler produces two outputs:

1. Each `ExpressionDef`'s result as a relation of its CQL type: population membership, resources,
   scalars.
2. The identity of the source resources behind each result: each resource's type and its own `id`. A resource
   value carries no lineage field. Its type is the define's declared type; in a list of mixed types, the engine's
   handle for each element carries it as `__resource_type`, beside the `__choice` tag.

Building a `MeasureReport` or QRDA file from these is outside the compiler.
