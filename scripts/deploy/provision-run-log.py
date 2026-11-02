"""APP-RUN-LOG, DEC-RUN-LOG-LOCATION — create the Designer run log tables and grant them, as the deployer.

Run by ``scripts/deploy/deploy.sh`` after the bundle deploy (which creates the ``designer`` schema) and after the Apps
exist (the grant names the Designer App's service principal). Idempotent. The tables' one definition is
``cql_databricks_designer.run_log``; the App only inserts and never runs this DDL.

1. ``CREATE TABLE IF NOT EXISTS`` both tables, then migrate a table an earlier deploy made (:data:`RENAMED`), then
   compare their columns with that definition. A table that differs fails the deploy: ``IF NOT EXISTS`` does not
   change an existing table, so a column added to the definition needs a migration here, not a silent mismatch.
2. The Designer App's service principal: ``USE CATALOG``, ``USE SCHEMA`` and ``MODIFY`` on both tables.
3. The readers group: ``USE CATALOG``, ``USE SCHEMA`` and ``SELECT`` on both tables (not the whole schema, which
   may later hold other tables).

    python scripts/deploy/provision-run-log.py --catalog <designer_catalog> --warehouse <id> \\
        --designer-app <Designer App name> --readers-group <group>
"""
from __future__ import annotations

import argparse
import sys


#: APP-RUN-LOG — columns an earlier deploy named differently, ``(table, old, new)``. A rename needs Delta column mapping,
#: which upgrades the table's protocol (reader 2, writer 5) once; a reader older than that can no longer read it.
RENAMED: tuple[tuple[str, str, str], ...] = (("runs", "data_source", "environment"),)


def _columns(run, catalog: str) -> dict[str, list[str]]:
    from cql_databricks_designer import run_log
    rows = run(f"SELECT table_name, column_name FROM `{catalog}`.information_schema.columns "
               "WHERE table_schema = :schema AND table_name IN (:runs, :steps) ORDER BY table_name, ordinal_position",
               {"schema": run_log.SCHEMA, "runs": run_log.RUNS, "steps": run_log.STEPS})
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["table_name"], []).append(r["column_name"])
    return out


def migrate(run, catalog: str) -> list[str]:
    """APP-RUN-LOG — rename each :data:`RENAMED` column a table still has under its old name, and only then: a table
    with the new name, or with both, is left for :func:`column_problems` to judge. Idempotent. Returns what ran."""
    from cql_databricks_designer import run_log
    columns, ran = _columns(run, catalog), []
    for table, old, new in RENAMED:
        held = columns.get(table, [])
        if old not in held or new in held:
            continue
        name = run_log.table_sql(catalog, table)
        for sql in (f"ALTER TABLE {name} SET TBLPROPERTIES ('delta.columnMapping.mode' = 'name', "
                    "'delta.minReaderVersion' = '2', 'delta.minWriterVersion' = '5')",
                    f"ALTER TABLE {name} RENAME COLUMN `{old}` TO `{new}`"):
            run(sql)
            ran.append(sql)
    return ran


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
    migrate(run, catalog)
    problems = column_problems(run, catalog)
    if problems:
        raise RuntimeError("the run log tables do not match their definition; add a migration to this "
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
