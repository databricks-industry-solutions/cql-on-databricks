# Data sources

A data source is a Unity Catalog schema tagged `cql_databricks_data_source = <name>`
(`DATA-SOURCE-UC-TAG`). The tag's value is the name callers use. The schema's `COMMENT` is its
description. The client finds data sources by tag, within your catalog scopes, and checks each one's layout
with the compiler client's check (`cql_databricks.compiler.data_source`, `BIND-DATA-SOURCE-LAYOUT`). A
source that fails the check fails the whole lookup.

## What the schema holds

| Object | Required | What it is |
|---|---|---|
| `retrieve_<Type>` | at least one | A function returning one FHIR type's resources (`BIND-RETRIEVE-UDF`) |
| `change_data_log` | no | `(data_type, updated_at)`: when each type's data last changed (`DATA-CHANGE-LOG`) |
| `change_log` | no | `(parameters, changed_at)`: which evaluation sets changed (`DATA-CHANGE-LOG-PERIODS`) |

A type is bound when its `retrieve_<Type>` function exists. A `retrieve_` function that names no FHIR type
the compiler models is a layout error.

How your tables become FHIR is your logic, inside the `retrieve_<Type>` functions. The compiler's side of
the contract is in [data-binding.md](../../docs/data-binding.md).

If your tables already hold one FHIR type each, `publish_data_source` writes the schema, the functions and
the tag for you (`BIND-PUBLISH`, [client README](../README.md#publish-a-data-source)).

## Change logs

Durable calculations don't read the change logs. You decide when to refresh. A change time must be when
the data changed (load time, commit time, `meta.lastUpdated`), never a clinical date. Deletes must move
it.

## Value sets and knowledge

Value-set expansions and libraries come from schemas tagged `cql_databricks_terminology` and
`cql_databricks_knowledge`. See [Data Bindings](../src/cql_databricks/data_bindings/README.md).

## Sources this repository ships

- Synthetic Population Small (`synthetic-population-small`): the patients in `examples/data` the sample measures
  run over, about 1,000 synthesized per measure. `scripts/deploy/deploy.sh` loads them (`DATA-SYNTH-SMALL`).
- Synthetic Population Large: synthesized cohorts with expected results per patient, loaded with
  `loader/scripts/synthetic-population/load.py`. See [Synthetic data](../../../loader/docs/synthetic-data.md).
