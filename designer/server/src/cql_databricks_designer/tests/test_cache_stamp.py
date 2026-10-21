"""PERF-CACHE-REVISION — one stamp busts every cached read, and a revalidation never reaches the handler.

The defect this replaces: five endpoints carried hand-picked `max-age` lines, so a define's profile
stayed stale for 60 seconds after the run that changed it with nothing able to invalidate it, while a
library nobody had touched was refetched every minute anyway. The assertions below are the two halves
of the claim — the stamp MOVES when tracked state moves, and while it holds still the expensive
handler is not entered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import anyio
import pytest
from fastapi import FastAPI

from cql_databricks_designer.cache_stamp import (
    CACHE_CONTROL, REVISION_HEADER, CacheStamp, install, is_cacheable, matches)


@dataclass
class Reply:
    """One response, as the ASGI protocol delivers it."""
    status: int
    headers: dict[str, str]
    body: bytes


def call(app, method: str, path: str, headers: dict[str, str] | None = None) -> Reply:
    """Drive the app over its ASGI interface.

    Starlette's own ``TestClient`` needs httpx, which this repo does not depend on and which a test
    is not a good enough reason to add. The protocol is small enough to speak directly, and doing so
    keeps the assertions on the middleware rather than on a client's caching behaviour.
    """
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "server": ("test", 80), "client": ("test", 1),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    anyio.run(app, scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return Reply(
        status=start["status"],
        headers={k.decode().lower(): v.decode() for k, v in start["headers"]},
        body=body)


@dataclass
class Row:
    """A stand-in for the dataclass rows the real readers return (`PipelineLibraryRow` et al.)."""
    id: str
    version: str = "1"


class Store:
    """A tracked store whose rows the test moves under the stamp."""

    def __init__(self, rows: list[Row]) -> None:
        self.rows = rows

    def list(self) -> list[Row]:
        return list(self.rows)


def _stamp(store: Store, *, ttl: float = 0.0) -> CacheStamp:
    """A stamp over one store. ``ttl=0`` so a test that moves a row sees it on the next read."""
    return CacheStamp(version="test", readers={"rows": store.list}, ttl=ttl)


# ── The stamp itself ──────────────────────────────────────────────────────────────────────────────

def test_the_stamp_holds_still_while_nothing_moves():
    store = Store([Row("a"), Row("b")])
    stamp = _stamp(store)
    assert stamp.value() == stamp.value()


def test_a_changed_row_moves_the_stamp():
    """The mechanism the whole design rests on: a compile or a deploy invalidates by existing."""
    store = Store([Row("a", version="1")])
    stamp = _stamp(store)
    before = stamp.value()
    store.rows = [Row("a", version="2")]
    assert stamp.value() != before


def test_a_new_row_moves_the_stamp():
    store = Store([Row("a")])
    stamp = _stamp(store)
    before = stamp.value()
    store.rows = [Row("a"), Row("b")]
    assert stamp.value() != before


def test_row_order_does_not_move_the_stamp():
    """A store returning the same rows in a different order is not a change, and must not read as one."""
    store = Store([Row("a"), Row("b")])
    stamp = _stamp(store)
    before = stamp.value()
    store.rows = [Row("b"), Row("a")]
    assert stamp.value() == before


def test_bust_moves_the_stamp():
    """The hand lever, for source data and out-of-band content ingest — the two the digest cannot see."""
    stamp = _stamp(Store([Row("a")]))
    before = stamp.value()
    assert stamp.bust("test") != before


def test_two_processes_do_not_share_a_stamp():
    """The build term carries a boot id, so a restart invalidates payload shapes no row describes."""
    store = Store([Row("a")])
    assert _stamp(store).value() != _stamp(store).value()


def test_a_ttl_holds_the_stamp_still_across_a_burst():
    """A page load fires several cacheable GETs at once; they must not be N reads of the stores."""
    store = Store([Row("a")])
    stamp = _stamp(store, ttl=60.0)
    before = stamp.value()
    store.rows = [Row("a", version="2")]
    assert stamp.value() == before  # within the window
    assert stamp.bust("test") != before  # the lever is not subject to it


def test_an_unreadable_store_can_never_validate():
    """"Unknown" reads as "not current" — serving the last good stamp would pin browsers at the exact
    moment the server stopped being able to tell whether it was still true."""
    class Broken:
        def list(self):
            raise RuntimeError("lakebase is down")

    stamp = CacheStamp(version="test", readers={"rows": Broken().list}, ttl=0.0)
    assert stamp.value() != stamp.value()


def test_a_plain_value_reader_holds_still_and_moves_when_it_changes():
    """PERF-CACHE-REVISION — a generation counter or another App's revision is a reader too.

    These once went through `asdict`, which raised, so the stamp read "unknown" and moved on every call.
    """
    state = {"generation": 1, "evaluator": "abc"}
    stamp = CacheStamp(version="test", ttl=0.0, readers={
        "knowledge": lambda: [state["generation"]], "evaluator": lambda: [state["evaluator"]]})
    before = stamp.value()
    assert stamp.value() == before
    state["generation"] = 2
    moved = stamp.value()
    assert moved != before
    state["evaluator"] = "def"
    assert stamp.value() != moved


# ── If-None-Match parsing ─────────────────────────────────────────────────────────────────────────

def test_if_none_match_is_read_as_a_list():
    """The header is a list by grammar; compared whole, a proxy adding a validator costs every 304."""
    assert matches('W/"abc"', 'W/"abc"')
    assert matches('W/"other", W/"abc"', 'W/"abc"')
    assert not matches('W/"other"', 'W/"abc"')
    assert not matches(None, 'W/"abc"')


# ── The allowlist ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/libraries/discover",
    "/api/libraries/ExampleMeasure/1.0.0/source",
    "/api/libraries/ExampleMeasure/1.0.0/specification",
    "/api/libraries/ExampleMeasure/1.0.0/data-requirements",
    "/api/libraries/ExampleMeasure/1.0.0/defines/Initial Population/profile",
    "/api/modeled-types",
    "/api/knowledge-sources",
    "/api/data-sources",
    "/api/data-sources/ds-1",
    "/api/results",
])
def test_the_reading_surface_is_cacheable(path):
    assert is_cacheable(path)


@pytest.mark.parametrize("path", [
    "/api/health",                                  # liveness — the compiler's current reachability
    "/api/data-sources/ds-1/status",                # live provisioning state
    "/api/data-sources/ds-1/describe",              # introspects Unity Catalog at request time
    "/api/data-sources/ds-1/status/stream",         # SSE — a stream has no entity to validate
    "/api/pipeline/status/ds-1/ExampleMeasure/1.0.0",     # live run state
    "/api/cache/bust",
])
def test_live_state_is_never_cacheable(path):
    """An allowlist, so the cost of forgetting is a round trip rather than a pinned wrong answer."""
    assert not is_cacheable(path)


# ── The middleware ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def wired():
    """An app carrying the real middleware over a handler that counts how often it is entered."""
    from fastapi.responses import JSONResponse

    app = FastAPI()
    store = Store([Row("a")])
    stamp = CacheStamp(version="test", readers={"rows": store.list}, ttl=0.0)
    calls = {"profile": 0, "health": 0}

    @app.get("/api/libraries/{name}/{version}/defines/{define}/profile")
    def profile(name: str, version: str, define: str):
        calls["profile"] += 1
        return {"define": define}

    @app.get("/api/health")
    def health():
        calls["health"] += 1
        return {"status": "ok"}

    @app.get("/api/libraries/{name}/{version}/source")
    def missing(name: str, version: str):
        return JSONResponse(status_code=404, content={"error": "no such library"})

    install(app, stamp)
    return app, store, calls


PROFILE = "/api/libraries/ExampleMeasure/1.0.0/defines/Initial Population/profile"


def test_a_cacheable_read_is_tagged_for_revalidation(wired):
    app, _, _ = wired
    res = call(app, "GET", PROFILE)
    assert res.status == 200
    assert res.headers["cache-control"] == CACHE_CONTROL
    assert res.headers["etag"].startswith('W/"')


def test_revalidation_is_a_304_that_never_enters_the_handler(wired):
    """The whole saving. A profile costs several SQL warehouse statements; a revalidation that
    reached the handler would pay all of them to conclude the browser already had the answer."""
    app, _, calls = wired
    first = call(app, "GET", PROFILE)
    second = call(app, "GET", PROFILE, {"If-None-Match": first.headers["etag"]})
    assert second.status == 304
    assert second.body == b""
    assert calls["profile"] == 1


def test_a_moved_row_makes_the_same_request_a_200_again(wired):
    """The half that `max-age` could never do: state moved, so the held body is stale AT ONCE."""
    app, store, calls = wired
    first = call(app, "GET", PROFILE)
    store.rows = [Row("a", version="2")]
    second = call(app, "GET", PROFILE, {"If-None-Match": first.headers["etag"]})
    assert second.status == 200
    assert second.headers["etag"] != first.headers["etag"]
    assert calls["profile"] == 2


def test_busting_makes_the_same_request_a_200_again(wired):
    app, _, calls = wired
    first = call(app, "GET", PROFILE)
    assert call(app, "POST", "/api/cache/bust").status == 200
    second = call(app, "GET", PROFILE, {"If-None-Match": first.headers["etag"]})
    assert second.status == 200
    assert calls["profile"] == 2


def test_a_live_endpoint_is_neither_tagged_nor_short_circuited(wired):
    app, _, calls = wired
    first = call(app, "GET", "/api/health")
    assert "etag" not in first.headers
    assert "cache-control" not in first.headers
    call(app, "GET", "/api/health", {"If-None-Match": "*"})
    assert calls["health"] == 2


def test_a_failure_is_never_tagged(wired):
    """A 404 is a statement about now. Tagging it would let a browser hold it past the moment it
    stops being true — the shape of the KNOWLEDGE-FAIL-LOUD defect."""
    app, _, _ = wired
    res = call(app, "GET", "/api/libraries/ExampleMeasure/1.0.0/source")
    assert res.status == 404
    assert "etag" not in res.headers
    assert "cache-control" not in res.headers


# --- PERF-CLIENT-REVISION: the same value, published for the APPLICATION ------------------------
#
# An ETag is addressed to the browser's cache, which under `no-cache` may never answer without a round
# trip — correct, and useless for latency. The SPA cannot read it, and the three expensive reads are
# POSTs that can never carry one. The header is the same revision addressed to the app.


@pytest.mark.parametrize("path,method", [
    (PROFILE, "GET"),                                    # cacheable, tagged
    ("/api/health", "GET"),                              # live, deliberately untagged
    ("/api/libraries/ExampleMeasure/1.0.0/source", "GET"),     # a 404
    ("/api/cache/bust", "POST"),                         # not a GET at all
])
def test_every_api_response_publishes_the_revision(wired, path, method):
    """Including the ones that carry no ETag, which is the point.

    The revision says nothing about the body — it reports the server's current state — so it is as true
    of a 404 or a POST as of a cacheable 200. A client that could only learn it from taggable GETs could
    never validate its cached metrics, which are POSTs.
    """
    app, _, _ = wired
    res = call(app, method, path)
    assert res.headers[REVISION_HEADER.lower()]


def test_the_revision_travels_on_a_304_too(wired):
    """A revalidation is the commonest response there is; a client must learn from it."""
    app, _, _ = wired
    first = call(app, "GET", PROFILE)
    second = call(app, "GET", PROFILE, {"If-None-Match": first.headers["etag"]})
    assert second.status == 304
    assert second.headers[REVISION_HEADER.lower()] == first.headers[REVISION_HEADER.lower()]


def test_the_revision_moves_when_tracked_state_moves(wired):
    """The client's whole invalidation rule rests on this being the same value the ETag is built from."""
    app, store, _ = wired
    before = call(app, "GET", PROFILE).headers[REVISION_HEADER.lower()]
    store.rows = [Row("a", version="2")]
    after = call(app, "GET", PROFILE).headers[REVISION_HEADER.lower()]
    assert before != after


def test_a_non_api_route_is_left_alone(wired):
    """The SPA's own assets are not the API's business, and stamping them would put a revision on
    every image and script the shell serves."""
    app, _, _ = wired

    @app.get("/index.html")
    def index():
        return {"ok": True}

    res = call(app, "GET", "/index.html")
    assert REVISION_HEADER.lower() not in res.headers


def test_the_revision_endpoint_answers_without_doing_anything_else(wired):
    """The one case the header cannot serve: a client returning to the tab has no request to make."""
    app, _, calls = wired
    res = call(app, "GET", "/api/cache/revision")
    assert res.status == 200
    assert json.loads(res.body)["revision"] == res.headers[REVISION_HEADER.lower()]
    # It answers from the stamp alone — no read handler is entered.
    assert calls == {"profile": 0, "health": 0}


def test_the_revision_endpoint_is_never_cached():
    """A cached answer to "what is the current revision" is a contradiction, and the one response in
    the app for which a stale value is self-defeating."""
    app = FastAPI()
    install(app, CacheStamp(version="test", readers={}, ttl=0.0))
    res = call(app, "GET", "/api/cache/revision")
    assert res.headers["cache-control"] == "no-store"
    assert "etag" not in res.headers


def test_a_reader_runs_as_the_forwarded_user_when_the_binding_is_installed_last():
    """SEC-OBO — the order both Apps use (`app.py`): stamp first, identity binding last, so it runs first.

    A reader that calls another App or the Jobs API as the user must see that user. Driven through the
    real middleware stack, because the claim is about how Starlette nests them.
    """
    from cql_databricks_common.workspace import forwarded_identity, install_forwarded_identity

    seen: list[object] = []
    app = FastAPI()

    def reader():
        seen.append(forwarded_identity())
        return []

    install(app, CacheStamp(version="test", readers={"probe": reader}, ttl=0.0))
    install_forwarded_identity(app)
    call(app, "GET", "/api/cache/revision",
         headers={"x-forwarded-access-token": "tok", "x-forwarded-email": "reader@example.com"})
    assert seen and all(s == ("tok", "reader@example.com") for s in seen), seen
