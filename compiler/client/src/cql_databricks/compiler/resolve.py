"""BUILD-RESOLVE-COMPILE, `DEC-STATELESS-SERVICES` — resolve a library for a compile, once, the same way for every caller.

Every caller that compiles or evaluates a library from tagged content needs the same three answers, centralized here:

- **the closure** (:func:`resolve`) — the root from tagged knowledge (``library``, ``version``) or as CQL text, and
  its includes from the knowledge corpus (``Corpus.resolve_closure``): compile order, includes matched only by name,
  and includes not found. A caller that cannot continue with gaps calls :meth:`Resolution.require_complete`; one that
  shows them (the Designer's editor) reads :meth:`Resolution.unresolved_messages`.
- **the data source** (:func:`resolve_data_source`) — named by its tag value (``cql_databricks_data_source =
  <name>``, `DEC-DATA-SOURCE-UC-TAG`), its bindings read with the data-source layout check (`BIND-DATA-SOURCE-LAYOUT`).
- **the ELM** (:func:`translate_root`, :func:`translate_includes`) — the root against every include, and each include
  against its own transitive includes only (PERF-CLOSURE-TRANSLATE). Every library is returned with its diagnostics;
  none is skipped.

Every read goes through the caller's resolver and SQL runner, so it runs as the caller. ``translate`` is any callable
``(cql, includes) -> {ok, elm, diagnostics}``: ``RemoteProvider.translate_result`` here, or the Apps' compiler client.

data-bindings is imported here and nowhere else in this package's import graph, so ``import cql_databricks.compiler``
does not need it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cql_databricks.data_bindings import (ContentResolver, IncludeRef, ResolvedLibrary, SqlRunner, parse_identity,
                                          parse_includes)

from .data_source import DataSourceLayout, check_layout, read_routines

#: ``(cql, includes) -> {"ok": bool, "elm": str | None, "diagnostics": [str]}``.
Translate = Callable[[str, Sequence[str]], Mapping[str, Any]]


class ResolutionError(RuntimeError):
    """A library, include or data source that cannot be resolved. Raised, never swallowed."""


@dataclass(frozen=True)
class Resolution:
    """A root library and its include closure, in compile order (dependencies first)."""
    name: str
    version: str
    source: str
    includes: tuple[ResolvedLibrary, ...]
    unresolved: tuple[IncludeRef, ...]

    @property
    def include_sources(self) -> list[str]:
        return [r.source for r in self.includes]

    @property
    def sources(self) -> list[str]:
        """The root's source, then every include's: the closure as CQL."""
        return [self.source, *self.include_sources]

    def unresolved_messages(self) -> list[str]:
        """One diagnostic per include not found, as an editor shows them."""
        return [f'Included library "{u.name}" version {u.version} is not in any tagged knowledge source.'
                for u in self.unresolved]

    def require_complete(self) -> "Resolution":
        """This resolution, or :class:`ResolutionError` naming every include not found."""
        if self.unresolved:
            missing = ", ".join(f"{u.name} {u.version}" for u in self.unresolved)
            raise ResolutionError(f"{self.name or 'the CQL'}: includes not in any tagged knowledge source: {missing}")
        return self


def resolve(content: ContentResolver, *, library: str | None = None, version: str | None = None,
            cql: str | None = None) -> Resolution:
    """The closure of ``library`` (``version``, else its newest) from tagged knowledge, or of ``cql`` as given.

    CQL with no ``library`` header resolves too, with an empty name: an editor validates such text."""
    if (library is None) == (cql is None):
        raise ResolutionError("give exactly one of library= (from tagged knowledge) or cql= (CQL text)")
    corpus = content.corpus()
    if cql is not None:
        name, root_version = parse_identity(cql, url="", version="")
        source = cql
    else:
        found = corpus.library(library, version)
        if found is None:
            raise ResolutionError(f"{library} {version or '(newest)'} is not in any tagged knowledge source")
        name, root_version, source = found.name, found.version, found.source
    closure = corpus.resolve_closure(source)
    return Resolution(name=name, version=root_version, source=source, includes=tuple(closure.includes),
                      unresolved=tuple(closure.unresolved))


def is_published(content: ContentResolver, cql: str, library: str, version: str) -> bool:
    """RUN-ADDRESS-BY-LIBRARY, `DEC-PUBLISHED-TEST` — ``cql`` is the corpus's ``library@version`` exactly, line endings
    aside; ``False`` for anything else, including a library or version the corpus does not hold. Only CQL that is the
    published version may be read as, or write, its results; anything else is a draft (`DEC-DRAFT-ADDRESS`)."""
    if not (library and version):
        return False
    published = content.corpus().library(library, version)
    return published is not None and _lines(published.source) == _lines(cql)


def _lines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def resolve_data_source(content: ContentResolver, run: SqlRunner, name: str,
                        modeled_types: Iterable[str], *, include_change_logs: bool = True) -> DataSourceLayout:
    """The bindings of the data source tagged ``name``. Fails naming the tagged names when there is none."""
    schema = _data_source_schema(content, name)
    return check_layout(run, schema.catalog, schema.schema, modeled_types, include_change_logs=include_change_logs)


def resolve_data_source_routines(content: ContentResolver, run: SqlRunner, name: str) -> dict:
    """SVC-COMPILE-BATCH — ship caller-visible catalog metadata with the CQL closure in one request."""
    schema = _data_source_schema(content, name)
    return {"catalog": schema.catalog, "schema": schema.schema,
            "routines": read_routines(run, schema.catalog, schema.schema)}


def _data_source_schema(content: ContentResolver, name: str):
    tagged = {s.name: s for s in content.data_source_schemas()}
    if name not in tagged:
        raise ResolutionError(f"no data source is tagged {name!r} in catalog scopes "
                              f"{', '.join(str(s) for s in content.scopes)}; known: {', '.join(sorted(tagged)) or 'none'}")
    return tagged[name]


@dataclass(frozen=True)
class TranslatedLibrary:
    """One library's ELM, or its diagnostics."""
    name: str
    version: str
    source: str
    elm: str | None
    ok: bool
    diagnostics: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "source": self.source, "elm": self.elm,
                "ok": self.ok, "diagnostics": list(self.diagnostics)}


def translate_root(translate: Translate, resolution: Resolution) -> TranslatedLibrary:
    """The root's ELM, translated with every include of its closure available."""
    return _translated(resolution.name, resolution.version, resolution.source,
                       translate(resolution.source, resolution.include_sources))


def translate_includes(translate: Translate, resolution: Resolution) -> list[TranslatedLibrary]:
    """Each include's ELM, in compile order, each translated against only its own transitive includes.

    ``resolve_closure`` lists each library once by name, so a name-keyed walk finds each dependency's source."""
    by_name = {inc.id.name: inc for inc in resolution.includes}

    def dependencies(source: str) -> list[str]:
        collected: dict[str, str] = {}

        def walk(src: str) -> None:
            for ref in parse_includes(src):
                dep = by_name.get(ref.name)
                if dep is None or ref.name in collected:
                    continue
                collected[ref.name] = dep.source   # before recursing, so a cycle ends
                walk(dep.source)

        walk(source)
        return list(collected.values())

    return [_translated(inc.id.name, inc.id.version, inc.source, translate(inc.source, dependencies(inc.source)))
            for inc in resolution.includes]


def require_translated(libraries: Iterable[TranslatedLibrary]) -> list[TranslatedLibrary]:
    """``libraries``, or :class:`ResolutionError` naming each that did not translate and why."""
    libraries = list(libraries)
    failed = [lib for lib in libraries if not lib.ok]
    if failed:
        raise ResolutionError("did not translate: " + "; ".join(
            f"{lib.name} {lib.version}: {(lib.diagnostics or ('no ELM returned',))[0]}" for lib in failed))
    return libraries


def _translated(name: str, version: str, source: str, result: Mapping[str, Any]) -> TranslatedLibrary:
    elm = result.get("elm")
    if elm is not None and not isinstance(elm, str):
        import json
        elm = json.dumps(elm)
    return TranslatedLibrary(name=name, version=version, source=source, elm=elm,
                             ok=bool(result.get("ok")) and bool(elm),
                             diagnostics=tuple(result.get("diagnostics") or ()))
