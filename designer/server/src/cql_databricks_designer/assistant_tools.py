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

from cql_databricks.agent.registry import ToolRegistry
from cql_databricks.data_bindings import ContentResolver

from .explain.narrations import NarrationKey, NarrationStore, contract_digest, signed_in_user, source_digest
from .specification import parse_specification
from .specification.measure_metadata import authored_overlay, compose
from .specification.model import SpecificationOverlay

log = logging.getLogger("cql_databricks_designer.assistant_tools")


def specification_document(library: str, version: str, *, content: ContentResolver,
                           narrations: NarrationStore) -> dict[str, Any] | None:
    """A library's Specification: the prose its measure's FHIR resource states (`SPEC-MEASURE-METADATA`),
    composed with the signed-in user's cached narration (`APP-NARRATIVE-OVERLAY`). Authored prose wins.

    Both are enrichments of a document that stands without them, so a failed read of either is logged
    and the document is served without it — `KNOWLEDGE-FAIL-LOUD` governs reads whose result IS the
    answer, and this is not one.

    ``None`` when there is no Specification at all, which the route reports as a 404 and the tool
    reports as a null — a real answer in both cases.
    """
    try:
        resource = content.corpus().measure_resource(library, version)
    except Exception as e:  # noqa: BLE001 — an enrichment must not take the document down
        log.warning("Measure metadata unavailable for %s@%s: %s", library, version, e)
        resource = None
    authored = authored_overlay(resource, library=library, version=version) if resource else None
    composed = compose(authored, cached_narration(library, version, content=content, narrations=narrations))
    return composed.to_dict() if composed is not None else None


def cached_narration(library: str, version: str, *, content: ContentResolver,
                     narrations: NarrationStore) -> SpecificationOverlay | None:
    """APP-NARRATIVE-OVERLAY — the signed-in user's newest narration of this exact CQL, or None."""
    try:
        found = content.corpus().library(library, version)
        if found is None:
            return None
        instance = narrations.latest(NarrationKey(
            user=signed_in_user(), library=library, version=version,
            source_sha256=source_digest(found.source), contract_sha256=contract_digest()))
        return parse_specification(instance) if instance else None
    except Exception as e:  # noqa: BLE001 — an enrichment must not take the document down
        log.warning("Narration unavailable for %s@%s: %s", library, version, e)
        return None


def designer_tools(*, content: ContentResolver, narrations: NarrationStore) -> ToolRegistry:
    """The Designer's contribution to the agent's server: ``get_specification``, and the sentence that
    says to start with it."""
    registry = ToolRegistry(instructions=(
        "Start with `get_specification` — it is the map of what can be addressed, and every other tool "
        "takes a define name it lists."))

    @registry.tool
    def get_specification(library: str, version: str) -> dict[str, Any] | None:
        """What a measure DECLARES: its defines, populations and the prose that narrates them.

        Start here. This is the map of what can be addressed — every other tool takes a define name
        that this one lists. `version` is the base version the measure states (e.g. "0.0.006").
        Returns null when the library has no specification, which is an answer, not a failure.
        """
        return specification_document(library, version, content=content, narrations=narrations)

    return registry
