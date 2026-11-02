# Data Bindings

Data Bindings finds what a calculation needs by Unity Catalog tag: data sources, value sets and CQL
libraries. It is the `cql_databricks.data_bindings` package inside the
`cql-databricks` client. It has no store and no App. The tagged schemas are the record.

## Modules

| Module | What it does |
|---|---|
| `tags.py` | The three tag keys, catalog scopes, and the lookup of tagged schemas |
| `layout.py` | The knowledge and terminology layouts, and the check that a schema meets one |
| `corpus.py` | Loads the libraries and measures from the knowledge schemas, and resolves include closures |
| `resolver.py` | `ContentResolver`: caches the tag lookups and the corpus for a long-running caller |
| `cql.py` | Reads a library's header, includes, value sets, retrieves and model from CQL text |
| `runner.py` | The SQL runner every read goes through: `spark_runner` and `warehouse_session` |
| `measure_import/` | `import_measures`: writes measures, libraries and value sets into a schema and tags it |

## Tags

| Tag on a schema | What the schema holds |
|---|---|
| `cql_databricks_data_source = <name>` | A data source: `retrieve_<Type>` functions. See [data sources](../../../docs/data-sources.md) |
| `cql_databricks_knowledge = <name>` | Libraries and measures: tables `library` and `measure`, each with `url`, `version` and `resource` |
| `cql_databricks_terminology = <name>` | Value sets: table `valueset` with `valueset_id`, `system`, `code` and `version` |

Value sets arrive already expanded. Nothing here computes an expansion. Tables may have more columns than
these. The compiler client checks the data-source layout itself (`cql_databricks.compiler.data_source`).

## Discovery

Lookups read `system.information_schema`, so each caller sees only the schemas its grants allow. They
search the caller's ordered catalog scopes. The earliest scope wins when a name
is found twice. A caller that names no scopes searches every catalog it can see, and a warning says how to
confine it. The [client README](../../../README.md) shows how to set the scopes.

A schema that doesn't match its layout fails the lookup. Nothing is skipped.

`ContentResolver.resolve_closure()` resolves a library's `include`s from the knowledge schemas, by name
and version, into one closure that the compiler compiles in one request.

Tag lookups take seconds. `ContentResolver` does them once and keeps the result until `reload()`.

Every read goes through a SQL runner, so a notebook (`spark_runner`) and a SQL warehouse (`warehouse_session`)
use the same code. Every statement sent to a SQL warehouse goes through `warehouse_session`
(`RUN-CLIENT-WAREHOUSE-CONNECTOR`).

## Importing measures

`cql_databricks.data_bindings.measure_import.import_measures` (`CONTENT-IMPORT-MEASURES`) writes knowledge and
terminology schemas and tags them. It takes FHIR `Measure`, `Library` and `ValueSet` resources (or Bundles) and
value-set code rows the caller has read. It reads no file and fetches nothing. The `import-quality-measures` skill
is how a user brings content to it (`CONTENT-IMPORT-SKILL`), and the deploy loads `examples/` through it
(`scripts/deploy/shipped_sources`). It needs a Spark session, so `cql_databricks.data_bindings` does not import it.

| Table | Load |
|---|---|
| `measure`, `library` | MERGE on `(url, version)` |
| `valueset` | Each value set's earlier codes deleted, then its new codes appended |

`measure` and `library` hold each resource as a `VARIANT` column, `resource`, beside `url` and `version`.

You build data sources yourself, over your own tables.

## Tests

```bash
.venv/bin/python -m pytest -q compiler/client/tests/data_bindings
```

`scripts/test.sh` runs them.
