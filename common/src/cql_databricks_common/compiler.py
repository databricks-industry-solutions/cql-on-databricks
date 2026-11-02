"""Thin HTTP client for the CQL Compiler Service (SVC-COMPILE-ENDPOINT, SVC-PROTOCOL).

The backend reaches the engine ONLY over HTTP (APP-DECOUPLE) — it hosts no JVM. This covers the
authoring surface (/translate, /capabilities, /health); runtime execution reaches /compile through the
cql-databricks RemoteProvider (run.py), not here. Errors are raised loudly so the API returns a clean
diagnostic rather than a silent pass (CORE-NO-SILENT-MISLOWER).

Auth is decided by the endpoint, not by the caller (SEC-APP-TO-APP, app_auth.py): a deployed App is
reached with this process's Databricks identity, a local JVM host with the static CQL_COMPILER_TOKEN.
"""
from __future__ import annotations

from typing import Any

import requests

from cql_databricks_common import app_auth


class CompilerError(RuntimeError):
    """A Compiler Service transport or HTTP error, surfaced to the caller."""


class CompilerClient:
    """SVC-PROTOCOL client. Same contract whether the service is a local JVM process or a deployed App
    (SVC-DEPLOY-TARGETS) — only the base URL and the credential differ.

    ``token`` is the LOCAL host's static bearer (`CQL_COMPILER_TOKEN`); against a deployed App it is
    rejected, because that endpoint is reached with this process's Databricks identity instead
    (`SEC-APP-TO-APP`)."""

    def __init__(self, endpoint: str, token: str | None = None, timeout: float = 30.0) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _headers(self, json_body: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if json_body:
            headers["content-type"] = "application/json"
        # Resolved per request: the identity token is short-lived, and the SDK refreshes it behind
        # `app_auth.identity_bearer()` (SEC-WS-CLIENT-SHARED). Caching it here would outlive it.
        bearer = app_auth.bearer(self._endpoint, self._token)
        if bearer:
            headers["authorization"] = f"Bearer {bearer}"
        return headers

    def _get(self, path: str) -> Any:
        try:
            res = requests.get(f"{self._endpoint}{path}", headers=self._headers(False),
                               timeout=self._timeout, allow_redirects=False)
        except requests.RequestException as e:
            raise CompilerError(f"compiler service unreachable at {self._endpoint}{path}: {e}") from e
        return self._parse(path, res)

    def _post(self, path: str, body: dict, *, timeout: float | None = None) -> Any:
        try:
            res = requests.post(f"{self._endpoint}{path}", json=body, headers=self._headers(True),
                                timeout=timeout or self._timeout, allow_redirects=False)
        except requests.RequestException as e:
            raise CompilerError(f"compiler service unreachable at {self._endpoint}{path}: {e}") from e
        return self._parse(path, res)

    def _parse(self, path: str, res: requests.Response) -> Any:
        # The platform OAuth gate answers an unauthenticated request with a redirect to the Databricks
        # sign-in page (SEC-APP-TO-APP). Requests are sent with allow_redirects=False so that arrives
        # here as the 3xx it is, instead of following through to a 200 carrying HTML — which read as a
        # malformed compiler response and hid the actual fault (an auth failure) behind a parse error.
        if 300 <= res.status_code < 400:
            raise CompilerError(
                f"compiler service {res.status_code} on {path}: redirected to "
                f"{res.headers.get('location', '<no location>')!r} — the Databricks App OAuth gate did "
                "not accept this request's identity. Check that the CQL on Databricks App's service principal has "
                "CAN_USE on the compiler App (the `app` resource in databricks.yml, DEPLOY-APP-GRANTS).")
        if not res.ok:
            detail = res.text
            try:
                parsed = res.json()
                if isinstance(parsed, dict) and parsed.get("error"):
                    detail = parsed["error"]
            except ValueError:
                pass
            raise CompilerError(f"compiler service {res.status_code} on {path}: {detail}")
        try:
            return res.json()
        except ValueError as e:
            # A 2xx whose body is NOT JSON. Fail loud with the body (CORE-NO-SILENT-MISLOWER) rather
            # than a raw JSONDecodeError 500.
            snippet = " ".join(res.text[:200].split())
            raise CompilerError(
                f"compiler service {res.status_code} on {path}: non-JSON response. Body: {snippet!r}"
            ) from e

    def capabilities(self) -> dict:
        """GET /capabilities — the advertised method set (SVC-SPLIT)."""
        return self._get("/capabilities")

    def health(self) -> dict:
        """GET /health — liveness (open, no auth)."""
        return self._get("/health")

    def translate(self, cql: str, includes: list[str] | None = None) -> dict:
        """POST /translate — CQL -> ELM diagnostics (authoring, Spark-free). Returns {elm, diagnostics, ok}."""
        return self._post("/translate", {"cql": cql, "includes": includes or []})

    def resource_schema(self, types: list[str]) -> dict[str, dict]:
        """POST /resource-schema — the canonical `__resource` mount DDL per FHIR type (SVC-RESOURCE-SCHEMA).

        The conformance loader uses this to build contract-correct `retrieve_<Type>` tables on the
        `(__subject_id, __resource)` mount contract without re-deriving the engine's FHIR model — the DDL
        comes from the engine's static schema, served over HTTP so the loader never depends on the JVM
        (APP-DECOUPLE). Returns `{Type: {modeled, patientContext?, ddl?}}`; an
        unmodeled type is reported (`modeled: False`), never silently dropped (CORE-NO-SILENT-MISLOWER)."""
        return self._post("/resource-schema", {"types": types})["schemas"]

    def list_modeled_types(self) -> list[str]:
        """POST /resource-schema with no types — the enumeration of every modeled FHIR resource
        (SVC-RESOURCE-SCHEMA). Additive shape on the same endpoint: an empty body returns
        `{resourceTypes: [...sorted...]}` (resources only — datatypes/backbones excluded). The CQL on Databricks Data
        Sources editor uses this so it offers only bindable entities instead of a hardcoded list."""
        return self._post("/resource-schema", {})["resourceTypes"]

    def describe(self, elm: str, deps: list[str] | None = None) -> dict:
        """POST /describe — single-library graph description. Returns LibraryGraph:
        {defines, dependencies, boundaries, materializedDefines, compilerVersion, ...}."""
        body: dict = {"elm": elm}
        if deps:
            body["deps"] = deps
        return self._post("/describe", body)

    def describe_multi(self, libraries: list[dict]) -> dict:
        """POST /describe with the ``libraries`` field — multi-library merged graph description
        (DESIGNER-SDP-NAV). Each entry is ``{elm, deps}``. Returns a merged LibraryGraph with
        cross-library edges, library-qualified defines, and deduplication."""
        return self._post("/describe", {"libraries": libraries})


def compile_provider(endpoint: str, token: str | None = None, *, timeout: float = 600.0):
    """The compiler's own client transport (`cql_databricks.compiler.RemoteProvider`) for this process.

    The deployer library speaks the compiler's client types, so the service hands it the
    compiler's client rather than ``CompilerClient``. Auth is the same decision as ``CompilerClient``'s,
    made per request (`SEC-APP-TO-APP`, ``app_auth.bearer``); a plan compile is long, hence the timeout.
    """
    from cql_databricks.compiler import CredentialProvider, RemoteProvider

    class _AppBearer(CredentialProvider):
        def auth_headers(self) -> dict[str, str]:
            value = app_auth.bearer(endpoint, token)
            return {"Authorization": f"Bearer {value}"} if value else {}

    return RemoteProvider(endpoint, _AppBearer(), timeout=timeout)
