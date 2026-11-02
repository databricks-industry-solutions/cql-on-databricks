"""KNOWLEDGE-LAKE-DIRECT — the knowledge corpus, read direct from the tagged tables.

One batch load per knowledge source holds every library (identity, CQL, data requirements) and the
measure → library edges in memory. Include closure, discovery and source lookups then run over that
list in Python, which is what they always did; only the read moved, from a Lakebase mirror kept fresh by
a sync job to the Lakehouse tables themselves.

**The derivations do not change** (`CONTENT-NO-DERIVED-COLUMNS`): base64-decoding the CQL
attachment, parsing the CQL header, and exploding ``Measure.library`` into edges happen here, at load.
All VARIANT navigation uses ``variant_get``, never the ``:`` path operator, which is Databricks-only and fails to parse on OSS Spark.

**Measure resources load lazily** (`KNOWLEDGE-CORPUS-REFRESH`): the whole ``Measure`` is the corpus's
largest payload and a page touches one at a time, so it is a point read when asked for, cached here.
"""
from __future__ import annotations

import functools
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from .cql import IncludeRef, compare_versions, parse_identity, parse_includes, parse_model
from .layout import KNOWLEDGE, check_layout
from .runner import SqlRunner
from .tags import TaggedSchema

log = logging.getLogger("cql_databricks.data_bindings.corpus")

# Decode the base64 CQL attachment by exploding `content[]` and keeping the text/cql entry. ELM is not
# read: nothing in the read path uses it, and it is most of the payload.
_LIBRARIES = """
SELECT url, version,
       max(variant_get(resource, '$.title', 'string')) AS title,
       max(variant_get(resource, '$.description', 'string')) AS description,
       max(to_json(variant_get(resource, '$.dataRequirement'))) AS data_requirement,
       max(CASE WHEN variant_get(c.value, '$.contentType', 'string') = 'text/cql'
                THEN CAST(unbase64(variant_get(c.value, '$.data', 'string')) AS STRING) END) AS content_cql
  FROM {table}, LATERAL variant_explode(variant_get(resource, '$.content')) AS c
 GROUP BY url, version
HAVING content_cql IS NOT NULL AND content_cql <> ''
"""

# CONTENT-MEASURE-LIBRARY-LINK — the junction, derived wholesale on every load so no edge can go stale.
# The `url|version` canonical suffix is stripped so an edge matches the Library's bare `url`.
_MEASURES = """
SELECT url AS measure_url, version AS measure_version,
       variant_get(resource, '$.title', 'string') AS title,
       variant_get(resource, '$.description', 'string') AS description,
       split_part(CAST(variant_get(ref.value, '$', 'string') AS STRING), '|', 1) AS library_url
  FROM {table}, LATERAL variant_explode(variant_get(resource, '$.library')) AS ref
"""

_MEASURE_RESOURCE = """
SELECT to_json(resource) AS resource FROM {table} WHERE url = :url AND version = :version
"""


@dataclass(frozen=True)
class LibraryId:
    name: str
    version: str


@dataclass
class Library:
    """One CQL library, joined to its measure when it is a measure's root."""
    name: str
    version: str
    url: str
    fhir_version: str
    source: str
    description: str
    title: str
    kind: str  # "measure" | "library"
    model: str
    data_requirements: list[dict[str, Any]]
    knowledge: TaggedSchema
    measure: tuple[str, str] | None = None  # (url, version) of the Measure naming this as its root


@dataclass
class ResolvedLibrary:
    id: LibraryId
    source: str


@dataclass
class ResolvedClosure:
    includes: list[ResolvedLibrary] = field(default_factory=list)
    unresolved: list[IncludeRef] = field(default_factory=list)


class Corpus:
    """Every library from every tagged knowledge source, in memory."""

    def __init__(self, run: SqlRunner, sources: list[TaggedSchema], libraries: list[Library]) -> None:
        self._run = run
        self.sources = sources
        self.libraries = libraries
        self._exact: dict[tuple[str, str], Library] = {}
        for lib in libraries:
            self._exact.setdefault((lib.name, lib.version), lib)
        by_name: dict[str, list[Library]] = {}
        for lib in libraries:
            by_name.setdefault(lib.name, []).append(lib)
        # By name, the earliest scope holding the name wins; newest version within it.
        earliest_then_newest = functools.cmp_to_key(lambda a, b: (a.knowledge.position - b.knowledge.position)
                                                    or compare_versions(b.version, a.version))
        self._by_name = {n: sorted(ls, key=earliest_then_newest) for n, ls in by_name.items()}
        self._measures: dict[tuple[str, str], dict[str, Any] | None] = {}
        self._measure_lock = threading.Lock()

    # -- lookups -------------------------------------------------------------------------------------

    def library(self, name: str, version: str | None = None) -> Library | None:
        """By exact (name, version), earliest scope first; or, when ``version`` is None, the newest version of
        ``name`` in the earliest scope that holds it."""
        if version is not None:
            return self._exact.get((name, version))
        candidates = self._by_name.get(name)
        return candidates[0] if candidates else None

    def library_in(self, store: tuple[str, str], name: str, version: str | None = None) -> Library | None:
        """RUN-ADDRESS-LOCATION — ``name`` (exact ``version``, else its newest) from the one tagged store
        ``(catalog, schema)``, or ``None`` when that store does not hold it."""
        held = [lib for lib in self._by_name.get(name, ())
                if (lib.knowledge.catalog, lib.knowledge.schema) == store
                and (version is None or lib.version == version)]
        return held[0] if held else None

    def discover(self) -> list[dict[str, Any]]:
        """Light records for the browse catalog (PERF-DISCOVER-LIGHTWEIGHT): no CQL source."""
        records = []
        for lib in self._exact.values():
            records.append({
                "id": {"name": lib.name, "version": lib.version},
                # RUN-ADDRESS-LOCATION — the store this copy is read from.
                "location": f"store:{lib.knowledge.catalog}.{lib.knowledge.schema}",
                "kind": lib.kind,
                "status": "published",
                "model": lib.model,
                "title": lib.title,
                "metadata": {"description": lib.description, "tags": ["measure", "marketplace"],
                             "author": "system"},
                "updated": "",
            })
        return records

    def resolve_closure(self, source: str, *, prefer: tuple[str, str] | None = None) -> ResolvedClosure:
        """The transitive ``include`` closure of ``source``: dependencies before dependents, each name
        once. An include that pins a version matches only that version; an unpinned one takes the newest by
        name. Includes with no match are surfaced as unresolved.

        ``prefer`` is a store ``(catalog, schema)`` whose own copy of an include wins over every other store's
        (RUN-ADDRESS-LOCATION); an include it lacks resolves from the rest in scope order."""
        includes: list[ResolvedLibrary] = []
        unresolved: list[IncludeRef] = []
        done: set[str] = set()
        in_progress: set[str] = set()

        def visit(refs: list[IncludeRef]) -> None:
            for ref in refs:
                if ref.name in done or ref.name in in_progress:
                    continue
                chosen = ((self.library_in(prefer, ref.name, ref.version) if prefer else None)
                          or self.library(ref.name, ref.version))
                if chosen is None:
                    unresolved.append(ref)
                    done.add(ref.name)
                    continue
                in_progress.add(ref.name)
                visit(parse_includes(chosen.source))
                in_progress.discard(ref.name)
                done.add(ref.name)
                includes.append(ResolvedLibrary(id=LibraryId(chosen.name, chosen.version), source=chosen.source))

        visit(parse_includes(source or ""))
        return ResolvedClosure(includes=includes, unresolved=unresolved)

    def measure_resource(self, name: str, version: str, *,
                         store: tuple[str, str] | None = None) -> dict[str, Any] | None:
        """SPEC-MEASURE-METADATA — the whole FHIR ``Measure`` whose root library is ``name@version``, from the
        one tagged ``store`` when given (RUN-ADDRESS-LOCATION).

        None for an ordinary library: most libraries are not a measure root, and that is not an error.
        A point read the first time, then cached for the life of this corpus.
        """
        lib = self.library_in(store, name, version) if store else self.library(name, version)
        if lib is None or lib.measure is None:
            return None
        key = lib.measure
        with self._measure_lock:
            if key in self._measures:
                return self._measures[key]
        rows = self._run(_MEASURE_RESOURCE.format(table=lib.knowledge.table("measure")),
                         {"url": key[0], "version": key[1]})
        resource = json.loads(rows[0]["resource"]) if rows and rows[0].get("resource") else None
        with self._measure_lock:
            self._measures[key] = resource
        return resource


def load_corpus(run: SqlRunner, sources: list[TaggedSchema], *, check: bool = True) -> Corpus:
    """Read every tagged knowledge source in ``sources`` order; the first copy of a (url, version) wins.

    Each source's layout is checked first, and a source that does not match fails the load: a corpus
    missing one source's libraries would resolve includes wrongly with nothing to say so. An environment's
    published views are not checked (``check=False``, `BIND-PUBLISHED-VIEW`): a missing view fails the read.
    """
    libraries: list[Library] = []
    seen: set[tuple[str, str]] = set()
    for knowledge in sources:
        if check:
            check_layout(run, knowledge, KNOWLEDGE)
        roots: dict[str, dict[str, Any]] = {}
        for edge in run(_MEASURES.format(table=knowledge.table("measure"))):
            if edge.get("library_url"):
                roots.setdefault(edge["library_url"], edge)
        rows = run(_LIBRARIES.format(table=knowledge.table("library")))
        for row in rows:
            key = (row["url"] or "", row["version"] or "")
            if key in seen:
                continue
            seen.add(key)
            cql = row["content_cql"] or ""
            name, version = parse_identity(cql, url=key[0], version=key[1])
            root = roots.get(key[0])
            requirements = json.loads(row["data_requirement"]) if row.get("data_requirement") else []
            libraries.append(Library(
                name=name, version=version, url=key[0], fhir_version=key[1], source=cql,
                description=(root or {}).get("description") or row.get("description") or "",
                title=(root or {}).get("title") or "",
                kind="measure" if root else "library",
                model=parse_model(cql),
                data_requirements=[r for r in requirements if isinstance(r, dict)],
                knowledge=knowledge,
                measure=(root["measure_url"], root["measure_version"]) if root else None))
        log.info("loaded %d libraries from %s.%s (knowledge %r)", len(rows), knowledge.catalog,
                 knowledge.schema, knowledge.name)
    return Corpus(run, sources, libraries)
