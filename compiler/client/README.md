# `cql-databricks`, the Python client

`cql-databricks` is the Python package you use to run CQL in your own notebooks and Jobs. It compiles CQL
through the Compiler Service App and returns lazy Spark DataFrames. The SQL runs on your cluster or SQL
warehouse, as you. Results go where you put them.

It needs Python 3.10 or later, and the bundle deployed ([deploy guide](../../docs/deploy.md)). The deploy
publishes the wheels to a Volume. Install them in a notebook:

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

In a notebook this needs no settings. The client finds the compiler App in the workspace App registry,
signs in as you, and finds data sources, value sets and the runtime functions through Unity Catalog tags.

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
- Each run re-creates the views of every define in the session library.
- `using` works as it does for a file: the same line twice is fine, a second FHIR version fails.
- A fragment joins only if the whole library still compiles. If it breaks an earlier define, it fails,
  and the session library stays as it was.
- Each fragment starts in the default context. The compiler lowers a whole library in one context: once
  any fragment declares `context Patient`, every define in the session library runs per patient.
- `include` works in a fragment, so a fragment can use a library from your folder or from a cell.
- CQL with a `library` header is its own library, as before.
- `reset(spark)` clears the session library.

## Which data a run reads

`exec()` and `materialize()` read the same data source (`CLIENT-EXEC-DISCOVERY`): a schema tagged
`cql_databricks_data_source`, found in your catalog scopes.

- `connect(spark, data_source="acme_fhir")`, or `spark.cqldatabricks.dataSource`, names one by its tag value.
- With none named, the only data source in scope is used. Several fail, listing their names.
- CQL that retrieves nothing needs no data source.
- A data source without a function for a type the CQL retrieves fails before the run, naming the type.
- `connect(spark, data_catalog="cat.schema")` reads that schema's `retrieve_<Type>` functions instead.

Value sets come from the `valuesets` view beside the runtime functions, which covers every schema tagged
`cql_databricks_terminology`. `connect(valueset_table=...)` names a table instead.

## Data in a session

`mount()` puts FHIR data and value sets on your Spark session, with nothing written to a catalog
(`CLIENT-SESSION-MOUNT`). It ends when the session ends. Every client on the session reads it, the `%%cql`
magic's too, and it replaces the data-source search.

```python
from cql_databricks.compiler import FhirJson, mount, reset

reset(spark)                                                    # start clean
mount(spark, "./data", valuesets="./valuesets")                 # a folder of <Type>.ndjson files
mount(spark, {"Patient": patients_df, "Observation": "acme.silver.observation"})   # replaces the first
mount(spark, FhirJson("acme.bronze.fhir_raw", column="resource"))                  # raw JSON, mixed types
```

- A string is a folder only if it contains `/`. A table name is `catalog.schema.table`. Nothing is guessed.
- Each source is checked against the model first. Any error refuses the whole mount, listing every problem.
- A DataFrame or table is reshaped to the model: missing fields read as null, extra fields are dropped.
- Value sets are a folder of `{valueset_id, system, code}` rows, a table, or a DataFrame with those columns.
- A retrieve of a type you didn't mount fails, naming the mounted types.
- `data_source=` still names a tagged data source instead. `materialize()` refuses a mount: kept results
  belong to a data source.
- `reset(spark)` drops the mount, the libraries your `%%cql` cells declared, and the session library
  (`CLIENT-SESSION-RESET`).

Files under `/Volumes` are read by Spark. Other files are read on the driver, up to 64 MiB per mount.

## Publish a data source

`publish_data_source()` writes a data source over your tables (`BIND-PUBLISH`): the schema, one
`retrieve_<Type>` function per type, and the `cql_databricks_data_source` tag. It writes no data. Nothing runs
until `run()`.

```python
from cql_databricks.compiler import publish_data_source

plan = publish_data_source(spark, "acme.cql_fhir", name="acme_fhir", description="Acme FHIR R4, nightly",
                           fhir={"Patient": "acme.silver.patient", "Observation": "acme.silver.observation"})
print(plan.sql)       # review
plan.issues           # every finding against the model, any severity
plan.run()            # refuses on any ERROR; then creates, tags, and checks the layout
```

- Sources are tables only, `catalog.schema.table`. For raw JSON or a DataFrame, write
  `FhirJson(...).to_df(spark, type)` or the DataFrame to a table first, then publish that table.
- Each function reads the whole table and ignores the pushdown arguments; the run filters above it.
- It runs as you. You need `CREATE SCHEMA`, `CREATE FUNCTION` and `APPLY TAG`.
- `run(runner=...)` runs the same statements through a SQL warehouse runner instead of the session.

## The FHIR model

`resource_schema()` returns the struct the compiler expects for each FHIR type (`CLIENT-RESOURCE-SCHEMA`):
what a `retrieve_<Type>` function returns in `__resource`.

```python
client.resource_schema("Observation").struct   # a PySpark StructType
client.resource_schema("Observation").ddl      # the same, as DDL
client.resource_schema()                       # every modeled type, keyed by type
```

## Discovery scopes

Discovery reads an ordered list of catalog scopes (`CLIENT-DISCOVERY-SCOPE`). Each entry is a `catalog`, a
`catalog.schema`, or `*` for every catalog you can see. When a name is found in more than one scope, the
earliest wins.

```python
client = connect(spark, catalog_scopes=["my_catalog.fhir", "shared_catalog"])
```

Or set `CQL_DATABRICKS_CATALOG_SCOPES` to a comma-separated list (`my_catalog.fhir,shared_catalog`), which the
`%%cql` magic and the module-level `cql()` read too. Don't use the Spark conf: serverless refuses to set it.
With no list set, discovery searches every catalog and logs a warning. An empty list is an error.

Data sources, value sets and libraries are found by tag. See the
[Data Bindings README](src/cql_databricks/data_bindings/README.md).

## Durable results

`exec()` computes every time and keeps nothing. `materialize()` writes each define to a Delta table you
own, and reuses a table when the define computes the same thing: the same SQL, parameter values, upstream
defines and data source (`RUN-CLIENT-DURABLE`, `RUN-CLIENT-RESULT-IDENTITY`).

```python
from cql_databricks.result import materialize

query = client.cql(library="HypertensionBPControl") \
              .with_measurement_period("2026-01-01", "2026-12-31")
results = materialize(query, "Initial Population", "Numerator",
                      spark=spark,
                      destination=("my_catalog", "cql_results"))
results.read("Initial Population").display()
```

- The destination schema must exist. `ensure_private_destination()` creates a private one for you.
- A durable run reads a tagged data source. Its address is part of each result's identity.
- Pass `mode="recompute"` to rebuild instead of reuse.
- Pass `run=` with a SQL warehouse runner instead of `spark=` to run on a warehouse.
- The client doesn't track source changes. Refresh when your data, value sets or runtime change.
- Results are written only to `destination`. A define it holds no reusable result for is reused from the
  `catalog.schema` entries of your catalog scopes, in order (`CLIENT-DISCOVERY-SCOPE`). Whole catalogs are
  never read for results. List only schemas whose results you trust: nothing checks who wrote them.
  `catalog_scopes=` on `materialize()` overrides the client's list.

Tables are tagged in Unity Catalog, so a later session, or another user with grants, can find and read
them (`resolve_results()`, `search_results()`). [calculation-architecture.md](docs/calculation-architecture.md)
covers how results are stored and reused.

## The explicit client

The package has two layers. The core reaches the compiler over plain HTTP with standard OAuth and imports
no platform SDK. The Databricks layer adds App discovery, ambient sign-in and Unity Catalog discovery.

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
          .library_source("cql/").data_catalog("fhir").spark(spark).build())
```

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
`tests/databricks/` covers the Databricks layer with fakes.
