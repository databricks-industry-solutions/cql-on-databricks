"""VSAC ValueSet expansion fetcher (CONTENT-VSAC, CONTENT-VSAC-AUTHORITY).

Fetches ValueSet expansions from the NLM VSAC FHIR API and writes them into the
runtime-compatible ``valueset`` table (``valueset_id, system, code`` — BIND-TERMINOLOGY-TABLE)
under the ``vsac`` authority schema, separate from the measure-publishing authority because
ValueSet expansions originate from NLM, not the measure publisher.

Requires a UMLS API key (free account at https://uts.nlm.nih.gov/uts/).  Set via
the ``UMLS_API_KEY`` environment variable.

Which ValueSets to fetch comes from the imported package's own Library resources, as shipped
(``ecqi.valueset_urls``: their data requirements and value-set dependencies). There is no ``valueset_ref``
table: it was a derived work-list, and the Lakehouse publishes standardized FHIR resources only
(``CONTENT-LAKEHOUSE-STANDARD-ONLY``).

A value set that cannot be expanded fails the import (``CORE-NO-SILENT-MISLOWER``): a measure run against a
partial terminology table silently under-counts every population that retrieves through the missing set.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from . import config

log = logging.getLogger("content_ingest.vsac")

# VSAC FHIR R4 endpoint (NLM Terminology Services).
VSAC_FHIR_BASE = "https://cts.nlm.nih.gov/fhir"
_TIMEOUT = 60


def _oid_from_url(url: str) -> str:
    """Extract the OID / last path segment for VSAC API calls (the API endpoint path, NOT the
    ``valueset_id`` storage key)."""
    return url.rsplit("/", 1)[-1] if "/" in url else url


class VsacError(Exception):
    """A value set VSAC could not expand. Always fatal."""


def expand_valueset(valueset_url: str, *, api_key: str,
                    version: str | None = None) -> list[dict[str, str]]:
    """Fetch the expansion of a single ValueSet from VSAC.

    Returns a list of ``{valueset_id, system, code, display, version}`` dicts (the tall
    expansion rows); raises ``VsacError`` on failure.  Each row matches the ``valueset`` table schema
    the engine's BIND-TERMINOLOGY-TABLE contract expects.

    ``valueset_id`` is the full canonical URL — the compiler emits
    ``WHERE valueset_id = '<canonical URL>'`` so the stored key must match.
    """
    oid = _oid_from_url(valueset_url)
    params: dict[str, str] = {"_format": "json"}
    if version:
        params["valueSetVersion"] = version

    # VSAC uses the OID in the API endpoint path.
    url = f"{VSAC_FHIR_BASE}/ValueSet/{oid}/$expand"
    headers = {"Authorization": f"Basic {_encode_key(api_key)}"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise VsacError(f"VSAC could not expand {valueset_url}: {e}") from e

    data = resp.json()
    expansion = data.get("expansion", {})
    contains = expansion.get("contains", [])
    if not contains:
        log.info("VSAC %s: empty expansion", oid)
        return []

    rows: list[dict[str, str]] = []
    vs_version = expansion.get("timestamp", "")
    for item in contains:
        rows.append({
            "valueset_id": valueset_url,
            "system": item.get("system", ""),
            "code": item.get("code", ""),
            "display": item.get("display", ""),
            "version": vs_version,
        })
    log.info("VSAC %s: %d codes", oid, len(rows))
    return rows


def _encode_key(api_key: str) -> str:
    """VSAC uses HTTP Basic auth with ``apikey`` as the username and the key as the password."""
    import base64
    return base64.b64encode(f"apikey:{api_key}".encode()).decode()


def fetch_referenced_valuesets(valueset_urls: list[str], *,
                               api_key: str,
                               rate_limit: float = 0.5) -> list[dict[str, str]]:
    """Fetch expansions for a list of ValueSet URLs from VSAC.

    Returns the combined list of tall expansion rows.  Rate-limited to avoid VSAC throttling.
    The first value set that fails to expand raises ``VsacError``.
    """
    all_rows: list[dict[str, str]] = []
    unique_urls = sorted(set(valueset_urls))
    log.info("fetching %d unique ValueSets from VSAC", len(unique_urls))

    for i, vs_url in enumerate(unique_urls):
        all_rows.extend(expand_valueset(vs_url, api_key=api_key))
        if i < len(unique_urls) - 1:
            time.sleep(rate_limit)

    log.info("VSAC: %d total codes from %d ValueSets", len(all_rows), len(unique_urls))
    return all_rows
