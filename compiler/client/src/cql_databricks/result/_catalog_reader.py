"""RUN-CLIENT-REQUEST-REOPEN — current-table transport after request/tag resolution, without probes."""
from __future__ import annotations

from dataclasses import dataclass

from cql_databricks.data_bindings.runner import SqlRunner

from ._catalog_runtime import CurrentArtifact


@dataclass
class CatalogReader:
    """RUN-CLIENT-OPTIMISTIC — the caller owns the SQL transport and its UC identity."""

    destination: tuple[str, str]
    run: SqlRunner
    spark: object = None
    #: CLIENT-DISCOVERY-SCOPE — the other result scopes a closure may reuse results from.
    sources: tuple[tuple[str, str], ...] = ()

    def usable(self, artifact: CurrentArtifact) -> bool:
        """RUN-CLIENT-OPTIMISTIC — trust completed metadata; do not probe availability or count rows."""
        if (not isinstance(artifact, CurrentArtifact) or not artifact.schema_ddl
                or artifact.table[:2] not in (self.destination, *self.sources)):
            raise ValueError("artifact descriptors are missing or outside the result destination and scopes")
        return True
