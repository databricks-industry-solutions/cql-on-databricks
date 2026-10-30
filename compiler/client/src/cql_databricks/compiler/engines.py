"""EVALUATOR-CLIENT-EVALUATE, `DEC-ONE-VERB-ENGINE-OPTION` — which engine runs a ``cql()`` query.

One verb, ``cql()``, and an engine setting resolved like every other session setting (`BUILD-RESOLVE`):
``cql(engine=…)`` > ``connect(engine=…)`` / ``builder.engine(…)`` > ``spark.cqldatabricks.engine`` >
``CQL_DATABRICKS_ENGINE`` > ``local``. ``local`` is this package's own path: compiled here, run on the
caller's session, nothing kept. Any other name is an engine another feature installs under the entry-point
group :data:`ENTRY_POINT_GROUP` (`DEC-ENGINE-PLUGIN`): the evaluator's client installs ``evaluator``, which
runs the query on the evaluator's compute and returns DataFrames over its stored results.

The seam runs this way round because the compiler sits below the evaluator (`DEC-FEATURE-ORDER`): this
package names no evaluator code, and the evaluator's client implements :class:`Engine`.

**No fallback.** An engine that is not installed, or that fails, raises. A query is never handed from one
engine to the other, so a stored answer and a live one are never confused.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence, Tuple

#: The entry-point group an engine registers under: ``<name> = "<module>:<factory>"``.
ENTRY_POINT_GROUP = "cql_databricks.engines"


@dataclass(frozen=True, kw_only=True)
class EngineRequest:
    """One query for an engine other than ``local``: the CQL, the defines to return and the session settings.

    ``cql`` is the root library's CQL text (an engine compiles it itself); ``targets`` are define names, and
    empty means the engine's default: the populations the library has, else every define it computed. ``measurement_period`` is the raw
    ``(start, end)`` the caller gave; the engine puts it in the form the library declares. ``settings`` is
    the session's resolved configuration, for the engine's own keys (its App, its endpoint)."""

    cql: str
    targets: Sequence[str]
    data_source: Optional[str] = None
    measurement_period: Optional[Tuple[str, str]] = None
    recompute: bool = False
    settings: Mapping[str, str] = field(default_factory=dict)
    spark: Any = None


class Engine(Protocol):
    """Runs an :class:`EngineRequest` and returns ``{define: DataFrame}`` for each of its targets, with the
    same columns ``local`` returns for that define (`DEC-EVALUATOR-OPAQUE`)."""

    def exec(self, request: EngineRequest) -> dict:
        ...


class EngineNotInstalled(LookupError):
    """The session names an engine no installed distribution provides."""


def engine_for(name: str) -> Engine:
    """The installed engine called ``name``. Raises :class:`EngineNotInstalled`, naming what is installed."""
    from importlib.metadata import entry_points

    found = {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}
    if name not in found:
        installed = ", ".join(sorted(found)) or "none"
        raise EngineNotInstalled(
            f"no CQL engine named {name!r} is installed (installed: local, {installed}).")
    return found[name].load()()


def parse_period(value: Optional[str]) -> Optional[Tuple[str, str]]:
    """A session measurement period, ``"<start>/<end>"`` (the ISO 8601 interval form), as ``(start, end)``.

    >>> parse_period("2026-01-01/2026-12-31")
    ('2026-01-01', '2026-12-31')
    >>> parse_period(None) is None
    True
    """
    if value is None or value == "":
        return None
    start, sep, end = value.partition("/")
    if not (sep and start.strip() and end.strip()):
        raise ValueError(f"a measurement period is '<start>/<end>', e.g. '2026-01-01/2026-12-31'; got {value!r}")
    return start.strip(), end.strip()


def parse_flag(value: Optional[str]) -> bool:
    """A boolean session setting: ``true``/``false`` (any case), or unset. Anything else is refused."""
    if value is None or value == "":
        return False
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise ValueError(f"expected true or false, got {value!r}")
