# Pipeline plans — `/compile-pipeline`

`/compile-pipeline` compiles a measure into a `PipelinePlan`: one SQL recipe per materialized define.
A caller builds and runs its own dataset graph from it. The compiler describes the plan; it doesn't run
it (`BUILD-COMPILE-SERVICE`, `PIPE-RUNTIME-COMPILE`). In this repository the client (`cql_databricks.result`) is
the caller: it runs each recipe on a SQL warehouse or cluster.

## 1. What a caller does — `PIPE-SQL-DAG-REUSE`

1. Describe once: `/describe` returns the `LibraryGraph`.
2. Compile once: `/compile-pipeline` returns the plan.
3. Check the compiler version in the response against the one it expects (`CORE-DETERMINISM`).
4. Build one dataset per recipe, wired by `recipe.reads`.

## 2. Recipes

`CompileService.compilePipeline` lowers the measure once and cuts the define graph at each materialized
define (`CompileService.cutPlan`). A reference to a materialized sibling becomes a read of that sibling's
published dataset, recorded in `reads`. Results are the same as the fully inlined script.

Each recipe (`DatasetRecipe`, Python `DatasetRecipe` in `providers.py`) has:

| Field | Meaning |
|---|---|
| `name` | The published dataset name |
| `target` | The define it computes |
| `reads` | The published siblings it reads, sorted (`PIPE-READS-SORTED`) |
| `statements` | The DDL script for the interactive path (`CREATE OR REPLACE TEMP VIEW`, `CACHE TABLE`) |
| `query` | The same logic as one `WITH … SELECT * FROM <target>`, with no DDL, for a declarative orchestrator |
| `library`, `libraryVersion`, `define` | The ELM coordinates, with the define's raw name |
| `sqlFile` | A deploy-ready `CREATE OR REFRESH MATERIALIZED VIEW` (or `VIEW`) statement (`PIPE-EMIT-SQL`) |
| `kind`, `valueColumn` | The row shape (`scalar`, `relation`, `resource`) and its payload column |
| `durable` | The durable identity, when the request asks for it (section 4) |

The row shape travels with the recipe, so a reader never probes the schema (`ARCH-NO-RUNTIME-SCHEMA`).
Scalars publish `(__subject_id, __val)`. Relations publish `(__subject_id, __ordinal, __val)`
(`EMIT-SQL-VALUE-COLUMN`). Null sidecars are views that re-inline in each consumer.

### Dataset names

`SqlNames.datasetName` builds the published name: `{library}_{version}[_{paramHash}]__{define}`, all
lower-cased and sanitized. `paramHash` is the first 8 hex digits of a SHA-256 over the parameters, so the
same measure with different parameters publishes different datasets.

### Shared reads as datasets — `PIPE-READ-DATASET`

`materialize.retrieves` (default `none`) can publish filtered retrieves as their own recipes: `costly`
publishes reads used at `materialize.retrieve_min_reuse` (default 2) or more call sites, `all` publishes
every filtered read. Bare retrieves are never published. A read recipe has `kind = "resource"`, payload
`__resource`, no `reads`, and a content-addressed name `read_<type>[_<filter>]_<hash>`
(`passes/RetrieveReads.scala`). It is ordered before the define recipes.

## 3. Libraries

`deps` carries the dependency ELM or CQL for one root library (`BUILD-COMPILE-LINK`).
`LibraryLinker.link` flattens root and dependencies into one library and `prune` drops what no root
needs. The service does no library lookup: the caller resolves the include closure and sends it. A
missing dependency fails the compile (`CORE-NO-SILENT-MISLOWER`).

`libraries` compiles several measures into one merged plan (`PIPE-MULTI-LIB`, `PIPE-MULTI-DESCRIBE`).
When it is present, `elm`, `cql` and `deps` are ignored:

```json
{"libraries": [{"elm": "...", "deps": ["..."]}], "context": "Patient", "parameters": {}}
```

`LibraryLinker.linkMulti` links each root on its own, prefixes each root's defines with `{libId}__`, and
merges shared dependency defines by name.

## 4. Durable identity — `PIPE-DURABLE-IDENTITY`, `RUN-CLIENT-RESULT-IDENTITY`

With `durable: true`, each recipe carries a `durable` object: `format` (`cql-result-identity-v1`),
`fingerprint`, `reusable` and `parameters` (`service/DurablePlan.scala`).

- The fingerprint is a SHA-256 over the format, the recipe's query, the typed values of the parameters
  in the define's dependency closure, the `[read, fingerprint]` pairs of its upstream recipes and the bound
  data source's `catalog.schema`.
- The destination, the caller and the compiler version are not part of it (`DEC-RESULT-IDENTITY`).
- A durable compile that retrieves data needs `dataSource`. Explicit `sources` bindings or bare
  `retrieve_<Type>` names fail, because the result would have no source address.
- `reusable` is false for a define that uses `Now`, `Today`, `TimeOfDay` or `Random`, and for every define
  that reads one.

The client reads each sibling through an outer CTE over its current Delta table, and embeds the compiler
query unchanged. No compiler SQL is parsed or rewritten. This is calculation identity. It says nothing
about whether the source data changed: the data source serves as-of data.

## 5. Tests

- `SqlDagReuseSpec` (`PIPE-SQL-DAG-REUSE`): one recipe per materialized define, sibling reads declared,
  and per-subject results identical to the fully inlined script.
- `MultiLibPipelineSpec` (`PIPE-MULTI-LIB`): qualified recipe names, the single-library path unchanged,
  shared dependencies merged.

## 6. The interactive client — `BUILD-CLIENT-SESSION`, `BUILD-CLIENT-QUERY`, `BUILD-CLIENT-POPULATIONS`

`cql(source)` returns a lazy `CqlQuery`.

```python
q = client.cql("cql/MyMeasure.cql").with_measurement_period("2026-01-01", "2026-12-31")
q.elm()              # ELM JSON
q.dag()              # the LibraryGraph (an alias of describe())
q.populations()      # the measure population defines
q.exec("Numerator")  # one target: a DataFrame
q.exec()             # the populations: {define: DataFrame}
```

The result follows the call: one named target gives a DataFrame, and `exec()` with no targets always gives
a dict. When context and data model are unset, the compiler applies `LANG-CONTEXT-DEFAULT`. The emitted SQL
is internal.

## 7. Library sources — `BUILD-LIBRARY-PROVIDER`

Three seams in the client:

| Seam | Answers | Built in |
|---|---|---|
| `CompileProvider` | How the request travels | `RemoteProvider` (HTTP) |
| `CredentialProvider` | How it authenticates | `NoAuth`, `StaticBearer`, `OAuth2TokenExchange` |
| `LibraryProvider` | Where CQL comes from | `FileLibraryProvider` |

Library resolution is in the client because the service only links what it's given.

```python
from cql_databricks.compiler import CqlClient
client = (CqlClient.builder()
          .endpoint("http://localhost:8080")
          .library_path("/Volumes/main/measures/my-measures", "./local-overrides")
          .spark(spark).build())
q = client.cql(library="MyMeasure")   # closure resolved
```

- Layouts, all scanned by default: `flat` (`*.cql`, `*.elm.json`), `ig` (`input/cql/**`), `fhir-library`
  (`Library-*.json`), `nested` (`*/*.cql`). Pick with `library_layout(...)`.
- Earlier roots win. An `include` resolves against the including library's own directory first. An exact
  `<Name>-<version>` match wins; otherwise any `<Name>`.
- A custom provider implements `resolve(ref)`. Pass it with `library_provider(...)`, or register it under
  the `cql_databricks.libraries` entry-point group and pick it with `library_provider_kind("name")`.
