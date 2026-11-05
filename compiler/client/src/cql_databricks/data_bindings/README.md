# Data Bindings

Data Bindings reads an environment's knowledge (libraries and measures, with their includes) and its value sets,
and imports measures into an environment. It is the `cql_databricks.data_bindings` package inside the
`cql-databricks` client. It has no store and no App. An environment's schemas are the record.

## Modules

| Module | What it does |
|---|---|
| `tags.py` | `TaggedSchema`: one content schema's kind, environment name, catalog and schema. Nothing is found by tag |
| `layout.py` | The knowledge and terminology layouts, and the check that a schema meets one |
| `corpus.py` | Loads the libraries and measures from knowledge schemas, and resolves include closures |
| `resolver.py` | `ContentResolver`: one environment's knowledge, loaded once for a long-running caller |
| `cql.py` | Reads a library's header, includes, value sets, retrieves and model from CQL text |
| `runner.py` | The SQL runner every read goes through: `spark_runner` and `warehouse_session` |
| `measure_import/` | `import_measures`: writes measures, libraries and value sets as files into a folder of the caller's own; `check_import`, its check |

## Environment layout

An environment is a catalog (`CLIENT-ENVIRONMENT`). Its knowledge is the published views `knowledge.library` and
`knowledge.measure`, each with `url`, `version` and `resource` (`DATA-ENVIRONMENT-LAYOUT`). Its value sets are the
published view `knowledge.valueset(valueset_id, system, code, version)`. Value sets arrive already expanded.
Nothing here computes an expansion. Tables may have more columns than these.

No schema is searched for and no tag is read. `ContentResolver` names the environment's `knowledge` schema and
reads it. It does not check the layout at run time: a missing view fails the read that needs it, and the
warehouse names it (`BIND-PUBLISHED-VIEW`). `load_corpus` checks the layout when asked (`check=True`, the default).

## Reading knowledge

`ContentResolver.corpus()` loads the environment's knowledge on first use and keeps it until `reload()`. Loading
takes seconds. `Corpus.resolve_closure()` resolves a library's `include`s, by name and version, into one closure
that the compiler compiles in one request.

Every read goes through a SQL runner, so a notebook (`spark_runner`) and a SQL warehouse (`warehouse_session`)
use the same code. Every statement sent to a SQL warehouse goes through `warehouse_session`
(`RUN-CLIENT-WAREHOUSE-CONNECTOR`).

## Importing measures

`cql_databricks.data_bindings.measure_import.import_measures` (`CONTENT-IMPORT-MEASURES`) writes measures,
libraries and value sets as files into a `/Volumes` or `/Workspace` folder of the caller's own, laid out as the folder
resolvers read it, and never an environment's `knowledge` (`DEC-NO-CONTENT-PUBLISHING`). It takes FHIR `Measure`,
`Library` and `ValueSet` resources (or Bundles) and value-set code rows the caller has read, and fetches nothing. The
`import-quality-measures` skill is how a user brings content to it (`CONTENT-IMPORT-SKILL`). The deploy loads
`examples/` into the default environment's `knowledge` tables after the same check, `check_import`
(`scripts/deploy/shipped_sources/knowledge_tables.py`), which loads each table this way:

| Table | Load |
|---|---|
| `measure`, `library` | MERGE on `(url, version)` |
| `valueset` | Each value set's earlier codes deleted, then its new codes appended |

`measure` and `library` hold each resource as a `VARIANT` column, `resource`, beside `url` and `version`.

## Tests

```bash
.venv/bin/python -m pytest -q compiler/client/tests/data_bindings
```

`scripts/test.sh` runs them.
