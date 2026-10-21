"""CLIENT-EXEC-DISCOVERY — the Databricks data binder: a run reads the data source found by its tag.

``exec()`` and ``materialize()`` find data the way a durable calculation does
(:func:`~cql_databricks.result.calculation.prepare_calculation`): the schema tagged
``cql_databricks_data_source`` (`DATA-SOURCE-UC-TAG`), in the caller's catalog scopes
(`CLIENT-DISCOVERY-SCOPE`), sent to the compiler as its routines (`SVC-COMPILE-BATCH`). Value sets come from
the runtime ``valuesets`` view beside the ``cql_*`` functions, which the deploy builds over every schema
tagged ``cql_databricks_terminology`` (`DATA-BINDINGS`).

Which source:

- the one named, by ``data_source=`` or ``spark.cqldatabricks.dataSource``;
- else the session mount (`CLIENT-SESSION-MOUNT`), which replaces discovery: its types and value sets are bound
  explicitly. A durable calculation refuses it, because its results are kept under a data source's name;
- else the only one in scope;
- else none: several in scope fail, naming them. Catalog scopes order one name found twice; they never
  choose between two names, because a different source is a different population.

CQL that retrieves nothing resolves nothing, so data-free CQL runs with no source in scope. A source that
lacks a type the CQL retrieves fails before the run, naming the type.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from .binding import BoundData
from .data_source import RETRIEVE_PREFIX
from .resolve import ResolutionError, resolve_data_source_routines


class DatabricksDataBinder:
    """Binds each compile to a named data source, the session mount, or the one tagged source in scope
    (`CLIENT-EXEC-DISCOVERY`).

    ``content`` and ``run`` are factories, so building a client touches no workspace; the first compile
    that retrieves does. The tag lookup is the resolver's, cached until it reloads (`CONTENT-RESOLVER`).
    ``terminology_table`` is the value-set table a compile carries when nothing is mounted. ``record`` is the
    session record the mount is kept in (`CLIENT-SESSION-STATE`). ``catalog_scopes`` is the list
    ``connect(catalog_scopes=...)`` was given, or None; ``materialize`` reads results in the same scopes
    (`CLIENT-DISCOVERY-SCOPE`)."""

    def __init__(self, content: Callable[[], object], run: Callable[[], object], *,
                 terminology_table: Optional[str], record, catalog_scopes: Optional[list[str]] = None) -> None:
        self.catalog_scopes = catalog_scopes
        self._content_factory = content
        self._run_factory = run
        self._content = None
        self._run = None
        self._terminology_table = terminology_table
        self._record = record

    def generation(self):
        """Moves whenever the mount does, so a compile cached against an older mount is not reused."""
        return self._record.version()

    def bind(self, library, retrieve_types: Sequence[str], *, durable: bool) -> BoundData:
        named = getattr(library, "data_source", None)
        mounted = None if named else self._record.mount()
        if mounted is not None:
            return _mounted(mounted, retrieve_types, durable=durable)
        if not retrieve_types:
            return BoundData(terminology_table=self._terminology_table)
        content, run = self._resolver()
        name = named or _only_source(content, retrieve_types)
        layout = resolve_data_source_routines(content, run, name)
        bound = {r.lower() for r in layout["routines"]}
        missing = [t for t in retrieve_types if (RETRIEVE_PREFIX + t).lower() not in bound]
        if missing:
            raise ResolutionError(
                f"data source {name!r} ({layout['catalog']}.{layout['schema']}) has no "
                f"{', '.join(RETRIEVE_PREFIX + t for t in missing)}, and this CQL retrieves "
                f"{', '.join(missing)}")
        return BoundData(data_source=layout, terminology_table=self._terminology_table)

    def _resolver(self):
        if self._content is None:
            self._content = self._content_factory()
            self._run = self._run_factory()
        return self._content, self._run


def _mounted(manifest: dict, retrieve_types: Sequence[str], *, durable: bool) -> BoundData:
    """CLIENT-SESSION-MOUNT — bind every retrieved type to its mounted function, explicitly."""
    if durable and retrieve_types:
        raise ResolutionError("a durable calculation keeps its results under a data source's name, so it never "
                              "reads the session mount; name a tagged data source with data_source=, or "
                              "reset(spark) to drop the mount")
    functions = manifest["types"]
    missing = [t for t in retrieve_types if t not in functions]
    if missing:
        raise ResolutionError(f"this CQL retrieves {', '.join(missing)}, and the session mount has only "
                              f"{', '.join(sorted(functions))}")
    return BoundData(sources={t: {"function": functions[t]} for t in retrieve_types},
                     terminology_table=manifest.get("valuesets"))


def _only_source(content, retrieve_types: Sequence[str]) -> str:
    """The one data source in scope, else a loud failure that says how to name one."""
    names = sorted(s.name for s in content.data_source_schemas())
    scopes = ", ".join(str(s) for s in content.scopes)
    if len(names) == 1:
        return names[0]
    if not names:
        raise ResolutionError(f"this CQL retrieves {', '.join(retrieve_types)}, and no data source is tagged "
                              f"cql_databricks_data_source in catalog scopes {scopes}")
    raise ResolutionError(f"{len(names)} data sources in scope: {', '.join(names)}; name one with "
                          f"data_source= or spark.cqldatabricks.dataSource")
