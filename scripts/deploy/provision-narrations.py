"""APP-NARRATIVE-OVERLAY — create the Designer's narration cache table and grant it, as the deployer.

Run by ``scripts/deploy/deploy.sh`` after the bundle deploy (which creates the ``designer`` schema) and after the Apps
exist (the grant names the Designer App's service principal). Idempotent. The table's one definition is
``cql_databricks_designer.explain.narrations``; the App only selects and inserts and never runs this DDL.

1. ``CREATE TABLE IF NOT EXISTS``, then compare its columns with that definition. A table that differs fails
   the deploy: ``IF NOT EXISTS`` does not change an existing table, so a column added to the definition
   needs an ``ALTER TABLE`` here, not a silent mismatch.
2. The Designer App's service principal: ``USE CATALOG``, ``USE SCHEMA``, ``SELECT`` and ``MODIFY`` on the
   table. No group is granted: nothing outside the App reads it.

    python scripts/deploy/provision-narrations.py --catalog <designer_catalog> --warehouse <id> \\
        --designer-app <Designer App name>
"""
from __future__ import annotations

import argparse
import sys


def column_problems(run, catalog: str) -> list[str]:
    """How the deployed table differs from ``narrations``'s column definition; empty when it matches."""
    from cql_databricks_designer.explain import narrations
    rows = run(f"SELECT column_name, full_data_type FROM `{catalog}`.information_schema.columns "
               "WHERE table_schema = :schema AND table_name = :table ORDER BY ordinal_position",
               {"schema": narrations.SCHEMA, "table": narrations.TABLE})
    found = [(r["column_name"], r["full_data_type"].upper()) for r in rows]
    if found != list(narrations.COLUMNS):
        return [f"{catalog}.{narrations.SCHEMA}.{narrations.TABLE} has {found}, "
                f"the definition is {list(narrations.COLUMNS)}"]
    return []


def provision(run, catalog: str, app_principal: str) -> None:
    """APP-NARRATIVE-OVERLAY — the DDL and its grants, together. Raises on any failure, which fails the deploy."""
    from cql_databricks_designer.explain import narrations
    if not app_principal:
        raise ValueError("the Designer App's service principal is required")
    run(narrations.create_statement(catalog))
    problems = column_problems(run, catalog)
    if problems:
        raise RuntimeError("the narration table does not match its definition; add an ALTER TABLE to this "
                           "script:\n  " + "\n  ".join(problems))
    run(f"GRANT USE CATALOG ON CATALOG `{catalog}` TO `{app_principal}`")
    run(f"GRANT USE SCHEMA ON SCHEMA `{catalog}`.`{narrations.SCHEMA}` TO `{app_principal}`")
    run(f"GRANT SELECT, MODIFY ON TABLE {narrations.table_sql(catalog)} TO `{app_principal}`")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True, help="designer_catalog")
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--designer-app", required=True, help="the Designer App's name")
    args = parser.parse_args(argv)

    from cql_databricks.data_bindings import warehouse_runner
    from cql_databricks_common.workspace import service_principal_client

    # Locally the shared client IS the deployer (`SEC-LOCAL-IDENTITY`): the profile's OAuth.
    w = service_principal_client()
    app_principal = w.apps.get(args.designer_app).service_principal_client_id or ""
    provision(warehouse_runner(w, args.warehouse), args.catalog, app_principal)
    print(f"==> APP-NARRATIVE-OVERLAY: {args.catalog}.designer.narrations current; SELECT and MODIFY to {app_principal}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
