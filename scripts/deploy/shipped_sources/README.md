# Shipped sources

`shipped_sources` is the deploy's own data (`DEPLOY-SHIPPED-SOURCES`). The deploy runs it from
`scripts/deploy/provision-examples.py`; it is never shipped to the Volume.

| What | Where |
|---|---|
| The example measures, their libraries and value sets (`examples.py`, `CONTENT-EXAMPLE-PRELOAD`) | `<catalog>.example_measures` (knowledge and terminology) |
| The example patients | The data source `synthetic-population-small` |

The example measures go through the one import path, `cql_databricks.data_bindings.measure_import.import_measures`
(`CONTENT-IMPORT-MEASURES`), in the client. Other measures are imported with the same function, through the
`import-quality-measures` skill (`CONTENT-IMPORT-SKILL`).

The repository ships no official measure content and no list of measures. Everything in `examples/` is
written for this repository.

## Data sources

`population.py` lands a FHIR bulk-data population as a data source: the small population from `examples/data`,
and the large one, which `loader/scripts/synthetic-population/load.py` lands by hand
([synthetic data](../../../loader/docs/synthetic-data.md)). `sources.py` names both. A data source's mount holds
each resource as `__resource`, a typed `STRUCT` from `resource-schemas.json` (`CONTENT-IMPORTER-FHIR-SCHEMAS`).
`change_data_log.py` is the exemplar change log the shipped sources carry (`DATA-CHANGE-LOG`).

| Table | Load |
|---|---|
| `retrieve_<type>_data` (a data source's mounts) | Overwrite |

The measure tables and how they load are in the
[data-bindings README](../../../compiler/client/src/cql_databricks/data_bindings/README.md#importing-measures).
