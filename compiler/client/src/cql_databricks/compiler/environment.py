"""CLIENT-ENVIRONMENT, CLIENT-ENVIRONMENT-RESOLVER: environments, and the resolver that finds them.

An environment is a Unity Catalog catalog tagged ``cql_databricks_environment = <name>`` and
``cql_databricks_environment_version = <layout version>``; the default one is also tagged
``cql_databricks_environment_default = true``. Its description is the catalog comment. It holds every dependency of a
run in the schemas of ``DATA-ENVIRONMENT-LAYOUT``:

| Schema | Holds |
|---|---|
| ``data`` | published views, one per FHIR type, named for the type in lower case, on the contract |
| ``terminology`` | the published view ``valueset(valueset_id, system, code, version)`` |
| ``knowledge`` | the published views ``library`` and ``measure`` |
| ``data_store``, ``terminology_store``, ``knowledge_store`` | the store tables publishing writes; optional |
| ``results``, ``draft_results`` | kept results (`RUN-CLIENT-DRAFT-RESULTS`) |

:class:`EnvironmentResolver` finds environments with one query of ``system.information_schema.catalog_tags``, as the
caller, so only catalogs the caller can see are found. A run in an environment binds its published views by name,
through rules (`BIND-PUBLISHED-VIEW`): one compiler call, and no other catalog query. No layout is checked at run time:
a missing view fails in the warehouse, naming it.

Part of the client core: SQL goes through the caller's SQL runner; nothing here imports Spark or a platform SDK.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .data_resolvers import SchemaDataResolver
from .knowledge_resolvers import TableKnowledgeResolver
from .terminology_resolvers import TableTerminologyResolver

#: CLIENT-ENVIRONMENT: the catalog tags that make a catalog an environment.
NAME_TAG = "cql_databricks_environment"
VERSION_TAG = "cql_databricks_environment_version"
DEFAULT_TAG = "cql_databricks_environment_default"

#: CLIENT-ENVIRONMENT: the layout version this client writes, and every version it reads.
LAYOUT_VERSION = "1"
SUPPORTED_LAYOUTS = ("1",)

#: DATA-ENVIRONMENT-LAYOUT: the standard schema names.
DATA, TERMINOLOGY, KNOWLEDGE = "data", "terminology", "knowledge"
DATA_STORE, TERMINOLOGY_STORE, KNOWLEDGE_STORE = "data_store", "terminology_store", "knowledge_store"
RESULTS, DRAFT_RESULTS = "results", "draft_results"

_ENVIRONMENTS = f"""
SELECT t.catalog_name AS catalog, t.tag_name AS tag, t.tag_value AS value, c.comment AS comment
  FROM system.information_schema.catalog_tags t
  JOIN system.information_schema.catalogs c ON c.catalog_name = t.catalog_name
 WHERE t.tag_name IN ('{NAME_TAG}', '{VERSION_TAG}', '{DEFAULT_TAG}')
"""

_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


class EnvironmentResolver:
    """CLIENT-ENVIRONMENT-RESOLVER: the environments ``run`` (the caller's SQL runner) can see, and the resolvers of
    each. The environments are read once, on first use."""

    def __init__(self, run) -> None:
        self._run = run
        self._found: list[Environment] | None = None

    def environments(self) -> list[Environment]:
        """Every environment the caller can see, by name. Two catalogs with one name fail, naming both."""
        if self._found is None:
            tags: dict[str, dict[str, str]] = {}
            comments: dict[str, str | None] = {}
            for row in self._run(_ENVIRONMENTS):
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
            self._found = sorted(found, key=lambda e: e.name)
        return self._found

    def default(self) -> Environment:
        """The default environment. Two visible defaults fail, naming both; none fails, naming the tag."""
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
        for env in self.environments():
            if env.name == name:
                return _supported(env)
        visible = ", ".join(e.name for e in self.environments()) or "none"
        raise EnvironmentLookupError(f"no environment named {name!r} is visible to you; environments you can see: {visible} "
                               f"(CLIENT-ENVIRONMENT-RESOLVER)")

    def resolve(self, name: str | None = None) -> Environment:
        """The environment named ``name``, or the default when None."""
        return self.default() if name is None else self.get(name)

    # ------------------------------------------------------------------ the resolvers of one environment

    def data(self, env: Environment) -> SchemaDataResolver:
        """BIND-PUBLISHED-VIEW: the environment's ``data`` views, by rule."""
        return SchemaDataResolver(env.schema(DATA))

    def terminology(self, env: Environment) -> TableTerminologyResolver:
        """BIND-PUBLISHED-VIEW: the environment's ``terminology.valueset`` view, by rule; its store is
        ``terminology_store.valueset``."""
        return TableTerminologyResolver(f"{env.schema(TERMINOLOGY)}.valueset", self._run,
                                        store=f"{env.schema(TERMINOLOGY_STORE)}.valueset")

    def content(self, env: Environment, runner: Callable | None = None):
        """The environment's knowledge and terminology as a
        :class:`~cql_databricks.data_bindings.ContentResolver` over its fixed schemas."""
        from cql_databricks.data_bindings import ContentResolver

        return ContentResolver(runner or (lambda: self._run), env.catalog, env.name)

    def knowledge(self, env: Environment, content=None) -> TableKnowledgeResolver:
        """BIND-PUBLISHED-VIEW: the environment's ``knowledge`` views; its store is ``knowledge_store``."""
        held = content if content is not None else self.content(env)
        return TableKnowledgeResolver(lambda: held, store=env.address(KNOWLEDGE_STORE),
                                      view=env.address(KNOWLEDGE), run=self._run)


def _supported(env: Environment) -> Environment:
    if env.version not in SUPPORTED_LAYOUTS:
        raise EnvironmentLookupError(f"environment {env.name!r} ({env.catalog}) follows layout version "
                               f"{env.version or '(none)'!r}, and this client reads {', '.join(SUPPORTED_LAYOUTS)}: "
                               f"upgrade the client, or fix the catalog's {VERSION_TAG} tag (CLIENT-ENVIRONMENT)")
    return env
