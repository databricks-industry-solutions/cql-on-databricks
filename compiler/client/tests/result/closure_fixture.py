"""RUN-CLIENT-RESULT-SETS — a synthetic closure in the shape ``selection_reading.SelectionClosure`` produces.

The insights tests describe a measure (ExampleMeasure and its include closure) as ONE complete selection: every
define is a pinned relation whose recorded schema is the column list the test gives it. The derivations read those
columns through the real ``relation_columns``, so no test stubs column discovery; only the warehouse is faked.
"""
from __future__ import annotations

import json

from cql_databricks.result._catalog_runtime import CurrentArtifact
from cql_databricks.result.insights.evaluation import ClosureLibrary, SelectedLibrary
from cql_databricks.result.results import PinnedRelation

CATALOG = "acme_results"
DATA_SOURCE = "conformance"
SELECTION_ID = "synthetic-selection"


def pinned(schema: str, table: str, columns: dict[str, str]) -> PinnedRelation:
    """The pinned relation of ``{CATALOG}.{schema}.{table}``, whose selected descriptor records ``columns``."""
    artifact = CurrentArtifact(table=(CATALOG, schema, table),
                               schema_ddl=json.dumps([list(column) for column in columns.items()]))
    return PinnedRelation(artifact, SELECTION_ID, lambda _artifact: True)


def library(name: str, version: str, relations: dict[str, PinnedRelation]) -> ClosureLibrary:
    """One library of the selection, holding the pinned relations of the defines the selection computed."""
    return ClosureLibrary(SelectedLibrary(name, version, draft=""), relations=dict(relations))


class FixedClosure:
    """A ``ClosureSource`` over one selection. Like ``SelectionClosure`` it refuses another evaluation identity;
    a data source other than the selection's reads as a closure nobody ever ran (no libraries)."""

    def __init__(self, libraries: dict[str, ClosureLibrary], data_source_id: str = DATA_SOURCE) -> None:
        self._libraries = libraries
        self._data_source_id = data_source_id

    def closure(self, data_source_id: str, eval_id: str, *, root: str, draft: str) -> dict[str, ClosureLibrary]:
        if eval_id != SELECTION_ID:
            raise ValueError("derivation request does not match the complete selection")
        return dict(self._libraries) if data_source_id == self._data_source_id else {}
