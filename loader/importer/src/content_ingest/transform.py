"""Shape FHIR Measure and Library resources into Lakehouse rows (CONTENT-FHIR-IG).

**The Lakehouse stores the resource, its identity, and its provenance — nothing else**
(``CONTENT-NO-DERIVED-COLUMNS``).  Everything computable from the resource — decoded CQL/ELM, the
CQL-header identity, value-set references, the measure→library junction — is derived by the
CONSUMER of the knowledge feed (``KNOWLEDGE-FEED-INGEST``), not persisted here.  The published
tables stay faithful to the spec, base64 attachments included (``CONTENT-SHARE-FAITHFUL``).

The resource itself is carried as a JSON string and parsed into a ``VARIANT`` column at write time
(``load.py``, ``CONTENT-FHIR-VARIANT``) — lossless, with no schema to maintain and no fields
silently dropped.

Table shapes produced here:
* ``measure`` — one row per (url, version): the FHIR Measure resource
* ``library`` — one row per (url, version): the FHIR Library resource
* ``valueset`` — tall ``(valueset_id, system, code)`` matching BIND-TERMINOLOGY-TABLE (rows built by
  ``samples.py`` and from VSAC)
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class MeasureRow:
    """One row for the ``measure`` table — the resource, its identity, its provenance."""
    url: str
    version: str
    resource_json: str         # verbatim FHIR JSON; parsed to VARIANT at write time
    source_package: str        # provenance: the package or folder this copy came from
    source_year: int           # reporting year the package states, 0 when none
    source_lineage: str        # "ecqm" | "dqm"
    ingested_at: str           # ISO-8601 UTC timestamp


@dataclass
class LibraryRow:
    """One row for the ``library`` table — the resource, its identity, its provenance.

    ``url``/``version`` are the FHIR CANONICAL identity, taken straight from the resource.  The
    CQL-header identity (which legitimately diverges — FHIR ``2026.0.0`` vs CQL ``5.0.000``) is a
    derivation and lives consumer-side, under the distinct names ``cql_name``/``cql_version``.
    """
    url: str
    version: str
    resource_json: str
    source_package: str
    source_year: int
    source_lineage: str
    ingested_at: str


@dataclass
class ValueSetExpansionRow:
    """One code in a ValueSet expansion — the runtime ``valueset`` table contract
    (``valueset_id, system, code``, BIND-TERMINOLOGY-TABLE)."""
    valueset_id: str           # full canonical URL (BIND-TERMINOLOGY-TABLE key)
    system: str
    code: str
    display: str | None
    version: str | None        # expansion timestamp / ValueSet version
    source_url: str            # canonical ValueSet URL
    source_package: str
    ingested_at: str


@dataclass
class TransformResult:
    """Output of transforming one package's resources."""
    measures: list[MeasureRow] = field(default_factory=list)
    libraries: list[LibraryRow] = field(default_factory=list)
    valueset_expansions: list[ValueSetExpansionRow] = field(default_factory=list)


# ── CQL parsing ──────────────────────────────────────────────────────────────

_CQL_HEADER_RE = re.compile(r"""library\s+(\S+)\s+version\s+'([^']*)'""")


def _decode_content(resource: dict[str, Any], content_type: str) -> str | None:
    """Decode a base64-encoded ``content`` attachment from a Library resource."""
    for attachment in resource.get("content", []):
        if attachment.get("contentType") == content_type:
            data = attachment.get("data")
            if data:
                try:
                    return base64.b64decode(data).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    return None
    return None


def _has_cql_content(resource: dict[str, Any]) -> bool:
    """True if the Library has CQL or ELM content (i.e., it's a CQL-based library)."""
    for attachment in resource.get("content", []):
        if attachment.get("contentType") in ("text/cql", "application/elm+json",
                                             "application/elm+xml"):
            return True
    return False


def transform_resources(resources: list[dict[str, Any]], *,
                        source_package: str, source_year: int,
                        source_lineage: str) -> TransformResult:
    """Shape a list of FHIR resources into Lakehouse rows.

    Filters to CQL-based content only (``CONTENT-FHIR-IG``): Measure resources that reference a
    Library, and Library resources that carry CQL or ELM content attachments.
    """
    now = datetime.now(timezone.utc).isoformat()
    result = TransformResult()

    for r in resources:
        resource_type = r.get("resourceType")

        if resource_type == "Measure":
            library_refs = r.get("library", [])
            if not isinstance(library_refs, list):
                library_refs = [library_refs] if library_refs else []
            # Only ingest measures that reference at least one Library (CQL-based).
            if not library_refs:
                continue

            url = r.get("url", "")
            version = r.get("version", "")
            result.measures.append(MeasureRow(
                url=url,
                version=version,
                resource_json=json.dumps(r),
                source_package=source_package,
                source_year=source_year,
                source_lineage=source_lineage,
                ingested_at=now,
            ))


        elif resource_type == "Library":
            if not _has_cql_content(r):
                continue

            url = r.get("url", "")
            result.libraries.append(LibraryRow(
                url=url,
                version=r.get("version", ""),
                resource_json=json.dumps(r),
                source_package=source_package,
                source_year=source_year,
                source_lineage=source_lineage,
                ingested_at=now,
            ))

    return result
