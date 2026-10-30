"""CONTENT-RESOLVER — the layouts data-bindings owns, and the check that a tagged schema meets one.

``KNOWLEDGE`` is pure FHIR (`CONTENT-LAKEHOUSE-STANDARD-ONLY`): a ``library`` and a ``measure`` table, each
the resource plus its canonical identity. ``TERMINOLOGY`` is the tall value-set table the compiled SQL
joins at run time (`BIND-TERMINOLOGY-TABLE`, whose layout data-bindings now owns): one row per code, keeping
the expansion version so a measure's pinned value-set version still resolves. Value sets arrive
already expanded; nothing here computes an expansion.

Only required columns are listed. A table may carry more (provenance, ``display``) and still conform.
"""
from __future__ import annotations

from typing import Mapping

from .runner import ResolverError, SqlRunner
from .tags import TaggedSchema

Layout = Mapping[str, frozenset[str]]

KNOWLEDGE: Layout = {
    "library": frozenset({"url", "version", "resource"}),
    "measure": frozenset({"url", "version", "resource"}),
}

TERMINOLOGY: Layout = {
    "valueset": frozenset({"valueset_id", "system", "code", "version"}),
}

_COLUMNS = """
SELECT table_name, column_name
  FROM system.information_schema.columns
 WHERE table_catalog = :catalog AND table_schema = :schema
"""


def layout_problems(run: SqlRunner, schema: TaggedSchema, layout: Layout) -> list[str]:
    """What ``schema`` lacks against ``layout``: one line per missing table or column, sorted."""
    present: dict[str, set[str]] = {}
    for row in run(_COLUMNS, {"catalog": schema.catalog, "schema": schema.schema}):
        present.setdefault(row["table_name"], set()).add(row["column_name"])
    problems: list[str] = []
    for table, columns in sorted(layout.items()):
        if table not in present:
            problems.append(f"missing table {table}")
            continue
        for column in sorted(columns - present[table]):
            problems.append(f"{table} is missing column {column}")
    return problems


def check_layout(run: SqlRunner, schema: TaggedSchema, layout: Layout) -> None:
    """Raise when ``schema`` does not meet ``layout``, naming every gap at once."""
    problems = layout_problems(run, schema, layout)
    if problems:
        raise ResolverError(f"{schema.catalog}.{schema.schema} (tagged {schema.tag} = {schema.name!r}) "
                            f"does not match its layout: {'; '.join(problems)}")
