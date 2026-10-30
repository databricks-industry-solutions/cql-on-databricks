"""The results address: its ONE derivation (`RUN-ADDRESS-BY-LIBRARY`, `PIPE-PER-LIBRARY-SCHEMA`).

    published   {catalog}.{library}_{version}.{define}            rows keyed (__eval_id, __subject_id)
    draft       {catalog}.cql__drafts.d{key}_{define}              the same rows, the same columns

A published library version owns its results (`DEC-LIBRARY-OWNS-RESULTS`): one schema per library
VERSION, one table per define, every evaluation set (data source, terminology, period, other parameters)
as rows keyed by ``__eval_id`` (`RUN-RESULTS-BY-EVAL`). Callers asking for the same thing share rows,
computed once (`DEC-ADDRESS-NO-DEPLOYMENT`); there is no deployment term any more.

CQL that is not a published version (an edited library, or anonymous CQL) cannot write there: its defines
can have other columns. It goes to ONE common schema, :data:`DRAFT_SCHEMA`, its tables named by the
content of the CQL that produced them, :func:`draft_key` of the root ELM, plus the define
(`DEC-DRAFT-ADDRESS`). Identical CQL from anyone lands on the same tables. A published schema name never
contains ``__`` (:func:`normalize` collapses runs of ``_``), so no library can be named into the drafts
schema.

**Which of the two a scope is, is a required argument everywhere, with no default** (:class:`ResultScope`).
The 2026-08-08 lesson of `RUN-SCHEMA-NAME` is why: an optional address component is a second address that
one caller opts into and the others silently do not, and a read of an address nothing wrote looks exactly
like a measure that was never run.

It lives in the evaluator's client because the address is the evaluator's and the client reads results
the evaluator refers it to. No caller builds one: the evaluator returns references (`DEC-EVALUATOR-OPAQUE`).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Sequence

#: RUN-RESULTS-BY-EVAL — the column every result table keys an evaluation set's rows by. The compiler
#: reads it to scope a sibling read (`PIPE-EVAL-SCOPED-READ`, ``SystemColumns.EvalId``).
EVAL_ID_COLUMN = "__eval_id"
#: RUN-ADDRESS-BY-LIBRARY, `DEC-DRAFT-ADDRESS` — the one schema every draft's results go to. Its ``__`` is a
#: spelling :func:`normalize` never produces, so no published library version can share it.
DRAFT_SCHEMA = "cql__drafts"
#: The hex digits of the root ELM's SHA-256 that name a draft's tables: 64 bits, so two drafts alive in one
#: retention window do not share a table.
DRAFT_KEY_LENGTH = 16

_CLEAN = re.compile(r"[^a-z0-9]+")
_COLLAPSE = re.compile(r"_+")


def normalize(name: str) -> str:
    """Lowercase, replace non-alphanumerics with ``_``, collapse runs, strip the edges.

    >>> normalize("BP2Control Adult-Screen")
    'bp2control_adult_screen'
    """
    return _COLLAPSE.sub("_", _CLEAN.sub("_", name.lower())).strip("_")


def evaluation_version(data_source_id: str, *,
                       period_start: str | None = None,
                       period_end: str | None = None,
                       parameters: dict[str, str] | None = None,
                       terminology: Sequence[str] = ()) -> str:
    """The evaluation hash (`PIPE-STATE-LAYER`): data source, terminology binding, measurement period and
    any other compile-time parameters, NOT the ELM. Same parameters, same suffix, whatever the CQL says.

    The hash takes its terms as given. Making them canonical (a period in its declared type's form,
    `DEC-EVAL-ID-TERMS`) is the evaluator's ``evaluation_key``, the one caller writers and readers share.

    >>> len(evaluation_version("ds-1", period_start="2026-01-01", period_end="2026-12-31"))
    8
    """
    h = hashlib.sha256(data_source_id.encode("utf-8"))
    if period_start:
        h.update(f"|ps={period_start}".encode("utf-8"))
    if period_end:
        h.update(f"|pe={period_end}".encode("utf-8"))
    if parameters:
        h.update(f"|p={json.dumps(parameters, sort_keys=True)}".encode("utf-8"))
    if terminology:
        # RUN-EVAL-ID-INPUTS: the value sets the answer was computed against. Absent, the hash is unchanged.
        h.update(f"|t={json.dumps(sorted(terminology))}".encode("utf-8"))
    return h.hexdigest()[:8]


def eval_id(data_source_id: str, *, period_start: str | None = None, period_end: str | None = None,
            parameters: dict[str, str] | None = None, terminology: Sequence[str] = ()) -> str:
    """The ``__eval_id`` of an evaluation set (`DEC-EVAL-ID-LOOKUP`): :func:`evaluation_version` of its
    fields. Deterministic, so every writer derives the same id with no lookup, in every workspace."""
    return evaluation_version(data_source_id, period_start=period_start, period_end=period_end,
                              parameters=parameters, terminology=terminology)


def full_evaluation_version(base_version: str, data_source_id: str, *,
                            period_start: str | None = None,
                            period_end: str | None = None,
                            parameters: dict[str, str] | None = None) -> str:
    """``{base_version}_{evaluation_version}`` (`PIPE-STATE-LAYER`): the evaluator's INTERNAL key for a
    library's compiled state in one evaluation. Not part of the results address since `RUN-RESULTS-BY-EVAL`.

    >>> full_evaluation_version("1.2", "ds-1")[:4]
    '1.2_'
    >>> len(full_evaluation_version("1.2", "ds-1"))
    12
    """
    suffix = evaluation_version(data_source_id, period_start=period_start, period_end=period_end,
                                parameters=parameters)
    return f"{base_version}_{suffix}"


def draft_key(root_elm_hash: str) -> str:
    """The key a draft's tables are named by: the head of its root ELM's SHA-256 (`DEC-DRAFT-ADDRESS`).

    The root ELM only: a draft's includes come from the published corpus by name and version, exactly as a
    published library's do, so they are part of neither address. A change upstream of a define moves its
    claim (`RUN-FINGERPRINT`), not its address.

    >>> draft_key("0123456789abcdef" * 4)
    '0123456789abcdef'
    """
    key = root_elm_hash.strip().lower()
    if len(key) < DRAFT_KEY_LENGTH or not re.fullmatch(r"[0-9a-f]+", key):
        raise ValueError(f"draft key needs a hex SHA-256, got {root_elm_hash!r} (RUN-ADDRESS-BY-LIBRARY)")
    return key[:DRAFT_KEY_LENGTH]


@dataclass(frozen=True)
class ResultScope:
    """Where one library's results are: a published version's own schema, or the drafts schema under a key.

    ``draft`` is ``""`` for a published version and the :func:`draft_key` otherwise. It has no default
    (see the module docstring).

    >>> ResultScope("BP2", "1.2", draft="").table("c", "denominator")
    'c.bp2_1_2.denominator'
    >>> ResultScope("BP2", "1.2", draft="0123456789abcdef").table("c", "denominator")
    'c.cql__drafts.d0123456789abcdef_denominator'
    """

    library: str
    version: str
    draft: str

    def __post_init__(self) -> None:
        if self.draft:
            if not re.fullmatch(rf"[0-9a-f]{{{DRAFT_KEY_LENGTH}}}", self.draft):
                raise ValueError(f"{self.draft!r} is not a draft key (RUN-ADDRESS-BY-LIBRARY)")
        elif not (normalize(self.library) and normalize(self.version)):
            raise ValueError(f"a published result scope needs a library and a version, got "
                             f"{self.library!r} {self.version!r}; CQL with neither is a draft (DEC-DRAFT-ADDRESS)")

    def schema_name(self) -> str:
        """The schema, without the catalog.

        >>> ResultScope("FHIRHelpers", "4.0.1", draft="").schema_name()
        'fhirhelpers_4_0_1'
        """
        if self.draft:
            return DRAFT_SCHEMA
        return f"{normalize(self.library)}_{normalize(self.version)}"

    def schema(self, catalog: str) -> str:
        return f"{catalog}.{self.schema_name()}"

    @property
    def table_prefix(self) -> str:
        """What every table of this scope's name starts with inside its schema: ``d{key}_`` for a draft."""
        return f"d{self.draft}_" if self.draft else ""

    def table_name(self, name: str) -> str:
        """The table one define (by its normalized name) writes, inside :meth:`schema_name`."""
        return f"{self.table_prefix}{name}"

    def define_of(self, table_name: str) -> str | None:
        """The inverse of :meth:`table_name`: the define's name, or ``None`` for another scope's table."""
        if not table_name.startswith(self.table_prefix):
            return None
        return table_name[len(self.table_prefix):] or None

    def relation(self, catalog: str, name: str) -> tuple[str, str]:
        """``(catalog.schema, table)``, the shape the evaluator's readers key relations by."""
        return (self.schema(catalog), self.table_name(name))

    def table(self, catalog: str, name: str) -> str:
        """The fully qualified table one define writes — **the single derivation of that address.**

        Four places must agree on this string exactly, and twice they silently did not: a barrier's own
        write target, how a barrier in another library names it in a read (`PIPE-CROSS-SCHEMA-QUALIFY`),
        how the executor resolves that read to the barrier that builds it, and the reference a reader is
        handed (`DEC-EVALUATOR-OPAQUE`).
        """
        return f"{self.schema(catalog)}.{self.table_name(name)}"


def substitute_names(sql: str, mapping: dict[str, str]) -> str:
    """Replace the compiler's dataset names in ``sql`` with their addresses (`PIPE-CROSS-SCHEMA-QUALIFY`).

    The compiler emits names that are correct in its own address space; this relocates them into the
    results address. Longest name first, so a name that is a prefix of another is not replaced inside
    it: substituting ``result_x`` before ``result_x_y`` would corrupt the latter.
    """
    if not sql:
        return sql
    for old_name in sorted(mapping, key=len, reverse=True):
        sql = sql.replace(old_name, mapping[old_name])
    return sql
