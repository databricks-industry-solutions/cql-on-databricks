"""CONTENT-RESOLVER — what a long-running caller (an App) holds: tag lookups and the corpus, cached.

Tag searches take seconds, so they happen once and are reused until ``reload()``: resolve per compile or
deploy, never per request (`DEC-DATA-SOURCE-UC-TAG`). The runner is a factory, so constructing this
touches no workspace; the first read does.

Polling the Delta table version to reload on its own (`KNOWLEDGE-CORPUS-REFRESH`) is not built. Until it
is, new content is visible after ``reload()`` or a restart.

CLIENT-DISCOVERY-SCOPE — every lookup is confined to the caller's catalog scopes. The three tag keys are found
together in one query, the first time any of them is asked for. A caller that names no scopes searches every
catalog it can see (``*``), so a first run finds something; a warning says how to confine it.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Callable

from .corpus import Corpus, load_corpus
from .layout import TERMINOLOGY, check_layout
from .runner import SqlRunner
from .tags import (DATA_SOURCE_TAG, EVERYWHERE, KNOWLEDGE_TAG, TERMINOLOGY_TAG, CatalogScope, TaggedSchema,
                   find_tagged, parse_scopes)

log = logging.getLogger(__name__)


class ContentResolver:
    def __init__(self, runner: Callable[[], SqlRunner], scopes: Sequence[str] | None = None) -> None:
        """``scopes`` — CLIENT-DISCOVERY-SCOPE, the caller's ordered catalog scopes; checked here, before any query.

        ``None`` (none configured) means ``["*"]``, every catalog the caller can see, with a warning. An empty list
        is a mistake, not "none configured", and fails."""
        self._runner = runner
        if scopes is None:
            log.warning("No catalog scopes are configured, so discovery searches every catalog you can see "
                        "(CLIENT-DISCOVERY-SCOPE). To confine it, pass scopes or set "
                        "spark.cqldatabricks.catalogScopes or CQL_DATABRICKS_CATALOG_SCOPES.")
            scopes = [EVERYWHERE]
        self._scopes = parse_scopes(scopes)
        self._lock = threading.Lock()
        self._run: SqlRunner | None = None
        self._tagged: dict[str, list[TaggedSchema]] | None = None
        self._corpus: Corpus | None = None
        self._terminology: list[TaggedSchema] | None = None
        self._generation = 0

    @property
    def scopes(self) -> tuple[CatalogScope, ...]:
        return self._scopes

    @property
    def generation(self) -> int:
        """Moves on every load and every ``reload()``; a cache key that never forces a load itself."""
        return self._generation

    def _sql(self) -> SqlRunner:
        if self._run is None:
            self._run = self._runner()
        return self._run

    def _found(self, tag: str) -> list[TaggedSchema]:
        """The schemas tagged ``tag`` in scope. The caller holds the lock; one query serves all three keys."""
        if self._tagged is None:
            self._tagged = find_tagged(self._sql(), self._scopes)
            self._generation += 1
        return self._tagged[tag]

    def corpus(self) -> Corpus:
        """Every tagged knowledge source in scope, loaded once, earliest scope first so its copy of a library
        wins. A failed load raises and is retried next call."""
        with self._lock:
            if self._corpus is None:
                self._corpus = load_corpus(self._sql(), self._found(KNOWLEDGE_TAG))
                self._generation += 1
            return self._corpus

    def knowledge_sources(self) -> list[TaggedSchema]:
        return self.corpus().sources

    def terminology_sources(self) -> list[TaggedSchema]:
        """Every schema tagged ``cql_databricks_terminology`` in scope, each checked against its layout."""
        with self._lock:
            if self._terminology is None:
                found = self._found(TERMINOLOGY_TAG)
                for schema in found:
                    check_layout(self._sql(), schema, TERMINOLOGY)
                self._terminology = found
                self._generation += 1
            return self._terminology

    def data_source_schemas(self) -> list[TaggedSchema]:
        """Every schema tagged ``cql_databricks_data_source`` in scope (`DATA-SOURCE-UC-TAG`).

        Found here, not checked here: the data-source layout is the compiler's
        (``cql_databricks.data_source``), and content depends on no other feature.
        """
        with self._lock:
            return self._found(DATA_SOURCE_TAG)

    def reload(self) -> None:
        """Forget every lookup; the next read searches the tags and loads the corpus again."""
        with self._lock:
            self._tagged = None
            self._corpus = None
            self._terminology = None
            self._generation += 1
