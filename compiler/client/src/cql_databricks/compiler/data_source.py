"""BIND-DATA-SOURCE-LAYOUT, DATA-SOURCE-UC-TAG — the data-source layout the compiler owns, and its check.

A data source is a Unity Catalog schema tagged ``cql_databricks_data_source = <name>``
(`DEC-DATA-SOURCE-UC-TAG`). data-bindings finds the schema by its tag; this module checks what is
in it. The layout is the mount contract customers implement:

| Object | Required | Contract |
|---|---|---|
| ``retrieve_<Type>`` | at least one | a table, view or SQL function returning one FHIR type on the contract (`BIND-RELATION`) |
| ``change_data_log`` | no | ``(data_type, updated_at)``: when each type's data last changed (`DATA-CHANGE-LOG`) |
| ``change_log`` | no | ``(parameters, changed_at)``: which evaluation sets changed, by parameter value (`DATA-CHANGE-LOG-PERIODS`) |

The bindings are what the schema holds, not a list someone declared: a type is bound when its
``retrieve_<Type>`` table, view or function exists. Unity Catalog stores names in lower case, so each function is mapped
back to its FHIR type through the compiler's modeled types; a ``retrieve_`` function that names no
modeled type is a layout error, not something skipped.

Every read goes through a SQL runner (``run(statement, params) -> rows as dicts``), the same seam
data-bindings uses, so a notebook and an App run one implementation and this module needs no SDK.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

if TYPE_CHECKING:
    from cql_databricks.data_bindings import ContentResolver

SqlRunner = Callable[..., list[dict[str, Any]]]

RETRIEVE_PREFIX = "retrieve_"
CHANGE_DATA_LOG = "change_data_log"
CHANGE_DATA_LOG_COLUMNS = frozenset({"data_type", "updated_at"})
CHANGE_LOG = "change_log"
CHANGE_LOG_COLUMNS = frozenset({"parameters", "changed_at"})

_ROUTINES = """
SELECT routine_name
  FROM system.information_schema.routines
 WHERE routine_catalog = :catalog AND routine_schema = :schema
"""

_RETRIEVE_OBJECTS = """
SELECT routine_name
  FROM system.information_schema.routines
 WHERE routine_catalog = :catalog AND routine_schema = :schema
UNION ALL
SELECT table_name AS routine_name
  FROM system.information_schema.tables
 WHERE table_catalog = :catalog AND table_schema = :schema
"""

_CHANGE_LOG_COLUMNS = """
SELECT column_name
  FROM system.information_schema.columns
 WHERE table_catalog = :catalog AND table_schema = :schema AND table_name = :table
"""


class DataSourceLayoutError(RuntimeError):
    """A data source that does not meet the layout. Raised, never swallowed (fail loud)."""


@dataclass(frozen=True)
class DataSourceLayout:
    """What a data-source schema binds: ``bindings`` maps a FHIR type to its ``retrieve_<Type>`` object's FQN.
    ``change_log`` is the relation naming the evaluation sets that changed, if the schema has one
    (`DATA-CHANGE-LOG-PERIODS`)."""
    catalog: str
    schema: str
    bindings: Mapping[str, str] = field(default_factory=dict)
    change_data_log: str | None = None
    change_log: str | None = None


def _fqn(catalog: str, schema: str, name: str) -> str:
    return f"{catalog}.{schema}.{name}"


def change_log_ddl(catalog: str, schema: str) -> str:
    """`DATA-CHANGE-LOG-PERIODS` — the ``change_log`` table, for a data source whose owner appends a row per change.
    Idempotent. A customer may instead define a view of the same shape over their own tables."""
    return (f"CREATE TABLE IF NOT EXISTS {_fqn(catalog, schema, CHANGE_LOG)} "
            f"(parameters MAP<STRING, STRING>, changed_at TIMESTAMP) "
            f"COMMENT 'DATA-CHANGE-LOG-PERIODS: which evaluation sets changed, by parameter value; an empty map is "
            f"every open evaluation set'")


def change_log_all_statement(catalog: str, schema: str) -> str:
    """One ``change_log`` row saying every open evaluation set changed now: what a whole-population reload logs."""
    return f"INSERT INTO {_fqn(catalog, schema, CHANGE_LOG)} VALUES (map(), current_timestamp())"


def read_layout(run: SqlRunner, catalog: str, schema: str,
                modeled_types: Iterable[str], *, include_change_logs: bool = True) -> tuple[DataSourceLayout, list[str]]:
    """BIND-DATA-SOURCE-LAYOUT — validate bindings; RUN-CLIENT-REUSE callers omit legacy log checks."""
    by_lower = {t.lower(): t for t in modeled_types}
    params = {"catalog": catalog, "schema": schema}
    problems: list[str] = []
    bindings: dict[str, str] = {}
    for name in sorted({row["routine_name"] for row in run(_RETRIEVE_OBJECTS, params)}):
        if not name.lower().startswith(RETRIEVE_PREFIX):
            continue
        fhir_type = by_lower.get(name[len(RETRIEVE_PREFIX):].lower())
        if fhir_type is None:
            problems.append(f"{name} names no FHIR type the compiler models")
            continue
        bindings[fhir_type] = _fqn(catalog, schema, RETRIEVE_PREFIX + fhir_type)
    if not bindings:
        problems.append(f"no {RETRIEVE_PREFIX}<Type> table, view or function")
    change_data_log = (_optional_relation(run, params, CHANGE_DATA_LOG, CHANGE_DATA_LOG_COLUMNS, problems)
                       if include_change_logs else None)
    change_log = (_optional_relation(run, params, CHANGE_LOG, CHANGE_LOG_COLUMNS, problems)
                  if include_change_logs else None)
    layout = DataSourceLayout(catalog=catalog, schema=schema,
                              bindings=dict(sorted(bindings.items())), change_data_log=change_data_log,
                              change_log=change_log)
    return layout, sorted(problems)


def read_routines(run: SqlRunner, catalog: str, schema: str) -> list[str]:
    """SVC-COMPILE-BATCH — discover as the caller; model validation can run in the compiler."""
    return sorted({row["routine_name"] for row in run(_ROUTINES, {"catalog": catalog, "schema": schema})})


def _optional_relation(run: SqlRunner, params: dict, table: str, required: frozenset,
                       problems: list[str]) -> str | None:
    """The FQN of an optional relation in the schema, or ``None`` when it is absent. One that exists without its
    required columns is a problem, not absent."""
    columns = {r["column_name"] for r in run(_CHANGE_LOG_COLUMNS, {**params, "table": table})}
    if not columns:
        return None
    missing = sorted(required - columns)
    if missing:
        problems.append(f"{table} is missing column {', '.join(missing)}")
        return None
    return _fqn(params["catalog"], params["schema"], table)


def check_layout(run: SqlRunner, catalog: str, schema: str,
                 modeled_types: Iterable[str], *, include_change_logs: bool = True) -> DataSourceLayout:
    """The layout ``catalog.schema`` holds. Raises naming every gap at once when it does not conform."""
    layout, problems = read_layout(run, catalog, schema, modeled_types, include_change_logs=include_change_logs)
    if problems:
        raise DataSourceLayoutError(
            f"{catalog}.{schema} does not match the data-source layout: {'; '.join(problems)}")
    return layout


# ---------------------------------------------------------------- data sources as a caller sees them
_COMMENT = """
SELECT comment
  FROM system.information_schema.schemata
 WHERE catalog_name = :catalog AND schema_name = :schema
"""


@dataclass(frozen=True)
class DataBinding:
    """One FHIR type's retrieve UDTF (DATA-BINDINGS): ``function`` is its fully qualified name."""

    function: str

    def to_dict(self) -> dict[str, Any]:
        return {"function": self.function}


@dataclass(frozen=True)
class DataSource:
    name: str
    catalog: str
    schema: str
    description: str | None = None
    data_bindings: dict[str, DataBinding] = field(default_factory=dict)
    change_data_log: str | None = None

    @property
    def id(self) -> str:
        """The tag value: what the run routes and the results address call this source."""
        return self.name

    @property
    def target(self) -> str:
        return f"{self.catalog}.{self.schema}"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "catalog": self.catalog,
            "schema": self.schema,
            "dataBindings": {t: b.to_dict() for t, b in self.data_bindings.items()},
        }
        if self.description:
            d["description"] = self.description
        if self.change_data_log:
            d["changeDataLog"] = self.change_data_log
        return d


def list_data_sources(content: "ContentResolver", run: SqlRunner,
                      modeled_types: Iterable[str]) -> list[DataSource]:
    """DATA-SOURCE-UC-TAG — every schema ``content`` finds by the data-source tag, checked and described.

    Read-only, as whoever owns ``run``: the Designer passes the signed-in user's warehouse runner (`SEC-OBO`), so a
    user sees only the sources Unity Catalog lets them see. Lookups take seconds, so callers cache the result. A
    source that fails its layout check fails the whole list rather than dropping out of it: a missing source is not
    an empty one. ``to_dict`` is the wire shape (`designer/src/wire.ts`).
    """
    types = list(modeled_types)
    sources = []
    for tagged in content.data_source_schemas():
        layout = check_layout(run, tagged.catalog, tagged.schema, types)
        rows = run(_COMMENT, {"catalog": tagged.catalog, "schema": tagged.schema})
        sources.append(DataSource(
            name=tagged.name, catalog=tagged.catalog, schema=tagged.schema,
            description=(rows[0].get("comment") or None) if rows else None,
            data_bindings={t: DataBinding(function=f) for t, f in layout.bindings.items()},
            change_data_log=layout.change_data_log))
    return sources
