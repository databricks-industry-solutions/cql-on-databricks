"""Library SOURCE resolution — the third client seam (`BUILD-LIBRARY-PROVIDER`).

Three orthogonal, pluggable seams reach the compiler; this is the one that answers *which CQL text*:

* :class:`~.providers.CompileProvider` — the TRANSPORT (how the request reaches the compiler).
* :class:`~.providers.CredentialProvider` — the AUTH (how it is authenticated).
* :class:`LibraryProvider` — the SOURCE (where the library and its ``include`` closure come from).

Why the closure walk lives on the CLIENT. `BUILD-COMPILE-LINK` makes the compile service a DUMB endpoint:
it does NO library-source resolution — "the caller resolves the include closure and ships every
dependency; the service only MERGES what it is handed". That is a deliberate purity boundary (the service
reads no filesystem, no registry, no network), so the resolver belongs next to the files. This module is
the caller half of that contract.

A multi-library measure is unusable without it: its root ``include``s shared helper libraries and
FHIRHelpers. Before this seam the only way to compile one was to hand-list the closure (as the Scala
conformance harness still does in ``Corpus.scala``), and the Python client could not do it at all.

TWO RULES decide which source an include resolves to:

1. **Sibling-first.** An ``include`` is resolved against the ROOT's own directory before the wider search
   path. Published content ships a self-contained copy of the shared libraries PER MEASURE, so a flat scan
   of a content repo sees several different copies of ``FHIRHelpers`` and the other shared helpers. The Scala side hit exactly
   this (`CqlToElmCli`'s per-directory grouping: registering a whole corpus at once makes the cqframework
   LibraryManager raise "Multiple libraries for id … resolved" and emit NO ELM for ANY measure). Scoping
   to one closure at a time keeps each measure's dependency set unambiguous.
2. **Exact version.** An include that states a version resolves only to a source declaring that version;
   one that states none resolves by name. A pinned version nothing on the search path declares fails loud,
   as it does in the compiler's translator (HL7 CQL Author's Guide, Libraries).

The service still owns TRANSLATION. A resolved closure ships as CQL text (`BUILD-COMPILE-LINK`'s ``deps``
``{cql}`` entries) and the service registers it as translation sources, so a pure-Python caller with no
JVM never translates anything locally. FHIRHelpers is one of those sources: the compiler bundles none, and
an include no source provides fails (`LANG-FHIRHELPERS-FROM-CONTENT`).
"""
from __future__ import annotations

import abc
import base64
import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import config

#: Entry-point group for third-party library providers (`BUILD-LIBRARY-PROVIDER`). A group of its OWN,
#: not the ``cql_databricks.providers`` group the transport seam uses: that group already distinguishes
#: a second capability by a ``.discovery`` NAME SUFFIX, and stacking a third meaning onto one namespace
#: makes every lookup ambiguous. One group per seam.
LIBRARY_PLUGIN_GROUP = "cql_databricks.libraries"

class LibraryResolutionError(FileNotFoundError):
    """A library (or a member of its ``include`` closure) could not be resolved. Loud by design
    (`CORE-NO-SILENT-MISLOWER`): a silently-dropped dependency compiles to a plan missing whole
    populations. Subclasses ``FileNotFoundError`` so a caller can catch it as an ordinary lookup miss."""


@dataclass(frozen=True)
class LibraryRef:
    """A library ADDRESS: a name and an optional version pin. A pin must match exactly — see the module
    docstring's exact-version rule."""
    name: str
    version: Optional[str] = None

    @staticmethod
    def parse(text: str) -> "LibraryRef":
        """Parse ``"Name"`` or ``"Name|version"`` (the pipe form mirrors a FHIR canonical reference)."""
        name, sep, version = text.partition("|")
        return LibraryRef(name.strip(), version.strip() or None if sep else None)

    def __str__(self) -> str:
        return f"{self.name}|{self.version}" if self.version else self.name


@dataclass(frozen=True)
class LibrarySource:
    """One RESOLVED library: its identity, its body in whichever form(s) the origin carried, and the
    provenance of where it came from.

    ``cql`` and/or ``elm`` may be set — a ``.cql`` file yields CQL, a FHIR ``Library`` resource commonly
    carries BOTH (base64 ``content[]`` entries for ``text/cql`` and ``application/elm+json``). ``origin``
    is the path/URI it was read from, kept so a resolution surprise is diagnosable and never re-derived."""
    name: str
    version: Optional[str] = None
    cql: Optional[str] = None
    elm: Optional[str] = None
    origin: Optional[str] = None

    @property
    def ref(self) -> LibraryRef:
        return LibraryRef(self.name, self.version)

    def __post_init__(self):
        if not (self.cql or self.elm):
            raise ValueError(f"library source '{self.name}' carries neither CQL nor ELM")


class LibraryProvider(abc.ABC):
    """Resolves library ADDRESSES to SOURCES, and walks a root's ``include`` closure."""

    @abc.abstractmethod
    def resolve(self, ref: LibraryRef) -> LibrarySource:
        """The source for ``ref``. Raises [[LibraryResolutionError]] when it cannot be found."""

    def closure(self, root: LibrarySource) -> List[LibrarySource]:
        """The TRANSITIVE ``include`` closure of ``root``, EXCLUDING ``root`` itself, in a deterministic
        order (breadth-first by include order). The default walks [[includes_of]] and re-enters
        :meth:`resolve` — a provider whose backing store can answer a whole closure in one round trip may
        override it. Cycle-safe (published content does contain mutual includes)."""
        out: List[LibrarySource] = []
        seen = {root.name}
        queue = list(includes_of(root))
        while queue:
            ref = queue.pop(0)
            if ref.name in seen:
                continue
            seen.add(ref.name)
            src = self.resolve(ref)
            out.append(src)
            queue.extend(includes_of(src))
        return out

    def search(self, pattern: Optional[str] = None) -> List[LibraryRef]:
        """Every library this provider can offer, optionally narrowed by a case-insensitive substring or
        glob ``pattern``. Optional capability — discovery is not required to compile."""
        raise NotImplementedError(f"{type(self).__name__} does not support search()")


# --------------------------------------------------------------------------------------------------
# Parsing: library identity + include edges, from CQL text or ELM JSON.
# --------------------------------------------------------------------------------------------------

#: ``library <Name> version '<v>'`` — the CQL header.
_CQL_HEADER = re.compile(r"""(?m)^\s*library\s+"?([^\s"']+)"?(?:\s+version\s+'([^']*)')?""")

#: ``include <Name> [version '<v>'] [called <Alias>]`` — one CQL include edge.
_CQL_INCLUDE = re.compile(r"""(?m)^\s*include\s+"?([^\s"']+)"?(?:\s+version\s+'([^']*)')?""")


def identity_of(text: str, *, elm: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """The ``(name, version)`` a library DECLARES, read from its ELM identifier or its CQL header. The
    declared identity wins over the filename — published content is not always named after its library."""
    if elm:
        try:
            ident = json.loads(text).get("library", {}).get("identifier", {})
        except (ValueError, AttributeError):
            return (None, None)
        return (ident.get("id"), ident.get("version"))
    m = _CQL_HEADER.search(text)
    return (m.group(1), m.group(2)) if m else (None, None)


def includes_of(src: LibrarySource) -> List[LibraryRef]:
    """The libraries ``src`` ``include``s, in declaration order, de-duplicated by name.

    Read EXACTLY from ELM (``library.includes.def[]``, where ``path`` is the library id) when ELM is
    present, else by regex over the CQL text. The CQL path is a text scan rather than a parse because the
    client has no translator."""
    if src.elm:
        try:
            defs = json.loads(src.elm).get("library", {}).get("includes", {}).get("def", [])
        except (ValueError, AttributeError):
            defs = []
        refs = [LibraryRef(d.get("path") or d.get("localIdentifier"), d.get("version"))
                for d in defs if d.get("path") or d.get("localIdentifier")]
    elif src.cql:
        refs = [LibraryRef(m.group(1), m.group(2)) for m in _CQL_INCLUDE.finditer(_strip_comments(src.cql))]
    else:
        refs = []
    seen, out = set(), []
    for r in refs:
        if r.name not in seen:
            seen.add(r.name)
            out.append(r)
    return out


def _strip_comments(cql: str) -> str:
    """Blank out CQL comments so a commented-out ``include`` is not walked. Block comments are replaced
    by newlines rather than deleted, so the line-anchored include regex still sees real line starts."""
    cql = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), cql, flags=re.S)
    return re.sub(r"(?m)//.*$", "", cql)


# --------------------------------------------------------------------------------------------------
# FileLibraryProvider — a search PATH of roots, with pluggable layouts.
# --------------------------------------------------------------------------------------------------

#: The glob patterns (relative to a search root) each named LAYOUT scans (`BUILD-LIBRARY-PROVIDER`).
#: Layouts are ADDITIVE — a provider scans the union of its selected layouts, so a repo that is half IG
#: and half loose files resolves without picking a side.
#:
#: These follow the conventions published CQL content actually uses: ``flat`` is the cql-to-elm
#: ``DefaultLibrarySourceProvider`` file convention (``<Name>-<version>.cql`` then ``<Name>.cql``); ``ig``
#: is the FHIR IG-publisher / sushi source tree; ``fhir-library`` is the ``Library`` RESOURCE form
#: (base64 ``content[]``) that IG output and measure bundles ship; ``nested`` is the per-measure directory
#: layout (this repo's own ``cql/measures/<measure>/`` corpus, and how most content repos group a
#: measure with its dependency copies).
LAYOUTS: Dict[str, Tuple[str, ...]] = {
    "flat": ("*.cql", "*.elm.json"),
    "ig": ("input/cql/**/*.cql", "input/cql/**/*.elm.json"),
    "fhir-library": ("input/resources/library/Library-*.json", "resources/library/Library-*.json",
                     "Library-*.json"),
    "nested": ("*/*.cql", "*/*.elm.json"),
}

#: The layouts scanned when the caller names none — the union that covers every convention above without
#: a deep recursive walk of an arbitrary tree.
DEFAULT_LAYOUTS: Tuple[str, ...] = ("flat", "ig", "fhir-library", "nested")


class FileLibraryProvider(LibraryProvider):
    """A [[LibraryProvider]] over a filesystem search PATH (`BUILD-LIBRARY-PROVIDER`).

    ``search_path`` is an ordered list of ROOTS — earlier roots win, so a local override directory can
    shadow a vendored content repo. A root is any directory: a checkout of published eCQM content, an
    unpacked measure package, or a Unity Catalog Volume (``/Volumes/...`` is an ordinary path, so this
    class covers the Databricks case with no platform code).

    ``layouts`` selects which of [[LAYOUTS]] to scan; ``globs`` adds arbitrary extra patterns for a tree
    that matches no convention (the escape hatch — a layout list should never be a reason this provider
    cannot see a file).

    Scanning is LAZY and cached per root: the index is built on first use, so constructing a provider over
    a large content repo costs nothing until something is resolved."""

    def __init__(self, search_path: Optional[Iterable[str]] = None, *,
                 layouts: Iterable[str] = DEFAULT_LAYOUTS,
                 globs: Iterable[str] = (),
                 follow_symlinks: bool = False):
        roots = [Path(p).expanduser() for p in (search_path or ())]
        if not roots:
            raise ValueError("FileLibraryProvider requires at least one search-path root")
        unknown = set(layouts) - set(LAYOUTS)
        if unknown:
            raise ValueError(f"unknown layout(s) {sorted(unknown)}; known: {sorted(LAYOUTS)}")
        self._roots = roots
        self._patterns: Tuple[str, ...] = tuple(
            p for name in layouts for p in LAYOUTS[name]) + tuple(globs)
        self._follow_symlinks = follow_symlinks
        self._index: Optional[List[LibrarySource]] = None   # scan order == search-path precedence

    # -- indexing --------------------------------------------------------------------------------
    def _scan(self) -> List[LibrarySource]:
        if self._index is None:
            out: List[LibrarySource] = []
            seen_paths = set()
            for root in self._roots:
                if not root.is_dir():
                    continue
                for pattern in self._patterns:
                    for path in sorted(root.glob(pattern)):
                        real = path.resolve()
                        if real in seen_paths or not path.is_file():
                            continue
                        if path.is_symlink() and not self._follow_symlinks:
                            continue
                        seen_paths.add(real)
                        src = _read_source(path)
                        if src is not None:
                            out.append(src)
            self._index = out
        return self._index

    def _candidates(self, ref: LibraryRef, prefer_dir: Optional[Path]) -> List[LibrarySource]:
        """Sources declaring ``ref.name``, SIBLING-FIRST (see the module docstring's scoping rule): every
        source living in ``prefer_dir`` comes before any other, so a measure directory's own copy of a
        shared library always wins over another measure's copy."""
        named = [s for s in self._scan() if s.name == ref.name]
        if prefer_dir is None:
            return named
        prefer = prefer_dir.resolve()
        siblings = [s for s in named if s.origin and Path(s.origin).resolve().parent == prefer]
        return siblings + [s for s in named if s not in siblings]

    # -- LibraryProvider -------------------------------------------------------------------------
    def resolve(self, ref: LibraryRef, *, prefer_dir: Optional[Path] = None) -> LibrarySource:
        """The source for ``ref``: the exact ``(name, version)`` match when ``ref`` pins a version, else the first
        by-NAME match (the exact-version rule). ``prefer_dir`` scopes the lookup to a closure's own directory
        first."""
        candidates = self._candidates(ref, prefer_dir)
        if not candidates:
            raise LibraryResolutionError(
                f"cannot resolve CQL library '{ref}' on the search path "
                f"[{', '.join(str(r) for r in self._roots)}] (layouts scanned: "
                f"{', '.join(self._patterns)}). Add its directory to the search path, or widen `globs=`.")
        if ref.version:
            exact = next((s for s in candidates if s.version == ref.version), None)
            if exact is None:
                raise LibraryResolutionError(
                    f"cannot resolve CQL library '{ref}': the search path holds "
                    f"{', '.join(sorted({str(s.version) for s in candidates}))}, not {ref.version}.")
            return exact
        return candidates[0]

    def closure(self, root: LibrarySource) -> List[LibrarySource]:
        """The transitive closure of ``root``, resolved SIBLING-FIRST against ``root``'s own directory.

        Overrides the base walk only to thread ``prefer_dir`` through the recursion — the traversal itself
        (breadth-first, cycle-safe) is the same contract."""
        prefer_dir = Path(root.origin).parent if root.origin else None
        out: List[LibrarySource] = []
        seen = {root.name}
        queue = list(includes_of(root))
        while queue:
            ref = queue.pop(0)
            if ref.name in seen:
                continue
            seen.add(ref.name)
            try:
                src = self.resolve(ref, prefer_dir=prefer_dir)
            except LibraryResolutionError as e:
                raise LibraryResolutionError(
                    f"{e}\n  (required by the include closure of '{root.name}'"
                    + (f" at {root.origin}" if root.origin else "") + ")") from e
            out.append(src)
            queue.extend(includes_of(src))
        return out

    def search(self, pattern: Optional[str] = None) -> List[LibraryRef]:
        """Every library on the search path, optionally narrowed by a case-insensitive substring match or
        a ``*``/``?`` glob over the library NAME. De-duplicated by ``(name, version)``, in scan order."""
        refs, seen = [], set()
        for s in self._scan():
            if pattern and not _name_matches(s.name, pattern):
                continue
            key = (s.name, s.version)
            if key not in seen:
                seen.add(key)
                refs.append(s.ref)
        return refs


def _name_matches(name: str, pattern: str) -> bool:
    from fnmatch import fnmatch
    if any(c in pattern for c in "*?["):
        return fnmatch(name.lower(), pattern.lower())
    return pattern.lower() in name.lower()


def _read_source(path: Path) -> Optional[LibrarySource]:
    """Read one file into a [[LibrarySource]], or ``None`` when it is not a CQL library at all (an
    unrelated JSON in a scanned directory). Never raises on malformed content — an unreadable file is
    simply not indexed, and the loud failure comes later from [[FileLibraryProvider.resolve]] naming the
    library that could not be found."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.strip():
        # An EMPTY file is not a library. Without this, `LibrarySource.__post_init__` raises for a
        # zero-byte `.cql` and the exception escapes `_scan` — so one stray empty file on the search path
        # would break every resolve/search/closure, not just its own entry.
        return None
    suffix = path.name.lower()
    if suffix.endswith(".cql"):
        name, version = identity_of(text)
        # Fall back to the FILE name (minus any `-<version>` tail) for a library with no parsable header.
        name = name or re.sub(r"-\d[\w.]*$", "", path.stem)
        return LibrarySource(name=name, version=version, cql=text, origin=str(path))
    if suffix.endswith(".elm.json"):
        name, version = identity_of(text, elm=True)
        return LibrarySource(name=name or path.name[:-len(".elm.json")], version=version,
                             elm=text, origin=str(path)) if name or path.name else None
    if suffix.endswith(".json"):
        return _read_fhir_library(text, path)
    return None


def _read_fhir_library(text: str, path: Path) -> Optional[LibrarySource]:
    """A FHIR ``Library`` RESOURCE: identity from ``name``/``version``, body from the base64 ``content[]``
    attachments (``text/cql`` and/or ``application/elm+json``). This is the form IG output and measure
    bundles ship, so it is how most published content is consumed outside a source checkout."""
    try:
        res = json.loads(text)
    except ValueError:
        return None
    if not isinstance(res, dict) or res.get("resourceType") != "Library":
        return None
    cql = elm = None
    for att in res.get("content", []) or []:
        data = att.get("data")
        if not data:
            continue
        try:
            body = base64.b64decode(data).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        ctype = (att.get("contentType") or "").lower()
        if "cql" in ctype:
            cql = body
        elif "elm" in ctype and "json" in ctype:
            elm = body
    if not (cql or elm):
        return None
    name = res.get("name") or (identity_of(cql or "")[0] if cql else None)
    return LibrarySource(name=name or path.stem, version=res.get("version"),
                         cql=cql, elm=elm, origin=str(path)) if name or cql or elm else None


# --------------------------------------------------------------------------------------------------
# Resolution: config + entry-point plugins (the `BUILD-RESOLVE` precedence).
# --------------------------------------------------------------------------------------------------

def resolve_library_provider(conf: Mapping[str, str], *,
                             search_path: Optional[Iterable[str]] = None,
                             library_source: Optional[str] = None) -> Optional[LibraryProvider]:
    """Build the session's library provider by the `BUILD-RESOLVE` precedence, or ``None`` when no source
    is configured (a client that only ever binds inline CQL needs none — absence is not an error here;
    the loud failure comes when something actually asks for a library by name).

    Kind selection: ``spark.cqldatabricks.libraryProvider.kind`` / ``CQL_DATABRICKS_LIBRARY_PROVIDER`` — ``file``
    (the built-in default) or a PLUGIN registered under the ``cql_databricks.libraries`` entry-point
    group. The file provider's search path is ``search_path`` explicit > ``spark.cqldatabricks.libraryPath``
    (``os.pathsep``-separated) > ``CQL_DATABRICKS_LIBRARY_PATH`` > the single legacy ``library_source`` root —
    so an existing `BUILD-CLIENT-SESSION` ``.library_source(path)` config keeps working unchanged, as one
    entry of a one-element path."""
    kind = (config.resolve(conf, config.CONF_LIBRARY_PROVIDER_KIND, config.ENV_LIBRARY_PROVIDER,
                           "file") or "file").lower()
    if kind != "file":
        factory = _load_library_plugin(kind)
        if factory is None:
            raise ValueError(
                f"unknown library provider kind '{kind}' — no built-in and no plugin registered under "
                f"'{LIBRARY_PLUGIN_GROUP}' with that name")
        return factory(conf)

    roots = list(search_path or ())
    if not roots:
        raw = config.resolve_setting("library path", conf=conf, conf_key=config.CONF_LIBRARY_PATH,
                                     env=config.ENV_LIBRARY_PATH)
        if raw:
            roots = [p for p in raw.split(os.pathsep) if p]
    if not roots:
        src = config.resolve_setting("library source", explicit=library_source, conf=conf,
                                     conf_key=config.CONF_LIBRARY_SOURCE, env=config.ENV_LIBRARY_SOURCE)
        roots = [src] if src else []
    if not roots:
        return None

    layouts = config.resolve(conf, config.CONF_LIBRARY_LAYOUT, config.ENV_LIBRARY_LAYOUT)
    return FileLibraryProvider(
        roots, layouts=tuple(l.strip() for l in layouts.split(",") if l.strip()) if layouts
        else DEFAULT_LAYOUTS)


def _load_library_plugin(name: str):
    """The registered factory ``f(conf) -> LibraryProvider`` for ``name``, or ``None``. Standard Python
    packaging (an entry point in the ``cql_databricks.libraries`` group) — no import-time coupling."""
    from importlib import metadata
    try:
        eps = metadata.entry_points(group=LIBRARY_PLUGIN_GROUP)
    except TypeError:  # pragma: no cover - very old importlib.metadata
        eps = metadata.entry_points().get(LIBRARY_PLUGIN_GROUP, [])
    for ep in eps:
        if ep.name == name:
            return ep.load()
    return None
