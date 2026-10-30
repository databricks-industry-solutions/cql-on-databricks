"""APP-RUN-LOG, DEC-RUN-LOG-LOCATION — create the Designer run log tables and grant them, as the deployer.

Run by ``scripts/deploy/deploy.sh`` after the bundle deploy (which creates the ``designer`` schema) and after the Apps
exist (the grant names the Designer App's service principal). Idempotent. The tables' one definition is
``cql_databricks_designer.run_log``; the App only inserts and never runs this DDL.

1. ``CREATE TABLE IF NOT EXISTS`` both tables, then compare their columns with that definition. A table that
   differs fails the deploy: ``IF NOT EXISTS`` does not change an existing table, so a column added to the
   definition needs an ``ALTER TABLE`` here, not a silent mismatch.
2. The Designer App's service principal: ``USE CATALOG``, ``USE SCHEMA`` and ``MODIFY`` on both tables.
3. The readers group: ``USE CATALOG``, ``USE SCHEMA`` and ``SELECT`` on both tables (not the whole schema, which
   may later hold other tables).

    python scripts/deploy/provision-run-log.py --catalog <designer_catalog> --warehouse <id> \\
        --designer-app <Designer App name> --readers-group <group>
"""
from __future__ import annotations

import argparse
import sys


def column_problems(run, catalog: str) -> list[str]:
    """Each way the deployed tables differ from ``run_log``'s column definition; empty when they match."""
    from cql_databricks_designer import run_log
    rows = run(f"SELECT table_name, column_name, full_data_type FROM `{catalog}`.information_schema.columns "
               "WHERE table_schema = :schema AND table_name IN (:runs, :steps) "
               "ORDER BY table_name, ordinal_position",
               {"schema": run_log.SCHEMA, "runs": run_log.RUNS, "steps": run_log.STEPS})
    problems = []
    for table, columns in ((run_log.RUNS, run_log.RUN_COLUMNS), (run_log.STEPS, run_log.STEP_COLUMNS)):
        found = [(r["column_name"], r["full_data_type"].upper()) for r in rows if r["table_name"] == table]
        if found != list(columns):
            problems.append(f"{catalog}.{run_log.SCHEMA}.{table} has {found}, the definition is {list(columns)}")
    return problems


def provision(run, catalog: str, app_principal: str, readers_group: str) -> None:
    """APP-RUN-LOG — the DDL and its grants, together. Raises on any failure, which fails the deploy."""
    from cql_databricks_designer import run_log
    if not app_principal or not readers_group:
        raise ValueError("the Designer App's service principal and the readers group are both required")
    for ddl in run_log.create_statements(catalog):
        run(ddl)
    problems = column_problems(run, catalog)
    if problems:
        raise RuntimeError("the run log tables do not match their definition; add an ALTER TABLE to this "
                           "script:\n  " + "\n  ".join(problems))
    schema = f"`{catalog}`.`{run_log.SCHEMA}`"
    tables = [run_log.table_sql(catalog, run_log.RUNS), run_log.table_sql(catalog, run_log.STEPS)]
    for principal, privilege in ((app_principal, "MODIFY"), (readers_group, "SELECT")):
        run(f"GRANT USE CATALOG ON CATALOG `{catalog}` TO `{principal}`")
        run(f"GRANT USE SCHEMA ON SCHEMA {schema} TO `{principal}`")
        for table in tables:
            run(f"GRANT {privilege} ON TABLE {table} TO `{principal}`")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True, help="designer_catalog")
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--designer-app", required=True, help="the Designer App's name")
    parser.add_argument("--readers-group", required=True, help="run_log_readers_group")
    args = parser.parse_args(argv)

    from cql_databricks.data_bindings import warehouse_session
    from cql_databricks_common.workspace import service_principal_client

    # Locally the shared client IS the deployer (`SEC-LOCAL-IDENTITY`): the profile's OAuth.
    w = service_principal_client()
    app_principal = w.apps.get(args.designer_app).service_principal_client_id or ""
    # RUN-CLIENT-WAREHOUSE-CONNECTOR — one warehouse session for the provisioning, closed at its end.
    with warehouse_session(w, args.warehouse) as run:
        provision(run, args.catalog, app_principal, args.readers_group)
    print(f"==> APP-RUN-LOG: {args.catalog}.designer.runs and run_steps current; MODIFY to {app_principal}, "
          f"SELECT to {args.readers_group}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
