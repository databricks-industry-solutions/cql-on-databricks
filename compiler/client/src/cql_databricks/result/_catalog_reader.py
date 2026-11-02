"""RUN-CLIENT-REQUEST-REOPEN — current-table transport after request/tag resolution, without probes."""
from __future__ import annotations

from dataclasses import dataclass

from cql_databricks.resolvers.interfaces import ResultResolver, TableRef
from cql_databricks.data_bindings.runner import SqlRunner

from ._catalog_runtime import CurrentArtifact


@dataclass
class CatalogReader:
    """RUN-CLIENT-OPTIMISTIC — the caller owns the SQL transport and its UC identity. ``results`` is the resolver the
    results were found through (`CLIENT-RESULT-RESOLVER`)."""

    results: ResultResolver
    run: SqlRunner
    spark: object = None

    def usable(self, artifact: CurrentArtifact) -> bool:
        """RUN-CLIENT-OPTIMISTIC — trust completed metadata; do not probe availability or count rows."""
        if (not isinstance(artifact, CurrentArtifact) or not artifact.schema_ddl
                or not self.results.holds(TableRef(catalog=artifact.table[0], schema=artifact.table[1],
                                                   table=artifact.table[2]))):
            raise ValueError("artifact descriptors are missing or outside the schemas its results are found in")
        return True
