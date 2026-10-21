# Data Bindings

Data Bindings finds what a calculation needs by Unity Catalog tag: data sources, value sets and CQL
libraries (`CONTENT-RESOLVER`). It is the `cql_databricks.data_bindings` package inside the
`cql-databricks` client. It has no store and no App. The tagged schemas are the record.

## Modules

| Module | What it does |
|---|---|
| `tags.py` | The three tag keys, catalog scopes, and the lookup of tagged schemas |
| `layout.py` | The knowledge and terminology layouts, and the check that a schema meets one |
| `corpus.py` | Loads the libraries and measures from the knowledge schemas, and resolves include closures |
| `resolver.py` | `ContentResolver`: caches the tag lookups and the corpus for a long-running caller |
| `cql.py` | Reads a library's header, includes, value sets, retrieves and model from CQL text |
| `runner.py` | The SQL runner every read goes through: `spark_runner` and `warehouse_runner` |

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
search the caller's ordered catalog scopes (`CLIENT-DISCOVERY-SCOPE`). The earliest scope wins when a name
is found twice. A caller that names no scopes searches every catalog it can see, and a warning says how to
confine it. The [client README](../../../README.md) shows how to set the scopes.

A schema that doesn't match its layout fails the lookup. Nothing is skipped.

`ContentResolver.resolve_closure()` resolves a library's `include`s from the knowledge schemas, by name
and version, into one closure that the compiler compiles in one request.

Tag lookups take seconds. `ContentResolver` does them once and keeps the result until `reload()`.

Every read goes through a SQL runner, so a notebook (`spark_runner`) and an App (`warehouse_runner`) use
the same code.

## Writing these schemas

The importer writes and tags knowledge and terminology schemas ([loader](../../../../../loader/README.md)).
You build data sources yourself, over your own tables.

## Tests

```bash
.venv/bin/python -m pytest -q compiler/client/tests/data_bindings
```

`scripts/test.sh` runs them.
