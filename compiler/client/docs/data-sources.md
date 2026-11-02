# Environment data

An environment's data is its `data` schema (`DATA-ENVIRONMENT-LAYOUT`): one published view per FHIR type, named for
the type in lower case, returning the contract `(__subject_id, __resource)` (`BIND-CONTRACT`). A run binds the views by
name and reads no catalog metadata (`BIND-PUBLISHED-VIEW`). A retrieve of a type with no view fails in the warehouse,
naming the view. The [client README](../README.md#environments) says what an environment is.

## What the schema holds

| Object | Required | What it is |
|---|---|---|
| `<type>` (`patient`, `condition`, …) | one per type a measure retrieves | A view returning one FHIR type's resources |
| `change_log` | no | `(parameters, changed_at)`: which evaluation sets changed (`DATA-CHANGE-LOG-PERIODS`) |

How your tables become FHIR is your logic, inside the views. By default each view selects its table in
`data_store`. The compiler's side of the contract is in [data-binding.md](../../docs/data-binding.md).

If your tables already hold one FHIR type each, `publish_data_source` writes the views for you (`BIND-PUBLISH`,
[client README](../README.md#publish-data-into-an-environment)).

## Change logs

Durable calculations don't read the change log. You decide when to refresh. A change time must be when the data
changed (load time, commit time, `meta.lastUpdated`), never a clinical date. Deletes must move it.

## Value sets and knowledge

Value-set expansions are the environment's `terminology.valueset` view, and libraries its `knowledge.library` and
`knowledge.measure` views. The import (`CONTENT-IMPORT-MEASURES`) writes their store tables.

## Environments this repository ships

- Synthetic Population Small (`synthetic-population-small`), the default environment: the patients in
  `examples/data`, about 1,000 synthesized per measure, and the example measures. `scripts/deploy/deploy.sh` builds
  it in `uc_catalog` (`DEPLOY-DEFAULT-ENVIRONMENT`, `DATA-SYNTH-SMALL`).
- Synthetic Population Large (`synthetic-population-large`): synthesized cohorts with expected results per patient,
  an environment in its own catalog, built by hand with `scripts/generate-large-data.sh`. See
  [Synthetic data](../../../loader/docs/synthetic-data.md).
