"""RUN-CLIENT-RESULT-SETS — the closure a derivation reads, and the pinned relation each define resolves to.

Every derivation (the metric set, one define's profile, the gate flow, the score and the reads) asks the same
question of its closure: which library holds a define, and which relation holds its result. All of them ask
through :func:`locate`, so no two derivations can resolve one define to two different relations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..results import PinnedRelation


class MetricsUnavailable(RuntimeError):
    """Raised when no evaluation exists to read (CORE-NO-SILENT-MISLOWER).

    An empty metric set and a measure that was never run are different states, and a document showing
    absent figures for the second reason should say so rather than look like a run that found nothing.
    """


@dataclass
class SelectedLibrary:
    """RUN-CLIENT-RESULT-SETS — one library of a complete selection: its identity, and the draft label the root
    carries (a presentation label, never a storage address)."""
    library: str
    version: str
    draft: str


@dataclass
class ClosureLibrary:
    """One library of a complete selection's closure: its identity, and the pinned relation of each define it
    holds. A define the selection did not compute has no entry."""

    scope: SelectedLibrary
    relations: dict[str, PinnedRelation]
    defines: list = field(default_factory=list)

    def relation(self, name: str) -> PinnedRelation:
        return self.relations[name]


class ClosureSource(Protocol):
    """Where a derivation's closure is (DESIGNER-RUNS-SDP): ``{library_name: ClosureLibrary}`` for the complete
    selection ``eval_id`` of ``root`` (its ``draft``, or ``""``) on ``data_source_id``.

    ``selection_reading.SelectionClosure`` is the implementation: it adapts the compiler's metadata of one complete
    selection (RUN-CLIENT-RESULT-SETS). Every derivation reads its closure through this one shape.
    """

    def closure(self, data_source_id: str, eval_id: str, *, root: str, draft: str) -> dict[str, ClosureLibrary]: ...


def locate(libraries: dict[str, ClosureLibrary], define_library: str, name: str) -> PinnedRelation | None:
    """The pinned relation holding one define, or ``None`` if the selection has none for it.

    The name is the one the compiler recorded in the selection's metadata, matched exactly: no normalization and
    no library prefix is added.
    """
    entry = libraries.get(define_library)
    if entry is None:
        return None
    return entry.relations.get(name)
