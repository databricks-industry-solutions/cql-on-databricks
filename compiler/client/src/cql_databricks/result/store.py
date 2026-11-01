"""CLIENT-RESULT-STORE: kept results, found by fingerprint and written, in Delta.

:class:`DeltaResultStore` is the one shipped store: a destination ``(catalog, schema)`` the caller names, where
every result is written, and the ``catalog.schema`` result scopes it is also read from, destination first
(`CLIENT-DISCOVERY-SCOPE`). A result is a Delta table named ``<library>_<define>_<16 fingerprint digits>``
(`RUN-CLIENT-TABLE-NAMES`) carrying the four completion tags (`RUN-CLIENT-RESULT-IDENTITY`). ``materialize``
runs on the same store, finding every result of a run in one lookup and writing the misses in parallel
(`RUN-CLIENT-PARALLEL-DEFINES`). A result is never written to a folder.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone

from cql_databricks.compiler.roles import ResultStore, TableRef
from cql_databricks.data_bindings.runner import SqlRunner

from ._catalog_completion import (NAME_DIGITS, TAGS, Completion, _literal, _set_tags, result_table_name)
from ._durable import _destination, _digest
from ._durable_delta import table_sql
from ._catalog_runtime import TABLE_FORMAT

log = logging.getLogger(__name__)


class DeltaResultStore(ResultStore):
    """CLIENT-RESULT-STORE: results written to ``destination`` and read from it, then from ``sources`` in order,
    through ``run``, as its identity. The scopes are the trust boundary: a result found in one is reused with no
    owner check (`DEC-RESULT-SCOPE`)."""

    def __init__(self, destination: tuple[str, str], run: SqlRunner,
                 sources: Sequence[tuple[str, str]] = ()) -> None:
        _destination(destination)
        self.destination = tuple(destination)
        self.sources = tuple(s for s in dict.fromkeys(tuple(s) for s in sources) if s != self.destination)
        self._run = run

    def find(self, fingerprint: str) -> TableRef | None:
        """The table holding the completed result with ``fingerprint``, destination first, else None."""
        _digest(fingerprint, "fingerprint")
        scopes = [self.destination, *self.sources]
        params = {"suffix": "%\\_" + fingerprint[:NAME_DIGITS]}
        branches = []
        for i, catalog in enumerate(dict.fromkeys(catalog for catalog, _ in scopes)):
            params[f"schemas_{i}"] = json.dumps(sorted(schema for c, schema in scopes if c == catalog))
            branches.append(
                "SELECT catalog_name, schema_name, table_name, tag_name, tag_value "
                f"FROM {table_sql((catalog, 'information_schema', 'table_tags'))} "
                f"WHERE schema_name IN (SELECT explode(from_json(:schemas_{i}, 'ARRAY<STRING>'))) "
                "AND table_name LIKE :suffix "
                f"AND tag_name IN ({', '.join(_literal(tag) for tag in TAGS)})")
        found: dict[tuple[str, str, str], dict[str, str]] = {}
        for row in self._run(" UNION ALL ".join(branches), params):
            table = (row["catalog_name"], row["schema_name"], row["table_name"])
            if table[:2] in scopes:
                found.setdefault(table, {})[row["tag_name"]] = row["tag_value"]
        complete = [c for table, tags in found.items()
                    if (c := Completion.from_tags(table, tags)) is not None and c.fingerprint == fingerprint]
        if not complete:
            return None
        best = min(complete, key=lambda c: scopes.index(c.table[:2]))
        return TableRef(catalog=best.table[0], schema=best.table[1], table=best.table[2])

    def write(self, recipe, relation: str) -> TableRef:
        """Write ``recipe``'s result, the rows of the SQL query ``relation``, to the destination, then mark it
        complete. ``recipe`` is a durable :class:`~cql_databricks.compiler.providers.DatasetRecipe`."""
        if recipe.durable is None:
            raise ValueError(f"recipe {recipe.name} is not durable: compile it with durable=True")
        if not recipe.durable.reusable:
            raise ValueError(f"recipe {recipe.name} is not reusable, so it is never kept (CLIENT-PROVIDER-IDENTITY)")
        fingerprint = recipe.durable.fingerprint
        table = (*self.destination, result_table_name(recipe.library, recipe.define, fingerprint))
        started = datetime.now(timezone.utc)
        self._run(f"CREATE OR REPLACE TABLE {table_sql(table)} USING DELTA "
                  f"TBLPROPERTIES ('cql.durable.format' = '{TABLE_FORMAT}') AS {relation}")
        _set_tags(self._run, table, Completion(table=table, fingerprint=fingerprint, computed_at=started).tags())
        log.info("wrote %s", ".".join(table))
        return TableRef(catalog=table[0], schema=table[1], table=table[2])
