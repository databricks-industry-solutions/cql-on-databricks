"""DESIGNER-RUNS-SDP, PIPE-PRE-COMPUTED — reading one evaluation's results from the tables an SDP pipeline wrote, as the
caller.

A pipeline records nothing beside its tables (`DEC-STATELESS-SERVICES`), so what a reader needs is read off the
catalog: which defines a library's scope has a table for (``information_schema.tables``, one read for the whole
closure), and whether each scope holds rows for the evaluation set (one probe per scope, one statement). A scope with
no rows for ``__eval_id`` is a library that was not run for it, which is not the same as a run that counted zero
(`CORE-NO-SILENT-MISLOWER`).

:class:`CatalogClosure` is the derivations' :class:`~.insights.evaluation.ClosureSource` over the catalog;
:func:`result_counts` is ``GET /api/results``'s payload. Every read goes through the caller's ``execute``.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .address import EVAL_ID_COLUMN, ResultScope
from .insights.evaluation import ClosureLibrary
from .relation import literal, quote
from .results import Execute, count_relations, relation_columns


@dataclass(frozen=True)
class TableDefine:
    """One define a scope has a table for. ``read`` is a materialized scan's descriptor, which only the evaluator's
    state recorded; a pipeline's plan materializes none under the default ``materialize.retrieves = none``."""
    mv_name: str
    read: dict | None = None


def scope_tables(execute: Execute, results_catalog: str, scopes: Iterable[ResultScope]) -> dict[ResultScope, list[str]]:
    """Each scope's defines (by normalized name) that have a table, in ONE ``information_schema`` read. A draft shares
    the drafts schema with every other draft, so it keeps only the tables carrying its key (``define_of``)."""
    scopes = list(dict.fromkeys(scopes))
    if not scopes:
        return {}
    schemas = sorted({s.schema_name() for s in scopes})
    rows = execute(f"SELECT table_schema, table_name FROM {results_catalog}.information_schema.tables "
                   f"WHERE table_schema IN ({', '.join(literal(s) for s in schemas)}) "
                   f"ORDER BY table_schema, table_name") or []
    out: dict[ResultScope, list[str]] = {s: [] for s in scopes}
    for schema, table in ((r[0], r[1]) for r in rows):
        for scope in scopes:
            if scope.schema_name() == schema:
                define = scope.define_of(table)
                if define:
                    out[scope].append(define)
    return out


def scopes_with_rows(execute: Execute, results_catalog: str, tables: dict[ResultScope, list[str]],
                     eval_id: str) -> set[ResultScope]:
    """The scopes holding rows for ``eval_id``: one probe of one table per scope, under one ``UNION ALL``. Every table a
    pipeline writes is one REPLACE WHERE flow over the same open periods, so one table answers for its scope."""
    probed = [(i, scope, names[0]) for i, (scope, names) in enumerate(tables.items()) if names]
    if not probed:
        return set()
    branches = [f"SELECT {i} AS k, count(*) AS n FROM (SELECT 1 FROM {scope.schema(results_catalog)}."
                f"{quote(scope.table_name(name))} WHERE {EVAL_ID_COLUMN} = {literal(eval_id)} LIMIT 1)"
                for i, scope, name in probed]
    rows = execute("\nUNION ALL\n".join(branches)) or []
    present = {int(r[0]) for r in rows if int(r[1] or 0) > 0}
    return {scope for i, scope, _ in probed if i in present}


class CatalogClosure:
    """:class:`~.insights.evaluation.ClosureSource` over the result tables: ``scopes`` is ``{library: ResultScope}`` for
    the root (its draft, or its published version) and each include (its published version, `DEC-DRAFT-ADDRESS`). A
    library with no rows for the evaluation set is left out, as the evaluator's state layer leaves out a library it
    never compiled for it."""

    def __init__(self, execute: Execute, results_catalog: str, scopes: dict[str, ResultScope]) -> None:
        self._execute = execute
        self._catalog = results_catalog
        self._scopes = dict(scopes)

    def closure(self, data_source_id: str, eval_id: str, *, root: str, draft: str) -> dict[str, ClosureLibrary]:
        if root not in self._scopes or self._scopes[root].draft != draft:
            raise ValueError(f"CatalogClosure was built for {self._scopes.get(root)}, not {root} draft {draft!r}")
        tables = scope_tables(self._execute, self._catalog, self._scopes.values())
        ran = scopes_with_rows(self._execute, self._catalog, tables, eval_id)
        return {lib: ClosureLibrary(scope=scope, defines=[TableDefine(name) for name in tables[scope]])
                for lib, scope in self._scopes.items() if scope in ran}


def result_counts(execute: Execute, results_catalog: str, scope: ResultScope, eval_id: str) -> dict:
    """PIPE-PRE-COMPUTED — ``GET /api/results``'s payload for one scope and evaluation set: each define's subjects and
    elements (`SPEC-METRIC`), and whether the scope holds rows for the set at all (``computed``)."""
    tables = scope_tables(execute, results_catalog, [scope])
    names = tables.get(scope, [])
    computed = bool(names) and scope in scopes_with_rows(execute, results_catalog, tables, eval_id)
    defines = []
    if computed:
        refs = {name: scope.relation(results_catalog, name) for name in names}
        counts = count_relations(execute, relation_columns(results_catalog, list(refs.values()), execute=execute),
                                 eval_id=eval_id)
        for name in names:
            subjects, elements = counts.get(refs[name], (0, None))
            defines.append({"name": name, "define": name, "subjects": subjects, "elements": elements,
                            "status": "fresh", "computed": True})
    else:
        defines = [{"name": n, "define": n, "subjects": 0, "elements": None, "status": "unknown", "computed": False}
                   for n in names]
    return {"schema": scope.schema(results_catalog), "libraryName": scope.library, "version": scope.version,
            "versionKind": "draft" if scope.draft else "published", "defines": defines}
