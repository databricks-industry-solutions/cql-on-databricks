"""The Designer App's HTTP surface, pinned route by route (`SVC-APP-TOPOLOGY`, `APP-URL-ROUTING`).

**This gate exists because `app.py` had none.** No test in this repository imported it: 41 route
handlers and 1,836 lines were reachable only by running the deployed App, so every claim that a
change to it was behaviour-preserving rested on reading the diff. That is the same class of gap as a
suite that SKIPs for a missing dependency — a green run that proves nothing about the thing changed.
It is minted here as the contract for SPLIT-PACKAGES, whose whole promise is that moving these
handlers into `APIRouter`s and then into three packages changes no URL, no verb, and no handler.

**What it pins, and why each half is load-bearing.**

* **Path and verb** — what a client sees. A router mounted with the wrong `prefix`, a `@router.post`
  written where the app had `@app.put`, or a route silently lost to a name collision all show up here
  and nowhere else in the suite.
* **The handler each path resolves to** — because a route pointing at the *wrong function* is the
  defect this file has already survived once. `app.py`'s own comment records it: the module-level
  `measure_metrics` and the route handler `measure_metrics` collided, and an unaliased import made
  the route call ITSELF with the builder's keyword arguments, on every request. Path and verb were
  perfect throughout. Only the target was wrong.

**Nothing is stubbed** (`APP-PER-FEATURE`): the Designer's App holds no store and provisions
nothing at startup. The data-source and Bind routes keep the paths and handler names the SPA calls.
"""

from __future__ import annotations

import contextlib
from unittest import mock

import pytest


# The API surface, frozen. `(path, methods)` -> the handler that must serve it.
#
# Ordered as the reader meets the product, not as `app.py` happens to define it: health, then the
# knowledge reads, then the document's derivations, then the registry, then running things, then the
# agent surface. Adding a route means adding a row here IN THE RIGHT GROUP — which is the cheapest
# moment to notice that a new endpoint belongs to a different system than the one it was written in.
EXPECTED: dict[tuple[str, str], str] = {
    # --- deployment facts -------------------------------------------------------------------
    ("/api/health", "GET"): "health",
    ("/api/workspace-host", "GET"): "workspace_host",
    ("/api/debug/forwarded-scopes", "GET"): "forwarded_scopes",
    # PERF-CACHE-REVISION installs these two; they are part of the surface even though no handler
    # in `app.py` spells them out (`cache_stamp.install`).
    ("/api/cache/revision", "GET"): "cache_revision_value",
    ("/api/cache/bust", "POST"): "bust_cache",
    # --- knowledge: libraries and the locations they come from ------------------------------
    # DOC-OPEN-SAVE, RUN-ADDRESS-LOCATION — where a library can be opened from, and one workspace folder.
    ("/api/locations", "GET"): "locations",
    # DOC-EDITABLE-IN-SCOPE — whether the signed-in user can write a tagged store, asked before a library opens.
    ("/api/folders", "GET"): "folder",
    ("/api/libraries/discover", "GET"): "discover",
    ("/api/libraries/{name}/{version}/source", "GET"): "library_source",
    ("/api/libraries/{name}/{version}/data-requirements", "GET"): "library_data_requirements",
    ("/api/libraries/resolve", "POST"): "resolve_libraries",
    # KNOWLEDGE-LAKE-DIRECT — forget the held environments and their corpora.
    ("/api/knowledge/reload", "POST"): "reload_knowledge",
    # --- designer: the Specification document and its derivations ---------------------------
    ("/api/libraries/{name}/{version}/specification", "GET"): "library_specification",
    # DOC-OPEN-SAVE — save a library where it was opened, as the signed-in user.
    ("/api/libraries/save", "POST"): "save_library",
    # DOC-OPEN-SAVE — New and Save As: create a library at a location, as the signed-in user.
    ("/api/libraries/new", "POST"): "new_library",
    ("/api/libraries/publish", "POST"): "publish",
    ("/api/libraries/{name}/{version}/defines/{define}/profile", "POST"): "define_profile",
    ("/api/libraries/{name}/{version}/metrics", "POST"): "measure_metrics",
    ("/api/libraries/{name}/{version}/reads", "POST"): "read_metrics",
    ("/api/libraries/{name}/{version}/flow", "POST"): "cohort_flow",
    ("/api/sample-run", "POST"): "sample_run",
    # --- compiler proxy ---------------------------------------------------------------------
    ("/api/translate", "POST"): "translate",
    ("/api/modeled-types", "GET"): "modeled_types",
    # --- environments (`routes/environments.py`, as the signed-in user) ----------------------------
    # DOC-ENVIRONMENT-PICKER — read-only: an environment is a tagged catalog, not a record.
    ("/api/environments", "GET"): "list_environments",
    ("/api/environments/reload", "POST"): "reload_environments",
    # --- runs and their results, the Designer's own (`routes/runs.py`) ----------
    ("/api/run", "POST"): "run_measure",
    ("/api/run-stream", "POST"): "run_measure_stream",
    ("/api/results", "POST"): "get_results",
    ("/api/reopen", "POST"): "reopen",
    ("/api/warehouses", "GET"): "list_warehouses",
    # --- the agent surface ------------------------------------------------------------------
    ("/api/assistant/ask", "POST"): "assistant_ask",
    # No `/mcp`: the agent's MCP surface is the agent's own App (`APP-PER-FEATURE`).
}


@contextlib.contextmanager
def _app(spa_dist=None):
    """`create_app()`, with ``spa_dist`` standing in for a built SPA when the catch-all's order matters."""
    import cql_databricks_designer.app as app_module

    with contextlib.ExitStack() as stack:
        if spa_dist is not None:
            stack.enter_context(mock.patch.object(
                app_module.config, "spa_dist_dir", return_value=spa_dist))
        yield app_module.create_app()


@pytest.fixture
def built_app():
    with _app() as app:
        yield app


@pytest.fixture
def built_app_with_spa(tmp_path):
    """The app as DEPLOYED: a real `dist/` present, so the SPA catch-all is registered.

    Without this the catch-all never exists in a test run and the one ordering rule that a
    router-per-subpackage refactor can break would go unasserted — the gate would skip precisely
    where it is needed.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>stub</title>")
    with _app(spa_dist=dist) as app:
        yield app


_FRAMEWORK = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


def _walk(routes, prefix: str = ""):
    """Flatten a route list, descending through included routers.

    **`app.routes` IS NOT THE ROUTE TABLE once a router is included, and this is the trap.** Since
    FastAPI 0.141 / Starlette 1.6, `include_router` appends a single opaque `_IncludedRouter` wrapper
    — no `path`, no `methods`, no `endpoint` — and resolves the real routes behind it at match time.
    The inner paths keep their own spelling, with the `prefix` applied on the way through rather than
    baked in, so a flatten has to join the two itself.

    A surface check that reads `app.routes` naively therefore sees every routerized endpoint vanish at
    once. That is survivable when it under-reports (the "routes that disappeared" assertion fires and
    names them), and it is NOT survivable in the other direction: a check written only to catch
    *unexpected* routes would keep passing while the entire API moved out of view. This walk exists so
    that routerizing a handler changes nothing about what this file can see.
    """
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:                     # an _IncludedRouter wrapper
            ctx = getattr(route, "include_context", None)
            yield from _walk(inner.routes, prefix + (getattr(ctx, "prefix", "") or ""))
            continue
        path = getattr(route, "path", None)
        if path is None:
            continue
        yield prefix + path, route


def _surface(app) -> dict[tuple[str, str], str]:
    """Every (path, verb) the app serves, mapped to its handler name.

    FastAPI's own `/docs`, `/redoc` and `/openapi.json` are excluded — they are the framework's, not
    this app's, and pinning them would make a FastAPI upgrade look like an API change. HEAD is
    excluded for the same reason: Starlette derives it from GET.
    """
    out: dict[tuple[str, str], str] = {}
    for path, route in _walk(app.routes):
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None or path in _FRAMEWORK:
            continue
        for verb in sorted(getattr(route, "methods", None) or []):
            if verb == "HEAD":
                continue
            out[(path, verb)] = endpoint.__name__
    return out


def test_the_api_surface_is_exactly_this(built_app):
    """Every path and verb the app serves, and nothing more.

    Both directions are asserted separately so the failure names the defect: a MISSING route is a
    handler that stopped being registered (a router never included, a decorator lost in a move), and
    an UNEXPECTED one is a surface added without anyone deciding which system owns it.
    """
    actual = _surface(built_app)
    missing = sorted(set(EXPECTED) - set(actual))
    unexpected = sorted(set(actual) - set(EXPECTED))
    assert not missing, f"routes that disappeared: {missing}"
    assert not unexpected, f"routes nobody declared: {unexpected}"


def test_every_route_resolves_to_its_own_handler(built_app):
    """The target, not just the address (the `measure_metrics` self-call defect).

    A route whose path and verb are right and whose handler is the WRONG function is invisible to
    every other assertion here, and it has shipped in this file before.
    """
    actual = _surface(built_app)
    wrong = {k: (actual[k], EXPECTED[k]) for k in sorted(set(actual) & set(EXPECTED))
             if actual[k] != EXPECTED[k]}
    assert not wrong, f"route -> handler mismatches (got, want): {wrong}"


def test_no_two_routes_share_a_handler_by_accident(built_app):
    """A handler serving several addresses is fine ONLY where it was designed to.

    Anything sharing a handler across DIFFERENT paths means a decorator was copied without its body
    being renamed — the shape a bad merge leaves behind, and one that answers the wrong question with
    a 200.
    """
    by_handler: dict[str, set[str]] = {}
    for (path, _verb), handler in _surface(built_app).items():
        by_handler.setdefault(handler, set()).add(path)
    shared = {h: sorted(p) for h, p in by_handler.items() if len(p) > 1}
    assert not shared, f"one handler on several paths: {shared}"


def test_the_spa_catch_all_comes_last_of_everything(built_app_with_spa):
    """`APP-URL-ROUTING` — order IS the behaviour, so it is asserted, not assumed.

    The SPA fallback matches `/{path:path}`, which matches everything. Registered before anything
    else, it would swallow every `/api` path and answer it with `index.html`. Route order is invisible to the path/verb pinning above, and the ONE thing a
    router-per-subpackage refactor changes is the order in which things are registered.
    """
    paths = [path for path, _ in _walk(built_app_with_spa.routes)]
    assert "/{path:path}" in paths, "the SPA catch-all did not register with a dist present"
    last = len(paths) - 1
    assert paths.index("/{path:path}") == last, (
        f"the catch-all is at {paths.index('/{path:path}')} of {last}; "
        f"everything after it is unreachable: {paths[paths.index('/{path:path}') + 1:]}")


def test_the_api_surface_is_unchanged_by_the_spa_being_built(built_app_with_spa):
    """A deployed App and a fresh checkout serve the same API.

    The catch-all is the only difference a `dist/` may make. If building the SPA changed which `/api`
    routes exist, then a bug reproducible in one would be invisible in the other.
    """
    actual = {k for k in _surface(built_app_with_spa) if k[0].startswith("/api")}
    assert actual == {k for k in EXPECTED if k[0].startswith("/api")}
