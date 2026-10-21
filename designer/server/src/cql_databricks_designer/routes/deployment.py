"""Facts about THIS deployment, not about any measure (`SVC-DEPLOY-TARGETS`, `SEC-OBO`).

Three reads that answer "what am I talking to": liveness plus the compiler's capabilities, the
workspace URL the SPA links objects against, and — temporarily — what the platform actually minted for
the current request. None of them is cacheable content, which is why `/api/workspace-host` is
deliberately outside the cache-revision allowlist: it is a fact about the environment, not about stored
content the stamp tracks.
"""
from __future__ import annotations

from typing import Any

import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cql_databricks_common import config
from cql_databricks_common.compiler import CompilerClient
from cql_databricks_common.compiler import CompilerError
from cql_databricks_common.workspace import forwarded_identity

log = logging.getLogger("cql_databricks_designer")


def router(*, compiler: CompilerClient) -> APIRouter:

    router = APIRouter()

    @router.get("/api/health")
    def health() -> dict[str, Any]:
        try:
            caps = compiler.capabilities()
        except CompilerError as e:
            return {"status": "degraded", "target": config.target(), "compiler": None, "error": str(e)}
        return {"status": "ok", "target": config.target(), "compiler": caps}

    @router.get("/api/workspace-host")
    def workspace_host() -> Any:
        """APP-TOUR — the workspace URL the SPA links workspace objects against.

        Its one consumer is the Product Tour's link to the published example notebooks
        (`SVC-EXAMPLE-NOTEBOOKS`). Deliberately NOT in the cacheable allowlist (`PERF-CACHE-REVISION`):
        it is a fact about the deployment's environment, not about stored content the stamp tracks.
        """
        from cql_databricks_common.workspace import workspace_url
        try:
            return {"host": workspace_url()}
        except Exception as e:
            log.warning("no workspace host to link against: %s", e)
            return JSONResponse(status_code=503, content={"error": str(e)})

    @router.get("/api/debug/forwarded-scopes")
    def forwarded_scopes() -> Any:
        """What the platform actually minted for THIS request (`SEC-OBO`).

        A diagnostic endpoint, not a product surface. It exists because every other reading is indirect:
        `effective_user_api_scopes` says what the App ASKED for and the workspace ACCEPTED, while the
        failure we diagnose is a token that arrives carrying less than that. Only the token's own `scope`
        claim distinguishes "the platform downscoped us" from "this session's grant predates the scopes".

        **The token itself never leaves this function** — the claims are decoded WITHOUT verification
        (we are reading what we were handed, not trusting it: nothing is authorized on this) and only
        `scope`, `sub` and the times are returned. Signature verification is pointless here and would
        need a JWKS fetch; the claim is being read as diagnostic data, not as a credential.
        """
        import base64
        import binascii

        identity = forwarded_identity()
        if not identity:
            return JSONResponse(status_code=503, content={
                "error": "no x-forwarded-access-token on this request — user authorization is not "
                         "forwarding a token at all, which is a different fault from a narrow one"})
        token, email = identity
        parts = token.split(".")
        if len(parts) != 3:
            return {"email": email, "jwt": False,
                    "note": "the forwarded token is not a JWT, so it carries no readable scope claim"}
        try:
            body = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(body))
        except (ValueError, binascii.Error) as e:
            return JSONResponse(status_code=500, content={"error": f"undecodable claims: {e}"})
        scope = claims.get("scope") or claims.get("scp") or ""
        return {"email": email,
                "scopes": sorted(scope.split()) if isinstance(scope, str) else scope,
                "subject": claims.get("sub"),
                "issued_at": claims.get("iat"), "expires_at": claims.get("exp")}

    return router
