"""CLIENT-ENVIRONMENT, BIND-PUBLISHED-VIEW — what a long-running caller (an App) holds for one environment: its
knowledge and terminology schemas, by their standard names, and the knowledge corpus, cached.

Loading the corpus takes seconds, so it happens once and is reused until ``reload()``. The runner is a factory, so
constructing this touches no workspace; the first read does. Nothing is searched for by tag and no layout is checked:
a missing view fails the read that needs it (`BIND-PUBLISHED-VIEW`).
"""
from __future__ import annotations

import threading
from typing import Callable

from .corpus import Corpus, load_corpus
from .runner import SqlRunner
from .tags import KNOWLEDGE_TAG, TERMINOLOGY_TAG, TaggedSchema


class ContentResolver:
    def __init__(self, runner: Callable[[], SqlRunner], catalog: str, name: str) -> None:
        """The content of the environment ``name``, whose catalog is ``catalog`` (`DATA-ENVIRONMENT-LAYOUT`)."""
        self._runner = runner
        self.catalog, self.name = catalog, name
        self._knowledge = TaggedSchema(tag=KNOWLEDGE_TAG, name=name, catalog=catalog, schema="knowledge")
        self._terminology = TaggedSchema(tag=TERMINOLOGY_TAG, name=name, catalog=catalog, schema="terminology")
        self._lock = threading.Lock()
        self._run: SqlRunner | None = None
        self._corpus: Corpus | None = None
        self._generation = 0

    @property
    def generation(self) -> int:
        """Moves on every load and every ``reload()``; a cache key that never forces a load itself."""
        return self._generation

    def _sql(self) -> SqlRunner:
        if self._run is None:
            self._run = self._runner()
        return self._run

    def corpus(self) -> Corpus:
        """The environment's knowledge, loaded once. A failed load raises and is retried next call."""
        with self._lock:
            if self._corpus is None:
                self._corpus = load_corpus(self._sql(), [self._knowledge], check=False)
                self._generation += 1
            return self._corpus

    def knowledge_sources(self) -> list[TaggedSchema]:
        return self.corpus().sources

    def terminology_sources(self) -> list[TaggedSchema]:
        """The environment's terminology schema."""
        return [self._terminology]

    def reload(self) -> None:
        """Forget the corpus; the next read loads it again."""
        with self._lock:
            self._corpus = None
            self._generation += 1
