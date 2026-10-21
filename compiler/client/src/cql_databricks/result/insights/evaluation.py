"""PIPE-PER-LIBRARY-SCHEMA / PIPE-STATE-LAYER — locating ONE evaluation's result relations.

Two readers ask the same question of the state layer: the measure-wide metric set (``metrics.py``) and
one define's data profile (``profile.py``). Both must resolve ``(data source, measurement period)`` to
the schemas that evaluation wrote, and both must map a define to its relation through what the compiler
RECORDED rather than by re-deriving a table name. Two copies of that resolution would let the profile
read a different evaluation than the figures rendered above it on the same page — the
two-surfaces-disagreeing failure ``APP-SPEC-MODEL`` exists to prevent — so it lives here once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from ..address import ResultScope, normalize as _normalize
from ..results import RelationRef

#: A draft's state rows carry its key before the evaluation set's id: ``{version}_d{key}_{eval_id}``
#: (`orchestrator.deploy_closure`). A published version never ends ``_d`` plus 16 hex digits.
_DRAFT_VERSION = re.compile(r"^(?P<version>.+)_d(?P<key>[0-9a-f]{16})$")


class MetricsUnavailable(RuntimeError):
    """Raised when no evaluation exists to read (CORE-NO-SILENT-MISLOWER).

    An empty metric set and a measure that was never run are different states, and a document showing
    absent figures for the second reason should say so rather than look like a run that found nothing.
    """


@dataclass
class SelectedLibrary:
    """RUN-CLIENT-RESULT-SETS — presentation identity, with no legacy table-address operations."""
    library: str
    version: str
    draft: str


@dataclass
class ClosureLibrary:
    """One library of an evaluation's closure: where its rows are, and what the compiler recorded for it."""

    scope: ResultScope | SelectedLibrary
    defines: list = field(default_factory=list)
    relations: dict[str, RelationRef] | None = None

    def relation(self, results_catalog: str, name: str) -> RelationRef:
        """RUN-CLIENT-RESULT-SETS — explicit selected references never fall through to legacy naming."""
        if self.relations is not None:
            return self.relations[name]
        if not isinstance(self.scope, ResultScope):
            raise ValueError("selected library requires explicit pinned relations")
        return self.scope.relation(results_catalog, name)

    @property
    def mv_names(self) -> set[str]:
        return {d.mv_name for d in self.defines}


def closure_libraries(state, data_source_id: str, eval_id: str, *, root: str,
                      draft: str) -> dict[str, ClosureLibrary]:
    """``{library_name: ClosureLibrary}`` for one evaluation's whole include closure.

    Keyed by the EVALUATION SET rather than by a version the caller supplies, because the caller only
    knows the ROOT measure's version — a dependency's is its own (a helper library may be at
    4.19.000 under a 1.0.000 root). Every library compiled for one evaluation set ends its state version with the set's
    ``__eval_id`` (`RUN-RESULTS-BY-EVAL`), so the id identifies the closure.

    ``root`` and ``draft`` say which version of the root is read (`RUN-ADDRESS-BY-LIBRARY`): with a
    ``draft`` key, the root's rows are that draft's and the published version's are not read; without one,
    no draft's are. A draft's includes are the published versions, so they read the same either way.
    """
    libraries: dict[str, ClosureLibrary] = {}
    for row in state.list_libraries(data_source_id):
        if not row.library_version.endswith(f"_{eval_id}"):
            continue
        head = row.library_version[:-len(eval_id) - 1]
        match = _DRAFT_VERSION.match(head)
        key = match.group("key") if match else ""
        version = match.group("version") if match else head
        if key and (row.library_name != root or key != draft):
            continue  # another draft, or a draft of an include, which a run never writes
        if not key and draft and row.library_name == root:
            continue  # the published root, when the draft is asked for
        libraries[row.library_name] = ClosureLibrary(
            scope=ResultScope(row.library_name, version, draft=key), defines=list(state.list_defines(row.id)))
    return libraries


class ClosureSource(Protocol):
    """Where one evaluation's closure is (DESIGNER-RUNS-SDP): ``{library_name: ClosureLibrary}`` for the evaluation set
    ``eval_id`` of ``root`` (its ``draft``, or the published version when ``draft`` is ``""``) on ``data_source_id``.

    ``selection_reading.SelectionClosure`` supplies exact pinned references from one complete selection
    (RUN-CLIENT-RESULT-SETS). Legacy :class:`StateClosure` and ``reading.CatalogClosure`` remain for unmigrated
    callers. Every derivation uses the same algorithms over the explicitly selected source."""

    def closure(self, data_source_id: str, eval_id: str, *, root: str, draft: str) -> dict[str, ClosureLibrary]: ...


class StateClosure:
    """:class:`ClosureSource` over the evaluator service's state layer (:func:`closure_libraries`)."""

    def __init__(self, state) -> None:
        self._state = state

    def closure(self, data_source_id: str, eval_id: str, *, root: str, draft: str) -> dict[str, ClosureLibrary]:
        return closure_libraries(self._state, data_source_id, eval_id, root=root, draft=draft)


def locate(libraries: dict[str, ClosureLibrary], define_library: str, name: str,
           results_catalog: str) -> RelationRef | None:
    """The relation holding one define, or ``None`` if this evaluation never materialized it.

    Two candidate table names are tried against what the state layer RECORDED, never invented: the
    define's own normalized name (how a root measure's defines are recorded) and its library-prefixed
    form (how a dependency's are). Both are checked against ``pipeline_defines.mv_name``, so a name that
    matches neither means the define has no relation here rather than a table nobody has looked for.
    """
    entry = libraries.get(define_library)
    if entry is None:
        return None
    if entry.relations is not None:
        return entry.relations.get(name)
    mv_names = entry.mv_names
    for candidate in (_normalize(name), _normalize(f"{define_library}_{name}")):
        if candidate in mv_names:
            return entry.relation(results_catalog, candidate)
    return None
