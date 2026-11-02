# Run log

Every Designer run is logged to two Delta tables in `<designer_catalog>.designer` (`APP-RUN-LOG`). An
operator can see who ran what, on which data and warehouse, which defines were computed or reused, how
long each step took, and any errors. The log holds no CQL, patient data or result values.

The column definitions are `RUN_COLUMNS` and `STEP_COLUMNS` in
`designer/server/src/cql_databricks_designer/run_log.py`.

## `designer.runs`: one row per run

| Column | Meaning |
|---|---|
| `run_id` | UUID, also sent to the browser in the run's `done` or `error` frame |
| `user` | the signed-in user's email, from `x-forwarded-email`; empty in a local run |
| `library`, `version` | the measure |
| `data_source` | the data source ID |
| `period_start`, `period_end` | the measurement period |
| `warehouse_id` | the warehouse the run used |
| `trigger` | `refresh` or `automatic` |
| `mode` | `recompute` or `skip_if_computed` |
| `started_at`, `ended_at`, `duration_ms` | the whole run, including compile |
| `compile_ms` | time spent compiling |
| `defines_computed`, `defines_reused` | how many defines were computed and reused |
| `status` | `completed` or `failed` |
| `error` | the error message on failure, truncated |
| `app_version` | the deployed source revision that `scripts/deploy/stage-app.sh` bakes in (`CQL_APP_VERSION`); empty locally |

## `designer.run_steps`: one row per define

| Column | Meaning |
|---|---|
| `run_id` | joins to `designer.runs` |
| `library`, `define` | the define |
| `decision`, `reason` | `computed` or `reused`, and why |
| `result_table` | the result table |
| `statement_id` | the warehouse statement ID; empty when reused |
| `started_at`, `ended_at`, `duration_ms` | the statement's execution time |
| `error` | the error message if this define failed |

## Reading the log

Query the two tables with SQL or a dashboard. For a computed step's profile, rows and cost, join
`designer.run_steps.statement_id` to `system.query.history.statement_id`.

## Configuration and grants

Set in `databricks.local.yml`:

- `designer_catalog`: the catalog holding the `designer` schema. Defaults to `uc_catalog`.
- `run_log_readers_group`: the group that gets `SELECT` on the `designer` schema, so on both tables and any later one
  there. `scripts/deploy/deploy.sh` refuses to deploy while it is empty.

The bundle creates the `designer` schema with its grants: `USE SCHEMA` and `MODIFY` to the Designer App's service
principal, `USE SCHEMA` and `SELECT` to the readers group. They are the schema's whole grant list, so a grant made on
it by hand is revoked by the next deploy. `scripts/deploy/deploy.sh` then runs `scripts/deploy/provision-run-log.py`,
which creates both tables with `CREATE TABLE IF NOT EXISTS`, fails if their columns differ from the definition, and
grants both `USE CATALOG` on `designer_catalog`, which the bundle doesn't hold.

The App only inserts. It writes in the background, so logging never delays a run. A failed write is logged
at ERROR with the run ID (`DEC-RUN-LOG-FAILURE`).
