"""DEPLOY-DEFAULT-ENVIRONMENT, DATA-ENVIRONMENT-LAYOUT — build an environment in a catalog, as the deployer.

An environment is a catalog with the standard schemas (`DATA-ENVIRONMENT-LAYOUT`) and the catalog tags that make it
one (`CLIENT-ENVIRONMENT`). The default one is in ``uc_catalog``: its schemas and their grants are bundle resources
(``databricks.yml``), and the deploy fills them with :func:`fill`. The large synthetic population is an environment of
its own, in a catalog the bundle does not hold (`DATA-SYNTH-LARGE`), so :func:`build` makes its schemas and grants too:

1. the schemas (:func:`build` only), then the store tables the content is written to (the import and the population
   lander);
2. the published views, each selecting its store;
3. the catalog tags and comment, last, once everything a run reads exists; ``default`` only if no catalog the
   deployer can see is the default already;
4. the grants of ``DEPLOY-DEFAULT-ENVIRONMENT`` (:func:`build` only).

Every statement is idempotent, and any failure raises, which fails the deploy.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from cql_databricks.resolvers.environment import (DATA, DATA_STORE, DEFAULT_TAG, DRAFT_RESULTS, KNOWLEDGE,
                                                  KNOWLEDGE_STORE, LAYOUT_VERSION, NAME_TAG, RESULTS, VERSION_TAG,
                                                  EnvironmentResolver)

log = logging.getLogger("shipped_sources.environment")

#: DATA-ENVIRONMENT-LAYOUT — every schema of an environment, and what each holds.
SCHEMAS: dict[str, str] = {
    DATA: "Published data: one view per FHIR type on the contract (__subject_id, __resource), and change_log",
    DATA_STORE: "The data store the data views select",
    KNOWLEDGE: "Published knowledge: the library, measure and valueset views",
    KNOWLEDGE_STORE: "The knowledge store the library, measure and valueset views select",
    RESULTS: "Published results, written by their publishers",
    DRAFT_RESULTS: "Each user's own results, one table per result, owned by its writer",
}

#: The schemas readers get SELECT on (`DATA-ENVIRONMENT-LAYOUT`): the published ones, never a store.
PUBLISHED = (DATA, KNOWLEDGE, RESULTS)

#: DATA-CHANGE-LOG-PERIODS — the change log's name in ``data`` and in ``data_store``.
CHANGE_LOG = "change_log"


def quote(*parts: str) -> str:
    return ".".join("`" + part.replace("`", "``") + "`" for part in parts)


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def schema_statements(catalog: str) -> list[str]:
    """DATA-ENVIRONMENT-LAYOUT — every schema of the environment in ``catalog``."""
    return [f"CREATE SCHEMA IF NOT EXISTS {quote(catalog, schema)} COMMENT {_literal(comment)}"
            for schema, comment in SCHEMAS.items()]


def data_view(catalog: str, fhir_type: str) -> str:
    """BIND-PUBLISHED-VIEW — the published view of ``fhir_type``, selecting its store table."""
    name = fhir_type.lower()
    return (f"CREATE OR REPLACE VIEW {quote(catalog, DATA, name)} AS "
            f"SELECT `__subject_id`, `__resource` FROM {quote(catalog, DATA_STORE, name)}")


def change_log_statements(catalog: str) -> list[str]:
    """DATA-CHANGE-LOG-PERIODS — the store's change log, and the published view over it."""
    return [f"CREATE TABLE IF NOT EXISTS {quote(catalog, DATA_STORE, CHANGE_LOG)} "
            f"(parameters MAP<STRING, STRING>, changed_at TIMESTAMP) "
            f"COMMENT 'DATA-CHANGE-LOG-PERIODS: which evaluation sets changed, by parameter value; an empty map is "
            f"every open evaluation set'",
            f"CREATE OR REPLACE VIEW {quote(catalog, DATA, CHANGE_LOG)} AS "
            f"SELECT parameters, changed_at FROM {quote(catalog, DATA_STORE, CHANGE_LOG)}"]


def change_log_all_statement(catalog: str) -> str:
    """One change-log row saying every open evaluation set changed now: what a whole-population reload logs."""
    return f"INSERT INTO {quote(catalog, DATA_STORE, CHANGE_LOG)} VALUES (map(), current_timestamp())"


def content_view_statements(catalog: str) -> list[str]:
    """BIND-PUBLISHED-VIEW — the knowledge views, each selecting its store."""
    return [f"CREATE OR REPLACE VIEW {quote(catalog, KNOWLEDGE, 'valueset')} AS "
            f"SELECT valueset_id, system, code, version FROM {quote(catalog, KNOWLEDGE_STORE, 'valueset')}",
            *(f"CREATE OR REPLACE VIEW {quote(catalog, KNOWLEDGE, table)} AS "
              f"SELECT * FROM {quote(catalog, KNOWLEDGE_STORE, table)}" for table in ("library", "measure"))]


def tag_statements(catalog: str, *, name: str, description: str, default: bool) -> list[str]:
    """CLIENT-ENVIRONMENT — the tags that make ``catalog`` the environment ``name``, at this client's layout version,
    and its description as the catalog comment."""
    tags = {NAME_TAG: name, VERSION_TAG: LAYOUT_VERSION, **({DEFAULT_TAG: "true"} if default else {})}
    pairs = ", ".join(f"{_literal(k)} = {_literal(v)}" for k, v in tags.items())
    return [f"ALTER CATALOG {quote(catalog)} SET TAGS ({pairs})",
            f"COMMENT ON CATALOG {quote(catalog)} IS {_literal(description)}"]


def catalog_grant(catalog: str, principal: str) -> str:
    """DEPLOY-DEFAULT-ENVIRONMENT — ``USE CATALOG`` on the environment's catalog, which a schema grant needs."""
    return f"GRANT USE CATALOG ON CATALOG {quote(catalog)} TO {quote(principal)}"


def maintenance_grant(catalog: str, principal: str) -> str:
    """RUN-CLIENT-MAINTENANCE — ``MANAGE`` on ``draft_results``, so the maintenance Job expires its tables."""
    return f"GRANT USE SCHEMA, MANAGE ON SCHEMA {quote(catalog, DRAFT_RESULTS)} TO {quote(principal)}"


def grant_statements(catalog: str, *, users: str, maintenance: str | None,
                     content_readers: Iterable[str] = ()) -> list[str]:
    """DEPLOY-DEFAULT-ENVIRONMENT, RUN-CLIENT-DRAFT-RESULTS — the grants of an environment outside the bundle; the
    default environment's schema grants are ``databricks.yml``'s, the same set.

    ``users`` (the App users' group) read the published schemas and create their own tables in ``draft_results``,
    with no ``SELECT`` or ``MODIFY`` on it, so each reads only the tables they own. ``maintenance``, the maintenance
    Job's principal, gets ``MANAGE`` on ``draft_results`` to expire its tables (`RUN-CLIENT-MAINTENANCE`).
    ``content_readers`` (the Designer App's service principal) read the knowledge views, whose libraries and value
    sets it holds in memory (`KNOWLEDGE-LAKE-DIRECT`) and nothing else: never a store or a result."""
    statements = [catalog_grant(catalog, users),
                  *(f"GRANT USE SCHEMA, SELECT ON SCHEMA {quote(catalog, schema)} TO {quote(users)}"
                    for schema in PUBLISHED),
                  f"GRANT USE SCHEMA, CREATE TABLE ON SCHEMA {quote(catalog, DRAFT_RESULTS)} TO {quote(users)}"]
    if maintenance:
        statements += [catalog_grant(catalog, maintenance), maintenance_grant(catalog, maintenance)]
    for reader in content_readers:
        statements += [catalog_grant(catalog, reader),
                       f"GRANT USE SCHEMA, SELECT ON SCHEMA {quote(catalog, KNOWLEDGE)} TO {quote(reader)}"]
    return statements


def other_defaults(run, catalog: str) -> list[str]:
    """CLIENT-ENVIRONMENT-RESOLVER — the catalogs other than ``catalog`` tagged default that ``run`` can see."""
    return [e.catalog for e in EnvironmentResolver(run).environments() if e.default and e.catalog != catalog]


def fill(sql: Callable[[str], object], run, *, catalog: str, name: str, description: str, default: bool,
         load: Callable[[], dict[str, int]]) -> dict[str, int]:
    """DEPLOY-DEFAULT-ENVIRONMENT — fill the environment ``name`` in ``catalog``, whose schemas exist: ``load`` (which
    writes the stores and the data views), the content views and the tags. ``sql`` runs a statement and ``run`` is a
    SQL runner (``spark_runner``) for the one environment query. With ``default``, the catalog is tagged the default
    only when no other catalog the deployer can see is. Returns ``load``'s counts."""
    counts = load()
    for statement in content_view_statements(catalog):
        sql(statement)
    others = other_defaults(run, catalog) if default else []
    if others:
        log.warning("%s is not tagged the default environment: %s already is (CLIENT-ENVIRONMENT-RESOLVER)",
                    catalog, ", ".join(others))
    for statement in tag_statements(catalog, name=name, description=description, default=default and not others):
        sql(statement)
    log.info("built environment %s in %s%s", name, catalog, " (default)" if default and not others else "")
    return counts


def build(sql: Callable[[str], object], run, *, catalog: str, name: str, description: str, default: bool,
          load: Callable[[], dict[str, int]], users: str, maintenance: str | None = None,
          content_readers: Iterable[str] = ()) -> dict[str, int]:
    """DATA-SYNTH-LARGE — build the environment ``name`` in ``catalog``, a catalog the bundle does not hold: its
    schemas, :func:`fill`, and the grants. Returns ``load``'s counts."""
    for statement in schema_statements(catalog):
        sql(statement)
    counts = fill(sql, run, catalog=catalog, name=name, description=description, default=default, load=load)
    for statement in grant_statements(catalog, users=users, maintenance=maintenance, content_readers=content_readers):
        sql(statement)
    return counts
