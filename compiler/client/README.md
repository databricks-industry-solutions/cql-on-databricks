# `cql-databricks`, the Python client

`cql-databricks` is the Python package you use to run CQL in your own notebooks and Jobs. It compiles CQL
through the Compiler Service App and returns lazy Spark DataFrames. The SQL runs on your cluster or SQL
warehouse, as you. Results go where you put them.

It needs Python 3.10 or later, and the bundle deployed ([deploy guide](../../docs/deploy.md)). On serverless,
pick the **CQL on Databricks** base environment in a notebook's Environment pane: the deploy builds it with the
client in it (`DEPLOY-BASE-ENVIRONMENT`), so there is nothing to install. On other compute, install the wheel the
deploy put in the runtime Volume:

```python
%pip install /Volumes/<catalog>/runtime/artifacts/wheels/cql_databricks-*.whl
```

## Run CQL

`cql()` returns a lazy query. `exec()` runs it and returns a DataFrame. `exec(*targets)` is the only
terminal.

```python
from cql_databricks.compiler import cql

cql('define "Two": 2 + 2').exec("Two").display()
```

In a notebook this needs no settings. The client finds the compiler App in the workspace App registry, signs
in as you, and runs in the default environment: its data, value sets and libraries (see below).

To run a library from a folder of `.cql` files, by name:

```python
from cql_databricks.compiler import connect

client = connect(spark, library_path="/Workspace/Users/me/measure-content/input/cql")
result = (client.cql(library="HypertensionBPControl")
          .with_measurement_period("2026-01-01", "2026-12-31")
          .exec("Initial Population"))
```

`exec("X")` returns one DataFrame. `exec()` or `exec("X", "Y")` returns a dict of define name to
DataFrame. `describe()` returns the library's defines, their shapes and dependencies.

A library's `exec()` reads kept results where they match: yours, then the environment's official ones
(`CLIENT-QUERY-REUSE`). Only the defines with no kept result are computed. It writes nothing.

- A kept result is as fresh as when it was computed. Pass `recompute=True` to `connect()` or `cql()`, or call
  `.with_recompute()`, to compute everything from the data as it is now.
- CQL with no `library` header, and data from a resolver that is never reused (a DataFrame), are always computed.
- `boundaries=` needs `recompute=True`.

## The session library

CQL with no `library` header joins one library per Spark session (`SVC-NOTEBOOK-SESSION-SCOPE`). This
covers both `%%cql` cells and `cql("...")` strings. It works like views in a SQL notebook: a later
fragment sees earlier defines by name, and redeclaring a name replaces it.

```python
cql('define "Two": 2').exec("Two")
cql('define "Four": "Two" * 2').exec("Four")   # sees Two
cql('define "Two": 3').exec("Two")             # replaces Two; Four is now 6
```

- `exec()` with no targets runs only the new fragment's defines.
- `cql()` with no source is the session library as it stands. This is how a Python cell runs what `%%cql`
  cells declared: `cql().exec("Four")`. Its `exec()` with no targets runs the populations, else every define.
- A `%%cql` cell declares and never runs (`SVC-NOTEBOOK-CQL-MAGIC`). It compiles, so an error shows in that
  cell. The magic takes no options.
- Each run re-creates the views of every define in the session library.
- `using` works as it does for a file: the same line twice is fine, a second FHIR version fails.
- A fragment joins only if the whole library still compiles. If it breaks an earlier define, it fails,
  and the session library stays as it was.
- Each fragment starts in the default context. The compiler lowers a whole library in one context: once
  any fragment declares `context Patient`, every define in the session library runs per patient.
- `include` works in a fragment, so a fragment can use a library from your folder or from a cell.
- CQL with a `library` header is its own library, as before.
- `reset(spark)` clears the session library, the `%%cql` cells and the temporary views the client made, and
  returns the session's resolvers to the environment's (`CLIENT-SESSION-RESET`).

## Environments

Every run is in one environment (`CLIENT-ENVIRONMENT`). An environment is a Unity Catalog catalog tagged
`cql_databricks_environment = <name>`, with standard schemas (`DATA-ENVIRONMENT-LAYOUT`):

| Schema | Holds |
|---|---|
| `data` | One table or view per FHIR type, named for the type in lower case (`condition`), returning the resource as a `resource` struct and optionally `patient_id`, `min_date` and `max_date` (`BIND-CONTRACT`), and optionally `change_log` |
| `knowledge` | The tables `library`, `measure` and `valueset(valueset_id, system, code, version)` |
| `results` | Published results |
| `draft_results` | Each user's own results |

The deploy builds the default environment in `uc_catalog` and tags it `cql_databricks_environment_default =
true` (`DEPLOY-DEFAULT-ENVIRONMENT`). A run with nothing named uses it. Name another one:

```python
client = connect(spark, environment="synthetic-population-large")
```

Or set `CQL_DATABRICKS_ENVIRONMENT`. Environments are found with one query of
`system.information_schema.catalog_tags`, as you, so you see only catalogs you can read. Two defaults, no
default, a name that isn't there, or a layout version the client doesn't read each fail, naming what was found
(`CLIENT-ENVIRONMENT-RESOLVER`).

A run binds the environment's views by name, in one compiler call, and reads no other catalog metadata
(`BIND-PUBLISHED-VIEW`). A view that is missing fails in the warehouse, naming it. CQL that retrieves nothing
reads no data.

## Your own data, value sets and libraries

Each role can be replaced for a client (`CLIENT-RESOLVERS`). Pass a resolver to `connect()`:

```python
from cql_databricks.compiler import connect
from cql_databricks.resolvers import (DataFrameDataResolver, EnvironmentResolver, FolderTerminologyResolver, Overlay,
                                      TableDataResolver)
from cql_databricks.data_bindings import spark_runner

client = connect(spark, data=TableDataResolver({"Patient": "acme.silver.patient"}))
client = connect(spark, terminology=FolderTerminologyResolver("/Volumes/acme/dev/valuesets"))
# Develop a mapping: DataFrames for the types you have, the default environment's views for the rest.
environments = EnvironmentResolver(spark_runner(spark))
client = connect(spark, data=Overlay(DataFrameDataResolver({"Observation": observations_df}),
                                     environments.data(environments.default())))
```

| Role | Shipped resolvers |
|---|---|
| Data | `SchemaDataResolver` (an environment's `data` schema), `TableDataResolver`, `VolumeDataResolver`, `DataFrameDataResolver` |
| Terminology | `TableTerminologyResolver`, `FolderTerminologyResolver`, `DataFrameTerminologyResolver` |
| Knowledge | `TableKnowledgeResolver`, `FolderKnowledgeResolver`, `CellsKnowledgeResolver` |

- A DataFrame resolver is never reused or kept, and a SQL warehouse can't read it.
- `Overlay(dev, base)` serves each type, value set or library from `dev` when it has it, else from `base`
  (`CLIENT-RESOLVER-OVERLAY`).
- A resolver that binds per call site makes the run ask the compiler for the call sites first, so it costs a
  second compiler call (`BIND-CALL-SITE`).
- Libraries resolve from your `%%cql` cells, then `library_path`, then the environment's knowledge.

`%%cql` cells and `cql()` calls use one client the session builds for you. Set its resolvers with
`use_resolvers()` (`CLIENT-SESSION-RESOLVERS`):

```python
from cql_databricks.compiler import use_resolvers
from cql_databricks.resolvers import FolderTerminologyResolver, VolumeDataResolver

use_resolvers(data=VolumeDataResolver("/Volumes/acme/dev/tests/fhir"),
              terminology=FolderTerminologyResolver("/Volumes/acme/dev/tests/valuesets"))
```

- Every later cell reads through them. A role you don't pass is the environment's.
- Each call sets every role, so `use_resolvers()` with nothing returns to the environment. So does
  `reset(spark)`.
- They live in the Python process: restarting Python drops them.
- `results=` replaces where `exec()` reads kept results.
- A client from `connect()` is not affected. Pass it its resolvers.
- Notebook `07_resolution_paths` runs one question over every source: the environment's tables, the same content
  as files in its `files` volumes, and DataFrames.

## Publish data into an environment

`publish_data_source()` writes an environment's data views over your tables (`BIND-PUBLISH`): one
`CREATE OR REPLACE VIEW <catalog>.data.<type>` per type, with the reshape of the table as its body. It writes
no data and tags nothing. Nothing runs until `run()`.

```python
from cql_databricks.compiler import publish_data_source

plan = publish_data_source(spark, "acme_env",
                           fhir={"Patient": "acme.silver.patient", "Observation": "acme.silver.observation"})
print(plan.sql)       # review
plan.issues           # every finding against the model, any severity
plan.run()            # refuses on any ERROR
```

- Sources are tables only, `catalog.schema.table`. For raw JSON or a DataFrame, write
  `FhirJson(...).to_df(spark, type)` or the DataFrame to a table first, then publish that table.
- Each view reads the whole table; the run filters above it.
- It runs as you. You need `CREATE TABLE` on the environment's `data` schema.
- `run(runner=...)` runs the same statements through a SQL warehouse runner instead of the session.

## The FHIR model

`resource_schema()` returns the struct the compiler expects for each FHIR type (`CLIENT-RESOURCE-SCHEMA`):
what a data view returns in `__resource` (`BIND-CONTRACT`).

```python
client.resource_schema("Observation").struct   # a PySpark StructType
client.resource_schema("Observation").ddl      # the same, as DDL
client.resource_schema()                       # every modeled type, keyed by type
```

## Durable results

`exec()` computes every time and keeps nothing. `materialize()` writes each define to a Delta table you own, and
reuses a table when the define computes the same thing: the same SQL, parameter values, upstream defines, bound
relations and value-set codes (`RUN-CLIENT-DURABLE`, `RUN-CLIENT-RESULT-IDENTITY`, `CLIENT-RESOLVER-IDENTITY`).
Results are found and written through a result store you pass (`CLIENT-RESULT-RESOLVER`).

```python
from cql_databricks.resolvers import EnvironmentResolver
from cql_databricks.data_bindings import spark_runner
from cql_databricks.resolvers import Overlay
from cql_databricks.result import OFFICIAL, SchemaResultResolver, SchemaResultStore, materialize

environments = EnvironmentResolver(spark_runner(spark))
env = environments.default()
query = client.cql(library="HypertensionBPControl") \
              .with_measurement_period("2026-01-01", "2026-12-31")
# Your own results in a schema of your own, then the official ones in the environment's `results`.
store = Overlay(SchemaResultStore(("my_catalog", "my_results"), spark=spark),
                SchemaResultResolver(env.address("results"), spark=spark, reuse=OFFICIAL))
results = materialize(query, "Initial Population", "Numerator", spark=spark, results=store)
results.read("Initial Population").display()
```

- Each result resolver reads one schema: `reuse="own"` (the default) reuses only your results there, and
  `reuse="official"` anyone's. `Overlay(first, then)` asks `then` only for what `first` did not find, and writes
  through `first` when it is a store (`CLIENT-RESOLVER-OVERLAY`). Here your own results come first, then the
  environment's official ones (`RUN-CLIENT-RESULT-OWNERSHIP`). What isn't found is computed and written to the
  store's schema, as you. Another user's results are reused only from a schema read with `reuse="official"`.
- `results` is official by construction: whoever the implementer grants write access decides what is there. Its
  publishers write it with `environments.official_store(env)`, which takes only a library from the environment's
  own knowledge over its own data and terminology. CQL text, a folder library, a volume, a DataFrame or a custom
  resolver is refused before it computes (`RUN-CLIENT-DRAFT-RESULTS`).
- `environments.draft_store(env)` is the same overlay over the environment's shared `draft_results`, as the
  Designer's runs use. It needs `CREATE TABLE` on that schema. `environments.results(env)` reads both layers and
  writes nothing.
- `SessionResultStore(spark=spark)` (or `run=`) keeps results for the session only, as temporary tables
  (`CLIENT-SESSION-RESULTS`). Nothing outside the session sees them, and they go when it ends.

Pick what a run reuses and keeps by layering them. `official` is
`SchemaResultResolver(env.address("results"), spark=spark, reuse=OFFICIAL)`, `own` is
`SchemaResultResolver(env.address("draft_results"), spark=spark)`, and `session` is
`SessionResultStore(spark=spark)`:

| Reuse | Keep what is computed | `results=` |
|---|---|---|
| Your own, then official | in `draft_results` | `environments.draft_store(env)` |
| Official only | nothing: read only, so `resolve_results`, inline runs and `cql()` | `official` |
| Official, then this session's | for the session | `Overlay(session, official)` |
| This session's only | for the session | `session` |
| Your own, then this session's | for the session | `Overlay(session, own)` |

A durable run needs a store to write what it computes, so "official only" works with `materialize` as
"official, then this session's".
- Table names carry the writer's digest, so two identities computing one result write two tables. Two runs of
  yours computing one result write the same table. Each looks the result up again just before it writes, and again
  if its write fails, and reuses it if another run finished it.
- Pass `mode="recompute"` to rebuild instead of reuse, or `computed_since=` to pass over older results.
- Pass `run=` with a SQL warehouse runner instead of `spark=` to run on a warehouse.
- The client doesn't track data changes. A kept result is as fresh as when it was computed. A value set's codes are
  part of the identity, so an edited value set recomputes.

A later session reads results back with `resolve_results(query, ..., results=resolver)`, which never computes, and
lists a store's results with `store.search()`. [calculation-architecture.md](docs/calculation-architecture.md)
covers how results are stored and reused.

## Inline runs on a SQL warehouse

`run_inline()` runs a CQL library on a SQL warehouse session and keeps nothing (`CLIENT-INLINE-WAREHOUSE`).
It is the warehouse form of `exec()`: temporary views only, dropped when you close the run.

```python
from cql_databricks.result import run_inline

resolvers = environments.resolvers(env, environments.results(env))
with run_inline(content=content, run=session, compiler=compiler, resolvers=resolvers,
                cql=library_text, runtime_catalog="acme", period=("2026-01-01", "2026-12-31")) as run:
    run.counts()               # distinct subjects per define, as written
    run.score("proportion")    # the measure's score
    run.reused                 # the defines read from kept results, and their tables
```

- With a result resolver, a define already computed (yours, then the official one) is read from its table in place
  of computing it, and only the rest become views. Without one, or over a resolver that is not reusable, nothing is
  reused.
- `compiler` must render barriers as views: use `CqlDatabricksCompileProvider`. A `CACHE TABLE` is refused.
- Each run has its own view prefix, so two runs on one session never replace each other's views.
- Includes resolve from `content`. A view reads the data as it is now; nothing is written or tagged.

## Scoring a measure

`cql_databricks.result.insights.score` scores a measure (`RUN-CLIENT-MEASURE-SCORE`). Populations are counted
as written. Only the score applies the HL7 CQF Measures cascade. A proportion's rate is the Numerator within
the Denominator less its Exclusions, over the Denominator less its Exclusions and the Exceptions outside the
Numerator.

- `selection_score(...)` scores saved results; `InlineRun.score(...)` scores an inline run.
- The scoring type is declared: `parse_scoring(measure_resource)` reads `Measure.scoring`. It is never inferred.
- Proportion, ratio and cohort are scored. Continuous-variable is reported unsupported.
- The score carries its own counted numerator and base, which need not match the population counts.

## The explicit client

The package has two layers. The core reaches the compiler over plain HTTP with standard OAuth and imports
no platform SDK. The Databricks layer adds App discovery, ambient sign-in and the environment.

Ambient sign-in exchanges the notebook's or Job's own token for one scoped to the compiler App. In a Job
the exchange sometimes fails with `invalid_grant` for a minute or two and then succeeds, so the
Databricks layer retries that error for up to 3 minutes, logging a warning for each retry
(`SVC-EXCHANGE-RETRY`). Other errors fail at once.

`cql()` picks the layer. Given `client=` or `endpoint=`, it uses the core and runs no discovery. Given
neither, it discovers. `cql_explicit` is the core binder under its own name.

Build the core client against a named endpoint for local development and tests. The conformance suite's
end-to-end lane drives this path against a local compile service:

```python
from cql_databricks.compiler import CqlClient
client = (CqlClient.builder()
          .endpoint("http://localhost:8080").auth_bearer(token)
          .library_source("cql/").spark(spark).data_binder(binder).build())
```

`binder` is a `ResolverBinder` over the run's resolvers (`BIND-CALL-SITE`). Without one, only CQL that retrieves
nothing compiles: a retrieve or a value-set membership fails, naming its call site.

This path never imports the Databricks SDK, although the SDK is a base dependency
(`test_remote_kind_never_imports_the_platform_layer`).

## Compatibility

- Published names keep their meaning. New behaviour gets a new name.
- Public dataclasses are keyword-only.
- Nothing is removed within a major version.
- The emitted SQL is internal. The public surface is the API, `describe()` and the DataFrames.

## Install for development

```bash
pip install -e compiler/client                 # the client
pip install -e 'compiler/client[spark]'        # + pyspark, for local Spark
```

`pyspark` comes from either the `spark` extra or `databricks-connect`. Don't install both.

## Tests

```bash
./gradlew clientTest
```

`clientTest` runs `unittest` in the root venv that `scripts/setup.sh` creates. It fails if the venv is
missing. The tests need no workspace. `tests/` covers the core and `tests/data_bindings/` the bindings.
`tests/databricks_client/` covers the Databricks layer with fakes.
