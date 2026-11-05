# Environment data

An environment's data is its `data` schema (`DATA-ENVIRONMENT-LAYOUT`): one table or view per FHIR type, named for
the type in lower case, returning the contract: the resource as a `resource` struct and optionally `patient_id`,
`min_date` and `max_date` (`BIND-CONTRACT`). A run binds them by name (`BIND-PUBLISHED-VIEW`). A retrieve of a type
with neither fails in the warehouse, naming it. The [client README](../README.md#environments) says what an environment is.

## What the schema holds

| Object | Required | What it is |
|---|---|---|
| `<type>` (`patient`, `condition`, …) | one per type a measure retrieves | A table or view returning one FHIR type's resources |
| `change_log` | no | `(parameters, changed_at)`: which evaluation sets changed (`DATA-CHANGE-LOG-PERIODS`) |

How your tables become FHIR is your logic, inside views, in an environment of your own. The default environment
holds tables, which the deploy lands. The compiler's side of the contract is in [data-binding.md](../../docs/data-binding.md).

If your tables already hold one FHIR type each, `publish_data_source` writes the views for you (`BIND-PUBLISH`,
[client README](../README.md#publish-data-into-an-environment)).

## Change logs

Durable calculations don't read the change log. You decide when to refresh. A change time must be when the data
changed (load time, commit time, `meta.lastUpdated`), never a clinical date. Deletes must move it.

## Value sets and knowledge

Value-set expansions are the environment's `knowledge.valueset`, and libraries its `knowledge.library` and
`knowledge.measure`. The import (`CONTENT-IMPORT-MEASURES`) and publishing write those tables.

## Environments this repository ships

- Synthetic Population Small (`synthetic-population-small`), the default environment: the patients in
  `examples/data`, about 1,000 synthesized per measure, and the example measures. `scripts/deploy/deploy.sh` builds
  it in `uc_catalog` (`DEPLOY-DEFAULT-ENVIRONMENT`, `DATA-SYNTH-SMALL`).
- Synthetic Population Large (`synthetic-population-large`): synthesized cohorts with expected results per patient,
  an environment in its own catalog, built by hand with `scripts/generate-large-data.sh`. See
  [Synthetic data](../../../loader/docs/synthetic-data.md).
