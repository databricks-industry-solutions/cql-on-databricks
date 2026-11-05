# Shipped sources

`shipped_sources` is the deploy's own data (`DEPLOY-SHIPPED-SOURCES`). The deploy runs it from
`scripts/deploy/provision-examples.py`; it is never shipped to the Volume.

| What | Where |
|---|---|
| The example measures, their libraries and value sets (`examples.py`, `CONTENT-EXAMPLE-PRELOAD`) | `<catalog>.example_measures` (knowledge and terminology) |
| The example patients | The data source `synthetic-population-small` |

The example measures are written into the environment's `knowledge` tables by `knowledge_tables.py`, the only writer
of those tables, after the client's import check, `cql_databricks.data_bindings.measure_import.check_import`
(`CONTENT-IMPORT-MEASURES`), accepts them. Users import measures into a folder of their own with the client's
`import_measures`, through the `import-quality-measures` skill (`CONTENT-IMPORT-SKILL`); nothing they run writes
`knowledge` (`DEC-NO-CONTENT-PUBLISHING`).

The repository ships no official measure content and no list of measures. Everything in `examples/` is
written for this repository.

## Data sources

`population.py` lands a FHIR bulk-data population as a data source: the small population from `examples/data`,
and the large one, which `loader/scripts/synthetic-population/load.py` lands by hand
([synthetic data](../../../loader/docs/synthetic-data.md)). `sources.py` names both. Its store tables hold
the data contract, `patient_id` and `resource`, a typed `STRUCT` from `resource-schemas.json`
(`CONTENT-IMPORTER-FHIR-SCHEMAS`, `BIND-CONTRACT`).
`change_data_log.py` is the exemplar change log the shipped sources carry (`DATA-CHANGE-LOG`).

| Table | Load |
|---|---|
| `retrieve_<type>_data` (a data source's mounts) | Overwrite |

The measure tables and how they load are in the
[data-bindings README](../../../compiler/client/src/cql_databricks/data_bindings/README.md#importing-measures).
