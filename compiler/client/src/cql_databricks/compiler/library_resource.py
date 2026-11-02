"""DOC-OPEN-SAVE, DOC-AUTO-SAVE, CLIENT-PUBLISH — the FHIR ``Library`` resource that carries a library's CQL and ELM.

One shape for every writer: the Designer's saves into a folder, New and Save As, and `publish` into a tagged store.
A ``Library`` never holds ELM that the CQL beside it does not compile to: CQL that compiles carries the ELM compiled
from it, CQL that does not carries none, and an ELM copy that cannot be regenerated here (ELM XML) is dropped.
"""
from __future__ import annotations

import base64
import copy
from typing import Any, Optional

#: The FHIR ``Library.type`` of a CQL logic library.
LOGIC_LIBRARY = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/library-type",
                             "code": "logic-library"}]}


def library_canonical(base: str, name: str) -> str:
    """The canonical URL of library ``name`` under the deployment's one ``base``."""
    return f"{base}/Library/{name}"


def library_resource(name: str, version: str, cql: str, elm: Optional[str], *, base: str,
                     derived_from: Optional[str] = None) -> dict[str, Any]:
    """A draft ``Library`` for ``name`` ``version``: identity under ``base``, the CQL and its compiled ELM (none
    when it does not compile), and, for a copy, the ``canonical|version`` it was derived from (`APP-DERIVE-MEASURE`)."""
    resource: dict[str, Any] = {
        "resourceType": "Library", "id": name, "url": library_canonical(base, name), "version": version,
        "name": name, "status": "draft", "type": LOGIC_LIBRARY,
        "content": [{"contentType": "text/cql", "data": _b64(cql)},
                    *([{"contentType": "application/elm+json", "data": _b64(elm)}] if elm is not None else [])]}
    if derived_from:
        resource["relatedArtifact"] = [{"type": "derived-from", "resource": derived_from}]
    return resource


def with_content(resource: dict[str, Any], cql: str, elm: Optional[str]) -> dict[str, Any]:
    """``resource`` (a FHIR ``Library``) carrying ``cql`` and, when it compiles, its ELM ``elm`` — nothing else
    changed. With ``elm`` None every ELM attachment goes; ELM XML goes either way."""
    out = copy.deepcopy(resource)
    content: list[dict[str, Any]] = []
    has_cql = has_elm = False
    for attachment in out.get("content") or []:
        kind = (attachment.get("contentType") or "").lower()
        if "cql" in kind:
            attachment["data"] = _b64(cql)
            has_cql = True
        elif "elm" in kind:
            if elm is None or "json" not in kind or has_elm:
                continue
            attachment["data"] = _b64(elm)
            has_elm = True
        content.append(attachment)
    if not has_cql:
        content.insert(0, {"contentType": "text/cql", "data": _b64(cql)})
    if elm is not None and not has_elm:
        content.append({"contentType": "application/elm+json", "data": _b64(elm)})
    out["content"] = content
    return out


def cql_of(resource: dict[str, Any]) -> Optional[str]:
    """The CQL a ``Library`` carries, decoded, or None."""
    for attachment in resource.get("content") or []:
        if (attachment.get("contentType") or "").lower() == "text/cql" and attachment.get("data"):
            return base64.b64decode(attachment["data"]).decode("utf-8")
    return None


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")
