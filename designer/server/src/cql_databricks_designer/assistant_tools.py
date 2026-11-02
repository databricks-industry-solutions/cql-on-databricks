"""AGENT-TOOL-REGISTRY — the tools the Designer contributes to the agent's surface.

The Specification is the Designer's (`DEC-SPECIFICATION-SCOPE`), and the agent may not import it
(`DEC-AGENT-DESIGNER-CYCLE`), so the Designer registers ``get_specification`` into the agent's tool
registry rather than the agent reaching for it. ``specification_document`` is the one composition both
the tool and ``/api/libraries/{name}/{version}/specification`` serve — a second copy is how a define
comes to read one way to a person and another way to an agent.
"""
from __future__ import annotations

import logging
from typing import Any

from cql_databricks.agent.server.registry import ToolRegistry
from cql_databricks.agent.server.wiring import current_environment
from cql_databricks.data_bindings import ContentResolver

from cql_databricks.compiler.folder_content import folder_measure
from cql_databricks.compiler.location import Location

from .data_bindings import EnvironmentContents, files_for
from .specification.measure_metadata import authored_overlay

log = logging.getLogger("cql_databricks_designer.assistant_tools")


def specification_document(library: str, version: str, *, content: ContentResolver,
                           location: str | None = None) -> dict[str, Any] | None:
    """A library's Specification: the prose its measure's FHIR resource states (`SPEC-MEASURE-METADATA`), read from
    the store or folder ``location`` names (RUN-ADDRESS-LOCATION) — in a folder, the package's ``Measure`` JSON.

    The resource is an enrichment of a document that stands without it, so a failed read is logged and
    the document is served without it — `KNOWLEDGE-FAIL-LOUD` governs reads whose result IS the answer,
    and this is not one.

    ``None`` when there is no Specification at all, which the route reports as a 404 and the tool
    reports as a null — a real answer in both cases.
    """
    at = Location.parse(location) if location else None
    try:
        if at is not None and at.kind == "folder":
            found = folder_measure(files_for(location), at.path, library, version)
            resource = found[1] if found else None
        else:
            resource = content.corpus().measure_resource(library, version,
                                                         store=at.catalog_schema if at else None)
    except Exception as e:  # noqa: BLE001 — an enrichment must not take the document down
        log.warning("Measure metadata unavailable for %s@%s: %s", library, version, e)
        return None
    authored = authored_overlay(resource, library=library, version=version) if resource else None
    return authored.to_dict() if authored is not None else None


def designer_tools(*, contents: EnvironmentContents) -> ToolRegistry:
    """The Designer's contribution to the agent's server: ``get_specification``, and the sentence that
    says to start with it. It reads the knowledge of the environment the drawer passes (`AGENT-DEFAULT-ENVIRONMENT`)."""
    registry = ToolRegistry(instructions=(
        "Start with `get_specification` — it is the map of what can be addressed, and every other tool "
        "takes a define name it lists."))

    @registry.tool
    def get_specification(library: str, version: str, location: str | None = None) -> dict[str, Any] | None:
        """What a measure DECLARES: its defines, populations and the prose that narrates them.

        Start here. This is the map of what can be addressed — every other tool takes a define name
        that this one lists. `version` is the base version the measure states (e.g. "0.0.006").
        Returns null when the library has no specification, which is an answer, not a failure.
        `location` is where the library is read: `store:<catalog>.<schema>` or `folder:<path>`; omit it for the
        environment's knowledge.
        """
        return specification_document(library, version, content=contents.get(current_environment()),
                                      location=location)

    return registry
