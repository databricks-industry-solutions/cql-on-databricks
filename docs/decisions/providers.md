# Providers (proposed)

Status: agreed, and partly replaced by [environments.md](environments.md), which says what. Each `CLIENT-*` and `BIND-*` ID below is a requirement in
[compiler/docs/requirements.md](../../compiler/docs/requirements.md), and `DEC-PROVIDER-RELATION` is listed in
[decisions/README.md](README.md). Which steps are built is in the [roadmap](../roadmap.md), with the decisions
still open.

## Problem

A CQL evaluation needs four things: clinical data, terminology (value sets), knowledge (libraries and
measures) and results. The compiler takes each of them explicitly in its request. The client finds them in
many different ways, each hard-coded:

| Role | How the client finds it today |
|---|---|
| Data | `source_binding`, `data_source=` (schema tag), `mount()`, the only tagged schema in scope, `data_catalog=`, the bare `retrieve_<Type>` name |
| Terminology | The `runtime.valuesets` view, built at deploy over every tagged terminology schema; `valueset_table=`; `mount(valuesets=)` |
| Knowledge | `%%cql` cells, then `library_path=` folders, then tagged knowledge schemas; `location=` in `resolve()` |
| Results | Durable calculation code in `cql_databricks.result` |

Problems with this:

- **Too many paths.** Each has its own precedence and failure modes. `data_catalog=` sends a setting
  (`data.fhir_catalog`) the compiler's `Config.resolve` ignores, so its schema is never applied. `mount()` and
  `publish_data_source()` are two ways to turn tables into UDTFs.
- **The UDTF contract is brittle.** Its seven parameters are positional, so removing `library` broke every
  function. Stale traces remain: "eight" in comments (`SqlEvalEnv.scala`, `client.py`) and `_ids` parsing in
  `BatchInputs.scala`, though `BIND-RETRIEVE-SUBJECT` is retired.
- **Terminology is a snapshot.** `runtime.valuesets` unions the schemas tagged when its owner built it, in the
  deployer's scopes, not the reader's. `runtime.measures` and `runtime.libraries` have no readers.
- **The compiler request mixes namespaces.** `sources` holds FHIR types beside two reserved keys, `valuesets` and
  `udfs`. Data has two forms (`dataSource`, `sources`), and so does knowledge (`deps`, `libraries`).
- **Kept results can go stale.** A result's identity covers the data source's address, not its logic or its
  value sets, so an edited mapping reuses an old score.
- **No dev overrides for data or terminology.** Knowledge can override one library on top of a store. Data and
  terminology can only replace the whole source.

## Where CQL runs

| Compute | Who | Why |
|---|---|---|
| SQL warehouse | The Designer, the agent and Genie's MCP tools, as the user | An App's forwarded user token gets only `sql` among compute scopes: no Databricks Connect, serverless, cluster or Jobs scope, and "Databricks blocks access to any functionality outside the approved scopes" |
| Spark session | Notebooks, Jobs, SDP pipelines | Customer code runs there |

Both run the same compiled SQL. The warehouse is the fast path (see Measurements), so published data must be
readable by SQL alone.

## How a retrieve gets its rows

| Need | Build it as | Pushdown | Runs on |
|---|---|---|---|
| Data already in FHIR shape | A Delta table or UC view | Catalyst pushes predicates and projections into the scans | Warehouse and Spark |
| A mapping that SQL can express | A UC view | Through the view, into the source scans | Warehouse and Spark |
| A different source per retrieve (a value set and date range served by a historical view, say) | A provider whose `retrieve` picks the relation from the request, in the client | Into whichever relation it picks | Warehouse and Spark; custom code runs only in notebooks and Jobs |
| The same, usable from the Designer and Genie | A UC SQL function that branches on its arguments; the arguments are literals, so Catalyst drops the branches not taken | The arguments, and Catalyst through the inlined body | Warehouse and Spark |
| Mapping that needs PySpark (an OMOP-to-FHIR job, say) | A pipeline or Job that materializes FHIR to Delta; the table is then the source | Scans of the materialized table prune by file skipping; nothing reaches the source tables | Warehouse and Spark |
| Iterating on a PySpark mapping (dev) | A DataFrame returned by the provider | Catalyst through the DataFrame's plan, except through Python UDFs | Spark only |

A DataFrame lives in one Spark session. A warehouse can't read it, so a DataFrame provider on a warehouse run
fails loudly, naming the provider. Nothing is materialized behind the caller's back.

## Proposal

Each role is an interface with methods made for that role. We ship several implementations; customers write
their own. The client resolves everything through providers, and nothing else.

### Interfaces

```python
class DataProvider(ABC):                        # CLIENT-DATA-PROVIDER
    reusable: bool = True                       # False for dev providers: their results are never reused
    def identity(self) -> str: ...              # goes into the result fingerprint (CLIENT-PROVIDER-IDENTITY)
    def types(self) -> frozenset[str]: ...      # a missing type fails before the run
    def retrieve(self, request: RetrieveRequest) -> Relation: ...
    def change_log(self) -> Relation | None: return None   # DATA-CHANGE-LOG-PERIODS

class TerminologySource(ABC):                   # CLIENT-TERMINOLOGY-PROVIDER
    def identity(self) -> str: ...
    def expansions(self, refs: Sequence[ValueSetRef]) -> Relation: ...   # (valueset_id, version, system, code)
    def find_code(self, code: str, system: str | None) -> Sequence[ValueSetRef]: ...  # AGENT-VALUE-SET-LOOKUP

class TerminologyStore(TerminologySource):
    def publish(self, value_sets: Sequence[ValueSet]) -> PublishReport: ...

class KnowledgeSource(ABC):                     # CLIENT-KNOWLEDGE-PROVIDER
    def identity(self) -> str: ...
    def library(self, name: str, version: str | None) -> LibrarySource | None: ...
    def measure(self, url: str, version: str | None) -> MeasureResource | None: ...
    def libraries(self) -> Iterable[LibraryRef]: ...

class KnowledgeStore(KnowledgeSource):
    def publish(self, closure: LibraryClosure) -> PublishReport: ...

class ResultStore(ABC):                         # CLIENT-RESULT-STORE; Delta only
    def find(self, fingerprint: str) -> TableRef | None: ...
    def write(self, recipe: Recipe, relation: Relation) -> TableRef: ...
```

`Relation` (`BIND-RELATION`) is one of:

| Kind | Names | Runs on |
|---|---|---|
| `TableRelation` | A Delta table or UC view holding `(__subject_id, __resource)` | Warehouse and Spark |
| `FunctionRelation` | A UC SQL function returning `(__subject_id, __resource)`, called with the request's arguments | Warehouse and Spark |
| `VolumeRelation` | A volume folder of `<Type>.ndjson` or FHIR Bundles, read with `read_files` | Warehouse and Spark |
| `DataFrameRelation` | A DataFrame; the client registers it as a temp view under a name it owns | Spark only |

A relation names something; it never carries a field-mapping language of its own. Mapping logic lives in the
view, the function, the pipeline or the DataFrame. The shape is checked against the model before the run
(`BIND-SCHEMA-CHECK`) and reshaped by the shared code (`BIND-RESHAPE`): missing fields become typed nulls, extra
fields are dropped. So a model change is absorbed once.

- **One request object, per retrieve** (`BIND-RETRIEVE-REQUEST`). `RetrieveRequest` holds `fhir_type`,
  `value_set`, `codes`, `date_property`, `date_low`, `date_high` and `parameters`. A new field gets a default, so
  old providers never break, and a retired field stays and is ignored. The provider is asked once for each
  retrieve in the CQL, so it may pick a different relation for each. It may ignore every field but `fhir_type`:
  the engine re-applies the filters (`BIND-CAPABILITY`). Whatever it returns must hold every resource that
  matches the request; a relation missing some returns wrong results without an error.
- **Functions take the arguments they declare, by name** (`BIND-NAMED-ARGS`). The client reads a function's
  parameters from `information_schema.parameters` and the compiler calls it with only those, as named arguments
  (`fn(value_set => …, date_low => …)`). A function declares only what it uses, so adding a request field never
  breaks one, and no function has a fixed positional signature.
- **Read and write are separate interfaces** (`CLIENT-PROVIDER-READ-WRITE`). One class may implement both. Code
  that reads takes a `Source`; `publish` takes a `Store`, so publishing into a read-only folder is a type error.
  Data has no `publish`: customers own their tables. A result store's job is writing, so `write` is on it.
- **Knowledge is looked up at compile time** and returns CQL and ELM, not rows. The include-closure algorithm
  stays in one place in the client core. Providers only answer lookups.
- **Overrides are composition** (`CLIENT-PROVIDER-OVERLAY`). `Overlay(dev, base)` exists for each role: per type
  for data, per value set for terminology, per library for knowledge. Today's knowledge lookup order (cells,
  folders, stores) becomes an explicit chain. An overlay's `reusable` is false when any layer's is.

### Shipped implementations

| Role | Implementations |
|---|---|
| Data | Delta table or view; UC SQL function; volume folder of `<Type>.ndjson` or Bundles; DataFrame (Spark only). Never a workspace folder |
| Terminology | Delta table; FHIR `ValueSet` JSON in a volume (both computes) or a workspace folder (Spark only). A value set must carry its expansion; one with only `compose` fails |
| Knowledge | `%%cql` session cells; folder (workspace or volume); Delta store |
| Results | Delta, in a schema the caller names. Never a folder |

### Default resolver

`TaggedResolver` (`CLIENT-DEFAULT-RESOLVER`) finds providers by schema tag, in the caller's catalog scopes
(`CLIENT-DISCOVERY-SCOPE`), as the caller:

| Tag on a schema | Provider it yields |
|---|---|
| `cql_databricks_data_source = <name>` | A data provider over the schema's `retrieve_<Type>` objects: each a table, view or SQL function |
| `cql_databricks_terminology = <name>` | A Delta terminology provider over its `valueset` table |
| `cql_databricks_knowledge = <name>` | A Delta knowledge store over its `library` and `measure` tables |

The Designer, the agent and Genie's MCP tools use it to list sources and pick one by name. Terminology for a run
is every tagged terminology schema in the caller's scopes, read at each compile; this replaces
`runtime.valuesets`. Tags stay on schemas: Unity Catalog can tag functions, but `information_schema` has no view
for function tags, and a kept result's address is a `catalog.schema`.

### Compiler request

The request carries one field per role (`BIND-PROVIDER-REQUEST`), in place of `dataSource`, `sources` (with its
`valuesets` and `udfs` keys) and `deps`/`libraries`:

```json
{"data": {"<retrieve id>": <relation>, ...}, "terminology": [<relation>, ...], "knowledge": [...], "runtime": {"schema": "..."}}
```

`describe`, which the client already calls before binding, returns each retrieve with an id and its request. The
client asks the provider for each one and sends the relations keyed by retrieve id; the compiler embeds them in
the SQL it emits, passing a function its request as named arguments. A `DataFrameRelation` is registered as a temp
view under a name the client owns. Which relation each retrieve used is in the SQL, so the fingerprint covers it.
The client never patches the SQL the compiler returns.

### Results and identity

A kept result's fingerprint covers each provider's `identity()` in place of the data source's address
(`CLIENT-PROVIDER-IDENTITY`, amending `RUN-CLIENT-RESULT-IDENTITY`), and the terminology's too. A run with any
provider whose `reusable` is false is never reused and never kept.

## What is removed

| Removed | Replaced by |
|---|---|
| `mount()`, `reset()`'s mount handling (`CLIENT-SESSION-MOUNT`) | Data and terminology providers; `Overlay` for dev |
| `connect(data_catalog=)` and the ignored `data.fhir_catalog` setting | A data provider named by address |
| `source_binding` | A data provider, or an `Overlay` per type |
| "Only one tagged source in scope" (`CLIENT-EXEC-DISCOVERY`) | `TaggedResolver` naming the source; see `DEC-NOTEBOOK-DEFAULT-RESOLVER` |
| `connect(valueset_table=)` | A terminology provider |
| `runtime.measures`, `runtime.libraries`, `runtime.valuesets`, `bind_knowledge_views` (`DATA-BINDINGS`) | Providers from `TaggedResolver` |
| `POST /api/knowledge-sources/reload` in the Designer (`DATA-BIND-AUTO`, already retired) | Nothing: no caller |
| `_ids` parsing in `BatchInputs.scala` (`BIND-RETRIEVE-SUBJECT`, already retired) | Nothing |
| The seven positional retrieve parameters (`BIND-EXPLICIT-PARAMS`) | `BIND-RETRIEVE-REQUEST`, `BIND-NAMED-ARGS` |

Each removed requirement is marked SUPERSEDED in place, naming its replacement.

## Measurements

2026-10-31, `DiabetesHighOrMissingA1c`, the same compiled SQL on every arm, the UC `cql_*` functions, run from a
laptop, one run per arm. Times are medians of five runs; "cold" is the first run in a new session.

`synthetic-population-small` (4,000 patients):

| Compute | Cold | Warm | One patient |
|---|---|---|---|
| Warehouse Small | 46.8 s | 9.1 s | 7.1 s |
| Serverless Spark | 50.6 s | 12.5 s | 11.2 s |
| Serverless Spark, data from DataFrames through `mount()` | — | 18.6 s | 18.1 s |

Counting one define per query, the warehouse was faster on all 14 defines, by 1.1× to 1.9×.

`synthetic-population-large`, Diabetes cohort (250,000 patients):

| Compute | Cold | Warm | One patient |
|---|---|---|---|
| Warehouse Small | 35.2 s | 15.6 s (11.0–26.3) | 11.3 s |
| Warehouse Medium | 36.0 s | 11.0 s | 11.3 s |
| Warehouse Large | 36.9 s | 12.1 s | 11.9 s |
| Warehouse X-Large | 35.0 s | 11.5 s | 10.9 s |
| Serverless Spark | 72.6 s | 20.1 s | 19.0 s |

- The warehouse is faster at every size; serverless can't be sized.
- Warehouse size barely matters at 250,000 patients, and 62× the data cost about 1.7×. The time is fixed
  overhead (planning about 28 KB of SQL per statement, the UDF calls), not data, so that is what to make faster.
- Session JVM UDFs (`spark.addArtifact` with `registerJavaFunction`) load on serverless, but run in the same
  Safespark sandbox as the UC functions, a preview limit allows 5 UDF calls per query, and a struct return failed.
  They are not a path for the runtime functions.

## Open decisions

| ID | Question |
|---|---|
| `DEC-PUBLISHED-STORE-LOCATION` | Where do published stores live? (A) schemas the customer chooses, examples in `example_measures`; (B) `runtime`. `runtime` is owned and replaced by the deploy and readable by all users |
| `DEC-NOTEBOOK-DEFAULT-RESOLVER` | Does a notebook's `connect()` with no providers use `TaggedResolver` and pick a source when only one is in scope, or must a notebook name its providers? The workspace now has two tagged sources (`synthetic-population-small`, `synthetic-population-large`), so the examples' no-name path already fails there |

`DEC-PROVIDER-RELATION` is settled by "How a retrieve gets its rows" and `BIND-RELATION`.

## Plan

| Step | What | After |
|---|---|---|
| `CLIENT-PROVIDERS` | Requirements for every ID above in `compiler/docs/requirements.md`; SUPERSEDED marks; `DEC-PROVIDER-RELATION` in `docs/decisions/README.md` | — |
| `CLIENT-DATA-PROVIDER` | The interfaces, `RetrieveRequest`, `Relation`, `Overlay`, in the platform-free client core (`checkNeutrality`) | `CLIENT-PROVIDERS` |
| `BIND-PROVIDER-REQUEST` | The one-field-per-role request; `describe` returns each retrieve with an id and its request; relations bound per retrieve and embedded; `BIND-NAMED-ARGS`; conformance tests | `CLIENT-PROVIDERS` |
| `CLIENT-PROVIDER-IDENTITY` | The fingerprint covers provider identities; `reusable` | `CLIENT-DATA-PROVIDER` |
| `CLIENT-DEFAULT-RESOLVER` | `TaggedResolver` and the shipped data providers, using the shared reshape | `BIND-PROVIDER-REQUEST` |
| `CLIENT-TERMINOLOGY-PROVIDER` | Delta and JSON terminology providers, `find_code`, `publish` | `BIND-PROVIDER-REQUEST` |
| `CLIENT-KNOWLEDGE-PROVIDER` | Cells, folder and Delta store; `publish` wraps `publish_folder_library` | `CLIENT-DATA-PROVIDER` |
| `CLIENT-RESULT-STORE` | Today's durable code behind `ResultStore` | `CLIENT-PROVIDER-IDENTITY` |
| `CLIENT-PROVIDER-CALLERS` | `connect()`, `cql()`, `%%cql`, `prepare_calculation`, `run_inline`, the Designer and the agent's MCP tools move to providers | all of the above |
| `CLIENT-PROVIDER-REMOVALS` | Everything in "What is removed" | `CLIENT-PROVIDER-CALLERS` |
| `CLIENT-PROVIDER-DOCS` | Examples 01–05, READMEs, the import skill, `docs/deploy.md`, the deploy's shipped sources | `CLIENT-PROVIDER-REMOVALS` |

Tests: unit tests per provider; conformance tests for the compiler request change, each with the smallest CQL
that shows it (under `compiler/conformance/src/test/resources/cql/`); the live tests (`scripts/test.sh
integration`) for the Designer, the agent's `run_cql` and a notebook run on each shipped provider.
