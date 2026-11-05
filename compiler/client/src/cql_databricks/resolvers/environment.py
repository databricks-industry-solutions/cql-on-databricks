"""CLIENT-ENVIRONMENT, CLIENT-ENVIRONMENT-RESOLVER: environments, and the resolver that finds them.

An environment is a Unity Catalog catalog tagged ``cql_databricks_environment = <name>`` and
``cql_databricks_environment_version = <layout version>``; the default one is also tagged
``cql_databricks_environment_default = true``. Its description is the catalog comment. It holds every dependency of a
run in the schemas of ``DATA-ENVIRONMENT-LAYOUT``:

| Schema | Holds |
|---|---|
| ``data`` | one table or view per FHIR type, named for the type in lower case, on the contract |
| ``knowledge`` | the tables or views ``library``, ``measure`` and ``valueset(valueset_id, system, code, version)`` |
| ``results``, ``draft_results`` | kept results (`RUN-CLIENT-DRAFT-RESULTS`) |

:class:`EnvironmentResolver` finds environments with one query of ``system.information_schema.catalog_tags``, as the
caller, so only catalogs the caller can see are found. The once-per-process governance check runs beside that query,
not after it (`CLIENT-DISCOVERY-PARALLEL`). A run in an environment binds its relations by name, through rules
(`BIND-PUBLISHED-VIEW`): one compiler call, and no other catalog query. No layout is checked at run time: a missing
relation fails in the warehouse, naming it.

:class:`EnvironmentCache` keeps what each caller found for a process that serves many calls, an App, so a tool call or
request does not find them again (`CLIENT-ENVIRONMENT-CACHE`).

Part of the client core: SQL goes through the caller's SQL runner; nothing here imports Spark or a platform SDK.
"""
from __future__ import annotations

import contextvars
import re
import threading
import time
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .data import SchemaDataResolver
from .knowledge import TableKnowledgeResolver
from .interfaces import Overlay, Resolvers
from .terminology import TableTerminologyResolver

#: CLIENT-ENVIRONMENT: the catalog tags that make a catalog an environment.
NAME_TAG = "cql_databricks_environment"
VERSION_TAG = "cql_databricks_environment_version"
DEFAULT_TAG = "cql_databricks_environment_default"

#: CLIENT-ENVIRONMENT: the layout version this client writes, and every version it reads.
LAYOUT_VERSION = "3"
SUPPORTED_LAYOUTS = ("3",)

#: DATA-ENVIRONMENT-LAYOUT: the standard schema names.
DATA, KNOWLEDGE = "data", "knowledge"
RESULTS, DRAFT_RESULTS = "results", "draft_results"

_ENVIRONMENTS = f"""
SELECT t.catalog_name AS catalog, t.tag_name AS tag, t.tag_value AS value, c.comment AS comment
  FROM system.information_schema.catalog_tags t
  JOIN system.information_schema.catalogs c ON c.catalog_name = t.catalog_name
 WHERE t.tag_name IN ('{NAME_TAG}', '{VERSION_TAG}', '{DEFAULT_TAG}')
"""

_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: DEPLOY-ENVIRONMENT-TAG-POLICIES: the environment tags that are governed, of the three, in the caller's account.
_GOVERNED = f"""
SELECT tag_key
  FROM system.tags.governed_tags
 WHERE deleted_at IS NULL AND tag_key IN ('{NAME_TAG}', '{VERSION_TAG}', '{DEFAULT_TAG}')
"""
_governance_checked = False
_governance_lock = threading.Lock()


class UngovernedEnvironmentWarning(UserWarning):
    """CLIENT-ENVIRONMENT: an environment tag is not governed, so anyone who can tag a catalog can make it an
    environment or the default; or whether they are could not be read."""


def check_governance(run) -> None:
    """CLIENT-ENVIRONMENT, DEPLOY-ENVIRONMENT-TAG-POLICIES: warn, once per process, when an environment tag is not a
    governed tag in the caller's account, naming it, or when ``system.tags.governed_tags`` cannot be read as the
    caller. It never refuses an environment: a workspace whose account cannot govern them still runs."""
    if _claim_governance_check():
        _warn(_governance_note(run))


def _claim_governance_check() -> bool:
    """True for the first caller in the process, which then runs the check; False for every later one."""
    global _governance_checked
    with _governance_lock:
        if _governance_checked:
            return False
        _governance_checked = True
        return True


def _governance_note(run) -> str | None:
    """The governance warning's text, or None when all three tags are governed. Reads, never warns, so it may run on
    another thread and the warning still reaches the caller's (`CLIENT-DISCOVERY-PARALLEL`)."""
    try:
        governed = {row["tag_key"] for row in run(_GOVERNED)}
    except Exception as error:  # noqa: BLE001 — a read refused here is reported, never a failed run
        return (f"could not check that the environment tags are governed ({type(error).__name__}: "
                f"{str(error).splitlines()[0] if str(error) else ''}); if they are not, anyone who can tag a "
                f"catalog can make it an environment (DEPLOY-ENVIRONMENT-TAG-POLICIES)")
    missing = [tag for tag in (NAME_TAG, VERSION_TAG, DEFAULT_TAG) if tag not in governed]
    if missing:
        return (f"the environment tags {', '.join(missing)} are not governed tags: anyone who can tag a "
                f"catalog can make it an environment or the default one; make them governed "
                f"(DEPLOY-ENVIRONMENT-TAG-POLICIES)")
    return None


def _warn(note: str | None) -> None:
    if note:
        warnings.warn(note, UngovernedEnvironmentWarning, stacklevel=4)


class EnvironmentLookupError(LookupError):
    """No environment can be chosen: none is the default, two are, a name is not found, or a layout is not supported.
    It names what it found, never guesses."""


@dataclass(frozen=True, kw_only=True)
class Environment:
    """CLIENT-ENVIRONMENT: one environment, as its catalog's tags describe it."""
    name: str
    catalog: str
    version: str
    default: bool = False
    description: str | None = None

    def schema(self, name: str) -> str:
        """``catalog.<name>``, the catalog quoted when it needs to be, for a relation name the compiler embeds."""
        catalog = self.catalog if _PLAIN.match(self.catalog) else "`" + self.catalog.replace("`", "``") + "`"
        return f"{catalog}.{name}"

    def address(self, name: str) -> tuple[str, str]:
        """``(catalog, <name>)``, for a caller that quotes each part itself."""
        return self.catalog, name


def _read_environments(run) -> list[Environment]:
    """CLIENT-ENVIRONMENT: the environments ``run`` can see, by name. Two catalogs with one name fail, naming both."""
    tags: dict[str, dict[str, str]] = {}
    comments: dict[str, str | None] = {}
    for row in run(_ENVIRONMENTS):
        tags.setdefault(row["catalog"], {})[row["tag"]] = row["value"]
        comments[row["catalog"]] = row.get("comment")
    found = []
    for catalog, values in sorted(tags.items()):
        if NAME_TAG not in values:
            continue
        found.append(Environment(name=values[NAME_TAG], catalog=catalog, version=values.get(VERSION_TAG, ""),
                                 default=str(values.get(DEFAULT_TAG, "")).strip().lower() == "true",
                                 description=comments.get(catalog)))
    by_name: dict[str, list[Environment]] = {}
    for env in found:
        by_name.setdefault(env.name, []).append(env)
    doubled = {name: envs for name, envs in by_name.items() if len(envs) > 1}
    if doubled:
        raise EnvironmentLookupError("; ".join(
            f"environment {name!r} is tagged on catalogs {', '.join(e.catalog for e in envs)}"
            for name, envs in sorted(doubled.items())) + " (CLIENT-ENVIRONMENT)")
    return sorted(found, key=lambda e: e.name)


@dataclass(frozen=True)
class Discovery:
    """What one discovery found: the environments, and the governance warning to give the caller, if any."""
    environments: tuple[Environment, ...]
    note: str | None = None

    def emit(self) -> list[Environment]:
        """The environments, after warning on the calling thread (`CLIENT-ENVIRONMENT`)."""
        _warn(self.note)
        return list(self.environments)


def discover_environments(run) -> Discovery:
    """CLIENT-DISCOVERY-PARALLEL: the environments ``run`` can see, with the process's one governance check run beside
    the environment query, on its own thread, instead of after it. Warns nothing, so it may itself run off the caller's
    thread; :meth:`Discovery.emit` warns. ``run`` is called from two threads at once on the first discovery, the
    second in a copy of the caller's context, so a runner that reads the request's identity sees the caller's."""
    if not _claim_governance_check():
        return Discovery(tuple(_read_environments(run)))
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cql-governance") as pool:
        note = pool.submit(contextvars.copy_context().run, _governance_note, run)
        found = _read_environments(run)
        return Discovery(tuple(found), note.result())


def find_environments(run) -> list[Environment]:
    """CLIENT-ENVIRONMENT-RESOLVER: :func:`discover_environments`, warning on this thread."""
    return discover_environments(run).emit()


class EnvironmentResolver:
    """CLIENT-ENVIRONMENT-RESOLVER: the environments ``run`` (the caller's SQL runner) can see, and the resolvers of
    each. The environments are read once, on first use.

    ``found`` replaces where the list comes from: an :class:`EnvironmentCache`, or a discovery already started
    (`CLIENT-DISCOVERY-PARALLEL`). ``refresh``, when given, finds the list again; a name not in the list, or a default
    that is missing or doubled, asks it once before failing, since a held list can be older than the catalog's tags
    (`CLIENT-ENVIRONMENT-CACHE`)."""

    def __init__(self, run, *, found: Callable[[], list[Environment]] | None = None,
                 refresh: Callable[[], list[Environment]] | None = None) -> None:
        self._run = run
        self._find = found if found is not None else (lambda: find_environments(run))
        self._refresh = refresh
        self._found: list[Environment] | None = None

    def environments(self) -> list[Environment]:
        """Every environment the caller can see, by name. Two catalogs with one name fail, naming both."""
        if self._found is None:
            self._found = list(self._find())
        return self._found

    def _refreshed(self) -> bool:
        """Find the list again, once per resolver, when it has a ``refresh``. True when it did."""
        if self._refresh is None:
            return False
        refresh, self._refresh = self._refresh, None
        self._found = list(refresh())
        return True

    def default(self) -> Environment:
        """The default environment. Two visible defaults fail, naming both; none fails, naming the tag."""
        defaults = [e for e in self.environments() if e.default]
        if len(defaults) != 1 and self._refreshed():
            defaults = [e for e in self.environments() if e.default]
        if len(defaults) > 1:
            raise EnvironmentLookupError(f"{len(defaults)} catalogs are tagged {DEFAULT_TAG} = true: "
                                   f"{', '.join(e.catalog for e in defaults)}; untag all but one "
                                   f"(CLIENT-ENVIRONMENT-RESOLVER)")
        if not defaults:
            visible = ", ".join(f"{e.name} ({e.catalog})" for e in self.environments()) or "none"
            raise EnvironmentLookupError(f"no catalog you can see is tagged {DEFAULT_TAG} = true; environments you can see: "
                                   f"{visible} (CLIENT-ENVIRONMENT-RESOLVER)")
        return _supported(defaults[0])

    def get(self, name: str) -> Environment:
        """The environment named ``name``. One not found fails, naming those that are."""
        env = self._named(name)
        if env is None and self._refreshed():
            env = self._named(name)
        if env is not None:
            return _supported(env)
        visible = ", ".join(e.name for e in self.environments()) or "none"
        raise EnvironmentLookupError(f"no environment named {name!r} is visible to you; environments you can see: {visible} "
                               f"(CLIENT-ENVIRONMENT-RESOLVER)")

    def _named(self, name: str) -> Environment | None:
        return next((env for env in self.environments() if env.name == name), None)

    def resolve(self, name: str | None = None) -> Environment:
        """The environment named ``name``, or the default when None."""
        return self.default() if name is None else self.get(name)

    # ------------------------------------------------------------------ the resolvers of one environment

    def data(self, env: Environment) -> SchemaDataResolver:
        """BIND-PUBLISHED-VIEW: the environment's ``data`` relations, by rule."""
        return SchemaDataResolver(env.schema(DATA))

    def terminology(self, env: Environment) -> TableTerminologyResolver:
        """BIND-PUBLISHED-VIEW: the environment's ``knowledge.valueset``, by rule, which publishing writes."""
        return TableTerminologyResolver(f"{env.schema(KNOWLEDGE)}.valueset", self._run)

    def content(self, env: Environment, runner: Callable | None = None):
        """The environment's knowledge and terminology as a
        :class:`~cql_databricks.data_bindings.ContentResolver` over its fixed schemas."""
        from cql_databricks.data_bindings import ContentResolver

        return ContentResolver(runner or (lambda: self._run), env.catalog, env.name)

    def knowledge(self, env: Environment, content=None) -> TableKnowledgeResolver:
        """BIND-PUBLISHED-VIEW: the environment's ``knowledge``, which runs read and the client never writes
        (`DEC-NO-CONTENT-PUBLISHING`)."""
        held = content if content is not None else self.content(env)
        return TableKnowledgeResolver(lambda: held)

    def resolvers(self, env: Environment, results=None) -> Resolvers:
        """CLIENT-RESOLVERS: a run's resolvers in ``env``: its data and terminology, and ``results``, one of
        :meth:`results`, :meth:`draft_store` or :meth:`official_store`, when the run keeps or reuses results."""
        return Resolvers(data=self.data(env), terminology=self.terminology(env), results=results,
                         environment=env.name)

    def results(self, env: Environment) -> Overlay:
        """CLIENT-RESULT-RESOLVER: the caller's own results in ``env``'s ``draft_results`` over the official ones in its
        ``results`` (`RUN-CLIENT-RESULT-OWNERSHIP`). Writes nothing."""
        from cql_databricks.result.store import SchemaResultResolver

        return Overlay(SchemaResultResolver(env.address(DRAFT_RESULTS), self._run), self._official(env))

    def reader(self, env: Environment, destination) -> object:
        """RUN-CLIENT-REQUEST-REOPEN: the read-only result resolver a selection written to ``destination`` reopens
        through: :meth:`results` for ``env``'s ``draft_results``, and ``env``'s official ``results`` alone for a
        publisher's. Any other schema fails, naming it."""
        destination = tuple(destination)
        if destination == env.address(DRAFT_RESULTS):
            return self.results(env)
        if destination == env.address(RESULTS):
            return self._official(env)
        raise ValueError(f"{'.'.join(destination)} is not a result schema of environment {env.name!r} "
                         f"({env.catalog}.{DRAFT_RESULTS} or {env.catalog}.{RESULTS})")

    def draft_store(self, env: Environment, *, capacity: int | None = None) -> Overlay:
        """RUN-CLIENT-DRAFT-RESULTS: the caller's own results in ``env``'s ``draft_results``, where it writes, over the
        official ones in its ``results``: the Designer's runs."""
        from cql_databricks.result.store import SchemaResultStore

        return Overlay(SchemaResultStore(env.address(DRAFT_RESULTS), self._run, capacity=capacity),
                       self._official(env))

    def official_store(self, env: Environment) -> Overlay:
        """RUN-CLIENT-DRAFT-RESULTS: the caller's own results in ``env``'s ``results``, where it writes, over any
        writer's there, for its publishers. It takes only a calculation of ``env``'s own content: its data and
        terminology, and a library from its knowledge."""
        from cql_databricks.result.store import SchemaResultStore

        return Overlay(SchemaResultStore(env.address(RESULTS), self._run, requires=official_identities(self, env)),
                       self._official(env))

    def _official(self, env: Environment):
        """RUN-CLIENT-RESULT-OWNERSHIP: any writer's results in ``env``'s ``results``, official by construction."""
        from cql_databricks.result.store import OFFICIAL, SchemaResultResolver

        return SchemaResultResolver(env.address(RESULTS), self._run, reuse=OFFICIAL)


#: CLIENT-ENVIRONMENT-CACHE: how long an App keeps a caller's environments before finding them again.
CACHE_MAX_AGE_SECONDS = 300.0


class EnvironmentCache:
    """CLIENT-ENVIRONMENT-CACHE: the environments each caller can see, kept for the process that holds the cache.

    An App serves many calls for many users, and each call builds its resolvers anew, so a resolver's own once-only
    read does not outlive the call. This keeps each caller's list, keyed by whatever names the caller to the holder
    (an App keys by the signed-in user, as its held warehouse sessions are), never shared between callers, since
    each sees only the catalogs Unity Catalog shows them. It holds environments, never a runner: a forwarded token
    expires.

    A list is found again when it is older than ``max_age`` seconds, when :meth:`drop` drops it, and once when a
    resolver from :meth:`resolver` cannot find a name or a single default in it. A catalog retagged while its
    old catalog still exists is seen at the latest after ``max_age``."""

    def __init__(self, *, max_age: float = CACHE_MAX_AGE_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._max_age = max_age
        self._clock = clock
        self._lock = threading.Lock()
        self._held: dict[str, tuple[list[Environment], float]] = {}
        self._generation = 0

    @property
    def generation(self) -> int:
        """Moves when a caller's list changes or is dropped; reading it never finds anything."""
        return self._generation

    def resolver(self, caller: str, run) -> EnvironmentResolver:
        """An :class:`EnvironmentResolver` on ``run`` whose list is ``caller``'s, held here."""
        return EnvironmentResolver(run, found=lambda: self.environments(caller, run),
                                   refresh=lambda: self.reload(caller, run))

    def environments(self, caller: str, run) -> list[Environment]:
        """``caller``'s held list, or the one ``run`` finds when none is held or it is too old."""
        with self._lock:
            held = self._held.get(caller)
        if held is not None and self._clock() - held[1] < self._max_age:
            return list(held[0])
        return self.reload(caller, run)

    def reload(self, caller: str, run) -> list[Environment]:
        """Find ``caller``'s list through ``run`` and hold it. The lookup runs outside the lock: it takes seconds, and
        another caller's must not wait on it."""
        found = find_environments(run)
        with self._lock:
            before = self._held.get(caller)
            self._held[caller] = (found, self._clock())
            if before is None or before[0] != found:
                self._generation += 1
        return list(found)

    def drop(self, caller: str) -> None:
        """Forget ``caller``'s list; the next read finds it again."""
        with self._lock:
            self._held.pop(caller, None)
            self._generation += 1


def official_identities(environments: EnvironmentResolver, env: Environment) -> dict[str, frozenset[str]]:
    """RUN-CLIENT-DRAFT-RESULTS: the resolver identities a calculation of ``env``'s own content binds, by role: its data
    and terminology, and its knowledge, read by name or from its location ``store:<catalog>.knowledge``."""
    return {"data": frozenset({environments.data(env).identity()}),
            "terminology": frozenset({environments.terminology(env).identity()}),
            "knowledge": frozenset({f"{env.catalog}.{KNOWLEDGE}", f"store:{env.catalog}.{KNOWLEDGE}"})}



def _supported(env: Environment) -> Environment:
    if env.version not in SUPPORTED_LAYOUTS:
        raise EnvironmentLookupError(f"environment {env.name!r} ({env.catalog}) follows layout version "
                               f"{env.version or '(none)'!r}, and this client reads {', '.join(SUPPORTED_LAYOUTS)}: "
                               f"upgrade the client, or fix the catalog's {VERSION_TAG} tag (CLIENT-ENVIRONMENT)")
    return env
