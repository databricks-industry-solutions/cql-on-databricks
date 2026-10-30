"""CONTENT-RESOLVER — find schemas by UC tag (`DEC-DATA-SOURCE-UC-TAG`, `DEC-TERMINOLOGY-UC-TAG`).

A schema declares what it holds with one tag; the tag's value is the name callers use. Three keys:

| Key                          | Layout owner           |
|------------------------------|------------------------|
| ``cql_databricks_knowledge``   | content (``layout.KNOWLEDGE``)   |
| ``cql_databricks_terminology`` | content (``layout.TERMINOLOGY``) |
| ``cql_databricks_data_source`` | compiler (the mount contract)    |

The generic lookup lives here for all three; the compiler checks the data-source layout itself. Lookups
read ``system.information_schema``, which shows each caller only the schemas it may see, so a lookup
respects grants with no code of ours. They take seconds: resolve per compile or deploy, never per request.

CLIENT-DISCOVERY-SCOPE — a lookup reads only the caller's catalog scopes: an ordered list of ``catalog``,
``catalog.schema`` or ``*`` (every catalog the caller can see) entries. One query finds all three keys; an earlier
scope wins a name.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from .runner import ResolverError, SqlRunner

KNOWLEDGE_TAG = "cql_databricks_knowledge"
TERMINOLOGY_TAG = "cql_databricks_terminology"
DATA_SOURCE_TAG = "cql_databricks_data_source"


TAGS = (KNOWLEDGE_TAG, TERMINOLOGY_TAG, DATA_SOURCE_TAG)

_NO_SCOPES = "discovery needs at least one catalog scope (CLIENT-DISCOVERY-SCOPE); none is configured"

#: CLIENT-DISCOVERY-SCOPE — the entry for every catalog the caller can see; grants decide what is found.
EVERYWHERE = "*"


@dataclass(frozen=True)
class CatalogScope:
    """CLIENT-DISCOVERY-SCOPE — one entry of a caller's ordered scopes: a whole catalog, one schema in it, or
    ``*``, every catalog."""
    catalog: str
    schema: str | None = None

    def contains(self, catalog: str, schema: str) -> bool:
        return self.catalog in (EVERYWHERE, catalog) and self.schema in (None, schema)

    def __str__(self) -> str:
        return self.catalog if self.schema is None else f"{self.catalog}.{self.schema}"


def parse_scopes(entries: Sequence[str]) -> tuple[CatalogScope, ...]:
    """CLIENT-DISCOVERY-SCOPE — ``["cat", "cat.schema", "*", ...]`` in priority order. Fails on none, a bad entry or a
    repeat.

    Unity Catalog stores names in lower case, so an entry is compared in lower case too.
    """
    if isinstance(entries, str):
        raise ResolverError("catalog scopes are a list of entries, not one string: pass [\"cat\", \"cat.schema\"]")
    scopes = []
    for entry in entries:
        parts = entry.strip().lower().split(".") if isinstance(entry, str) else []
        if not 1 <= len(parts) <= 2 or not all(parts) or (EVERYWHERE in parts and parts != [EVERYWHERE]):
            raise ResolverError(f"catalog scope {entry!r} must be `catalog`, `catalog.schema` or `*`")
        scope = CatalogScope(*parts)
        if scope in scopes:
            raise ResolverError(f"catalog scope {str(scope)!r} is listed twice")
        scopes.append(scope)
    if not scopes:
        raise ResolverError(_NO_SCOPES)
    return tuple(scopes)


@dataclass(frozen=True)
class TaggedSchema:
    """One schema carrying a ``cql_databricks_*`` tag. ``name`` is the tag's value.

    ``position`` is the index of the first catalog scope that contains it: lower wins (CLIENT-DISCOVERY-SCOPE).
    """
    tag: str
    name: str
    catalog: str
    schema: str
    position: int = 0

    @property
    def fqn(self) -> str:
        return f"`{self.catalog}`.`{self.schema}`"

    def table(self, name: str) -> str:
        return f"{self.fqn}.`{name}`"

    def to_dict(self) -> dict[str, str]:
        return {"tag": self.tag, "name": self.name, "catalog": self.catalog, "schema": self.schema}


_FIND = """
SELECT catalog_name, schema_name, tag_name, tag_value
  FROM system.information_schema.schema_tags
 WHERE array_contains(from_json(:tags, 'ARRAY<STRING>'), tag_name)
   AND (:everywhere = 'true' OR array_contains(from_json(:catalogs, 'ARRAY<STRING>'), catalog_name))
 ORDER BY tag_name, tag_value, catalog_name, schema_name
"""


def find_tagged(run: SqlRunner, scopes: Sequence[CatalogScope]) -> dict[str, list[TaggedSchema]]:
    """CLIENT-DISCOVERY-SCOPE — every schema tagged with each key, inside ``scopes``, in one query.

    Each key's list holds one schema per name, earliest scope first. A name tagged in two scopes resolves
    to the earlier one. A name tagged twice in one scope fails, for the whole key: every lookup by that
    name would be a guess, so it is refused here rather than at the first lookup that happens to hit it.
    """
    if not scopes:
        raise ResolverError(_NO_SCOPES)
    catalogs = sorted({scope.catalog for scope in scopes} - {EVERYWHERE})
    everywhere = any(scope.catalog == EVERYWHERE for scope in scopes)
    by_name: dict[tuple[str, str], list[TaggedSchema]] = {}
    for row in run(_FIND, {"tags": json.dumps(list(TAGS)), "catalogs": json.dumps(catalogs),
                           "everywhere": "true" if everywhere else "false"}):
        position = next((i for i, scope in enumerate(scopes)
                         if scope.contains(row["catalog_name"], row["schema_name"])), None)
        if position is None:
            continue
        schema = TaggedSchema(tag=row["tag_name"], name=row["tag_value"], catalog=row["catalog_name"],
                              schema=row["schema_name"], position=position)
        by_name.setdefault((schema.tag, schema.name), []).append(schema)
    found: dict[str, list[TaggedSchema]] = {tag: [] for tag in TAGS}
    duplicates = []
    for (tag, name), schemas in sorted(by_name.items()):
        first = min(s.position for s in schemas)
        earliest = sorted((s for s in schemas if s.position == first), key=lambda s: (s.catalog, s.schema))
        if len(earliest) > 1:
            duplicates.append(f"{tag} = {name!r} on {', '.join(f'{s.catalog}.{s.schema}' for s in earliest)} "
                              f"(scope {scopes[first]})")
        else:
            found[tag].append(earliest[0])
    if duplicates:
        raise ResolverError(f"a tag value names more than one schema in one scope: {'; '.join(duplicates)}. "
                            "Give each its own value.")
    for schemas in found.values():
        schemas.sort(key=lambda s: (s.position, s.name))
    return found


def tag_statement(tag: str, name: str, catalog: str, schema: str) -> str:
    """The DDL that declares a schema as ``tag = name``. Loaders run it after they write."""
    if "'" in name:
        raise ResolverError(f"tag value {name!r} may not contain a quote")
    return f"ALTER SCHEMA `{catalog}`.`{schema}` SET TAGS ('{tag}' = '{name}')"


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def declaration_statements(*, name: str, catalog: str, schema: str, description: str) -> list[str]:
    """DATA-SOURCE-DESCRIPTION — the DDL that declares ``catalog.schema`` as the data source ``name``: its tag, then
    its comment. Whoever lands the data runs it, after the retrieve UDTFs exist; a customer runs the same two
    statements on their own schema."""
    return [tag_statement(DATA_SOURCE_TAG, name, catalog, schema),
            f"COMMENT ON SCHEMA `{catalog}`.`{schema}` IS {_sql_string(description)}"]
