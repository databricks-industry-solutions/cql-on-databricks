"""DOC-EDITABLE-IN-SCOPE — whether the signed-in user can write a tagged knowledge store, asked before a library opens.

A save into a store is a ``MERGE`` into its ``library`` and ``measure`` tables, as the user (`DOC-OPEN-SAVE`). Unity
Catalog lets it when the user holds, at each level, the privilege the write needs: ``USE CATALOG`` on the catalog,
``USE SCHEMA`` on the schema, ``SELECT`` and ``MODIFY`` on each table. A level's privileges come from a grant to the user
or to one of their account groups, held directly or inherited from a parent, by ``ALL PRIVILEGES`` or by owning the
object. ``MANAGE`` grants none of them: it is the right to manage access, not to write data.

The grants are Databricks' record, read as the user from the catalog's ``information_schema`` through the SQL
warehouse, the one scope the App already holds (`DOC-EDIT-PERMISSION-CHECK`). Nothing here is a list of its own
(`SEC-GOVERNANCE`). The combination is the rule above and nothing else, and it decides only whether the Designer
offers editing: the save is still the user's own write, so a store this misjudges is refused by Databricks.

Workspace-local groups hold no Unity Catalog grants, so ``is_account_group_member`` misses none.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

#: The tables a save into a store writes.
STORE_TABLES = ("library", "measure")

#: What a write needs at each level, in Unity Catalog's own words.
_NEEDS = {"catalog": ("USE_CATALOG",), "schema": ("USE_SCHEMA",), "table": ("SELECT", "MODIFY")}
#: What stands for every privilege on an object.
_EVERY = {"ALL_PRIVILEGES", "OWNER"}


def _ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def access_sql(catalog: str) -> str:
    """The user's own privileges and ownerships on the store's catalog, schema and tables, one row each:
    ``level`` (catalog, schema or table), ``name`` and ``privilege`` (``OWNER`` for an ownership)."""
    info = f"{_ident(catalog)}.information_schema"
    mine = "(grantee = current_user() OR is_account_group_member(grantee))"
    owns = "({0} = current_user() OR is_account_group_member({0}))"
    return f"""
SELECT 'catalog' AS level, catalog_name AS name, privilege_type AS privilege FROM {info}.catalog_privileges
 WHERE catalog_name = :catalog AND {mine}
UNION ALL SELECT 'catalog', catalog_name, 'OWNER' FROM {info}.catalogs
 WHERE catalog_name = :catalog AND {owns.format('catalog_owner')}
UNION ALL SELECT 'schema', schema_name, privilege_type FROM {info}.schema_privileges
 WHERE schema_name = :schema AND {mine}
UNION ALL SELECT 'schema', schema_name, 'OWNER' FROM {info}.schemata
 WHERE schema_name = :schema AND {owns.format('schema_owner')}
UNION ALL SELECT 'table', table_name, privilege_type FROM {info}.table_privileges
 WHERE table_schema = :schema AND array_contains(from_json(:tables, 'ARRAY<STRING>'), table_name) AND {mine}
UNION ALL SELECT 'table', table_name, 'OWNER' FROM {info}.tables
 WHERE table_schema = :schema AND array_contains(from_json(:tables, 'ARRAY<STRING>'), table_name)
   AND {owns.format('table_owner')}
"""


def write_refusal(rows: Iterable[dict[str, Any]], catalog: str, schema: str,
                  tables: Iterable[str] = STORE_TABLES) -> str | None:
    """Why the user cannot write the store, from ``access_sql``'s rows, or ``None`` when they can."""
    held: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        held.setdefault((str(row["level"]), str(row["name"])), set()).add(str(row["privilege"]))
    lacking = []
    for level, name, shown in [("catalog", catalog, catalog), ("schema", schema, f"{catalog}.{schema}")] + [
            ("table", t, f"{catalog}.{schema}.{t}") for t in tables]:
        got = held.get((level, name), set())
        if got & _EVERY:
            continue
        lacking += [f"{need.replace('_', ' ')} on {shown}" for need in _NEEDS[level] if need not in got]
    if not lacking:
        return None
    return f"You cannot write {catalog}.{schema}: you do not hold {', '.join(lacking)}."


def store_refusal(run: Callable[..., list[dict[str, Any]]], catalog: str, schema: str) -> str | None:
    """DOC-EDITABLE-IN-SCOPE — why the user ``run`` acts as cannot write the store, or ``None`` when they can."""
    rows = run(access_sql(catalog), {"catalog": catalog, "schema": schema, "tables": json.dumps(list(STORE_TABLES))})
    return write_refusal(rows, catalog, schema)
