"""PERF-CACHE-REVISION — ONE revision stamp, and it is the only thing that busts a cached read.

Every cacheable ``/api`` GET is served with ``Cache-Control: private, no-cache`` and an ``ETag``
built from this stamp (``app.py``'s ``cache_revision`` middleware). The browser therefore KEEPS
every body it has fetched and revalidates it, and while the stamp holds still that revalidation is
a bodyless ``304`` decided before the handler runs — no store read, no SQL warehouse statement,
no ELM. When the stamp moves, every cached body in every browser is stale at once.

**WHY A STAMP AND NOT THE ``max-age`` LINES IT REPLACES.** Five endpoints each carried their own
hand-picked TTL (60 s, 300 s, 300 s, 300 s, 60 s), which was wrong in both directions at the same
time: a define's profile stayed stale for a minute AFTER the run that changed it, with nothing able
to invalidate it, while a library that had not been touched in a week was refetched every 60
seconds regardless. Expiry is a guess about how long an answer will stay true. This is the answer.

**THE STAMP IS COMPUTED, NOT SIGNALLED**, and that is the property that makes "one place" real. It
is a digest over the state CQL on Databricks already keeps — every compiled library, every Data Source, every
Knowledge Location — so a compile, a deploy, a registry edit or a pipeline-state clear moves it by
existing, with no call to remember to add at the write. It also therefore catches work done by
another process: a scheduled Lakeflow Job or a second replica moves the same rows.

**WHAT IT CANNOT SEE, STATED RATHER THAN IMPLIED.**

* **Library CONTENT ingested into Unity Catalog** out of band (the importer runs as a job, not through
  the BFF). The corpus is held in memory and read again only on ``POST /api/knowledge-sources/reload``
  or a restart (``KNOWLEDGE-CORPUS-REFRESH`` is not built), and the stamp's ``knowledge`` term moves
  when it is.

That is what :meth:`CacheStamp.bust` is for, and it is the ONLY hand-operated part: one call, one
place, exposed as ``POST /api/cache/bust``. It is deliberately not sprinkled through the write
paths — anything the digest already sees must NOT also be signalled, or the two mechanisms become
two answers to one question and the digest stops being the thing that is trusted.

**TWO GAPS THAT WERE THE SAME GAP AT TWO ALTITUDES.** First, source data: the pipeline's
`DATA-CHANGE-LOG` term means new data moves a fingerprint and the run rebuilds the table, so the
``runs`` reader sees it. Second, the run itself: ``run_ledger.py`` supplies visibility of scheduled,
replica-driven, and manual runs from the Jobs API, which every trigger source writes to. These terms
together mean the computed digest catches work done by another process — the cache sees runs
rather than only the rows CQL on Databricks writes itself.

**A RESTART BUSTS EVERYTHING**, via ``boot`` in the build term. That is how a BFF code change is
noticed: ``insights/profile.py`` gaining a field changes the payload without changing any row, and
this repo mints no build identity for the running server. It costs one refetch per client per
deploy. If a real build stamp ever exists it replaces the boot id and the busting stops being a
side effect of restarting.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Callable, Iterable

log = logging.getLogger("cql_databricks_designer.cache_stamp")

#: Which GETs are cacheable — an ALLOWLIST, and the direction is the whole point. Under a denylist a
#: route added without a thought about caching would be served with an ETag, and the failure mode of
#: getting that wrong is a live status endpoint pinned at an answer that was true once. Under this
#: one the same oversight costs a round trip nobody notices. A route earns its place here by being a
#: fact about stored content: the knowledge documents, a define's profile for an evaluation, the
#: registries.
#:
#: What is deliberately ABSENT, each for its own reason rather than by omission:
#:   ``/api/health``                       — a liveness probe; its answer is the compiler's current
#:                                           reachability, which no stored row predicts.
#:   ``/api/data-sources/{id}/status``     — live provisioning state.
#:   ``/api/data-sources/{id}/describe``   — introspects Unity Catalog at request time.
#:   ``/api/data-sources/{id}/status/stream`` — SSE; a stream has no entity to validate.
#:   ``/api/pipeline/status/…``            — live run state, which is the thing that moves fastest.
#: Every POST is absent by construction: the two expensive reads that are POSTs (``/metrics``,
#: ``/translate``) are not browser-cacheable at all, and making them GETs is `API-CONTENT-ADDRESSED`,
#: a separate change.
CACHEABLE = (
    re.compile(r"^/api/libraries/"),      # discover, source, specification, data-requirements, profile
    re.compile(r"^/api/modeled-types$"),
    re.compile(r"^/api/knowledge-sources$"),
    re.compile(r"^/api/data-sources$"),
    re.compile(r"^/api/data-sources/[^/]+$"),
    re.compile(r"^/api/results$"),
)


#: How long the digest of the tracked stores is reused before they are read again. It is the upper
#: bound on how long a browser can revalidate against an answer a run has already superseded, so it
#: is short; it exists at all because a page load fires several cacheable GETs at once and one
#: reader's burst should not be N reads of the tracked stores.
STATE_TTL_SECONDS = 10.0

#: What every cacheable response says. ``no-cache`` is not "do not cache" — it is "cache this, and
#: revalidate before reuse", which is exactly the contract the ETag completes. ``private`` keeps the
#: bodies out of shared proxies: they are read as the signed-in user (`SEC-OBO`).
CACHE_CONTROL = "private, no-cache"

#: `PERF-CLIENT-REVISION` — the revision, published on EVERY ``/api`` response as a plain header.
#:
#: **Why the ETag is not enough.** An ETag is a caching instruction addressed to the browser's own
#: cache, and under ``no-cache`` that cache may never answer without a round trip — which is correct
#: for correctness and useless for latency. The SPA cannot read it, and the three expensive reads are
#: POSTs that can never carry one. This header is the same value addressed to the APPLICATION: a
#: global revision it can compare against, so it can serve what it already holds without asking.
#:
#: One value, two audiences, one authority. The server still decides freshness; the client only
#: mirrors what it is told, which is what keeps this from becoming a second answer to one question.
REVISION_HEADER = "X-CqlDatabricks-Revision"


def is_cacheable(path: str) -> bool:
    """Whether a GET of ``path`` is served from the browser's cache under revalidation."""
    return any(pattern.match(path) for pattern in CACHEABLE)


def _scope() -> str:
    """The signed-in user the current stamp is for (`SEC-OBO`), or ``""`` outside a request.

    Read from the forwarded EMAIL rather than from `current_user.me()`: this runs on every cacheable
    GET and a round trip per request to learn a name the platform already sent would undo the saving
    the stamp exists for. It is a cache partition key, not an authorization decision — the calls that
    make the bodies authorize themselves with the token.
    """
    from cql_databricks_common.workspace import forwarded_identity

    identity = forwarded_identity()
    return identity[1] if identity else ""


class CacheStamp:
    """The current revision of everything cacheable, and the one lever that moves it by hand.

    ``readers`` is the set of stores whose contents define the revision, keyed by the name that
    appears in the digest — see the module docstring for why they are read rather than notified.
    Each is called with no arguments and returns dataclass rows or plain JSON-able values.

    **The stamp is PER SIGNED-IN USER** (`SEC-OBO`), which is not an extra feature but the only
    honest reading of what it validates. Every cacheable body is `Cache-Control: private` and is
    computed as the caller (`user_client()`), so two people can hold two different correct answers
    to one URL — and the `runs` reader is per-user by construction, since a Job one identity owns is
    one another identity cannot read. A single process-wide stamp would let one user's revalidation
    be answered `304` on the strength of another user's revision.
    """

    def __init__(self, *, version: str, readers: dict[str, Callable[[], Iterable[Any]]],
                 ttl: float = STATE_TTL_SECONDS) -> None:
        self._build = f"{version}:{uuid.uuid4().hex[:12]}"
        self._readers = readers
        self._ttl = ttl
        self._epoch = 0
        self._lock = threading.Lock()
        self._cached: dict[str, tuple[str, float]] = {}  # scope -> (stamp, monotonic)

    def bust(self, reason: str) -> str:
        """Move the stamp now, invalidating every cached body in every browser. Returns the new one.

        For the two things the digest cannot see (module docstring). ``reason`` is logged rather
        than hashed: it explains an operator action in the log, and hashing it would make two busts
        for the same reason collide.

        Busts EVERY scope, because the operator lever means "nothing you hold is trustworthy" and a
        per-user bust would leave the other users holding exactly what it was pulled for.
        """
        with self._lock:
            self._epoch += 1
            self._cached.clear()
        log.info("PERF-CACHE-REVISION: cache stamp busted (epoch %d) — %s", self._epoch, reason)
        return self.value()

    def value(self) -> str:
        """The current stamp: a short digest of the build, the manual epoch, the tracked stores, and
        the signed-in user."""
        scope = _scope()
        now = time.monotonic()
        with self._lock:
            hit = self._cached.get(scope)
            if hit is not None and now - hit[1] < self._ttl:
                return hit[0]
            epoch = self._epoch
        # The store reads happen OUTSIDE the lock: they may be remote round trips, and holding the
        # lock across them would serialize every request on the app behind the slowest one.
        parts = [f"build={self._build}", f"epoch={epoch}", f"user={scope}"]
        parts.extend(f"{name}={self._digest(name, read)}"
                     for name, read in sorted(self._readers.items()))
        stamp = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]
        with self._lock:
            self._cached[scope] = (stamp, time.monotonic())
        return stamp

    def etag(self) -> str:
        """The ``ETag`` header value for the current stamp.

        WEAK, and correctly so: the entity a browser holds was gzipped by `PERF-RESPONSE-GZIP` and
        the byte-for-byte equivalence a strong validator claims is not what is being asserted here.
        What is asserted is that nothing which could change the answer has moved.
        """
        return f'W/"{self.value()}"'

    def _digest(self, name: str, read: Callable[[], Iterable[Any]]) -> str:
        """A stable digest of one store's rows: order-independent, and blind to nothing.

        Every field of every row participates, so a column added to a row shape is covered without
        anyone remembering this module exists. The per-row strings are SORTED before hashing, so
        the digest depends on the rows and never on the order a store happened to return them.

        A read that FAILS yields a value that can never validate. Serving the last good stamp would
        pin whatever browsers already hold at the exact moment the server has stopped being able to
        tell whether it is still true, and "unknown" reads as "not current" everywhere else in this
        codebase (`pipeline/fingerprint.py`'s ``is_current``). The handler behind the 200 that this
        forces will fail loudly on its own if the store really is down (`KNOWLEDGE-FAIL-LOUD`).
        """
        try:
            # A reader may also return plain values (a generation counter, another App's revision).
            # They were once passed to `asdict`, which raised, so the stamp moved on every read and no
            # cacheable GET was ever answered 304 (found 2026-10-03, APP-PER-FEATURE).
            rows = sorted(
                json.dumps(dataclasses.asdict(row) if dataclasses.is_dataclass(row) else row,
                           sort_keys=True, default=str)
                for row in read())
        except Exception as e:  # noqa: BLE001 — any store failure means "revision unknown"
            log.warning("PERF-CACHE-REVISION: %s revision unreadable, forcing a miss: %s", name, e)
            return f"unknown:{uuid.uuid4().hex}"
        return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:16]


def matches(if_none_match: str | None, etag: str) -> bool:
    """Whether a request's ``If-None-Match`` claims the entity this ``etag`` names.

    A browser echoes back exactly what it was given, but the header is a LIST by grammar and a
    proxy may legitimately send several, so it is split rather than compared whole — a reader
    behind something that adds a second validator would otherwise never see a 304 and would pay
    full price for every read forever.
    """
    if not if_none_match:
        return False
    return any(candidate.strip() == etag for candidate in if_none_match.split(","))


def install(app: Any, stamp: CacheStamp) -> None:
    """Wire ``stamp`` onto an app: every cacheable GET revalidates, and one route busts it by hand.

    The middleware and the allowlist live HERE rather than in ``app.py`` for the reason the stamp
    exists at all — a caching policy assembled at its call sites is the thing being replaced. What
    ``app.py`` owns is which stores define the revision; what happens to a response is this module's.
    """
    from fastapi import Request, Response  # noqa: PLC0415 — the app layer, imported by the app layer
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def cache_revision(request: Request, call_next):
        # OUTERMOST, because it is installed after GZipMiddleware and Starlette runs the last
        # registered first. That is what lets a 304 leave without the compressor ever seeing it,
        # and it is why the ETag is weak — the entity the browser holds is gzipped.
        if not request.url.path.startswith("/api"):
            return await call_next(request)

        if request.method != "GET" or not is_cacheable(request.url.path):
            # NOT CACHEABLE, BUT STILL STAMPED (`PERF-CLIENT-REVISION`). The header is not a caching
            # instruction — it is the revision, published so a client can tell whether what it already
            # holds is still current. That matters most on exactly the responses this branch handles:
            # the three POST reads (`/metrics`, `/cohort-flow`, `/read-metrics`) are the expensive ones
            # and are not browser-cacheable at all, so an ETag can never reach them. Without the header
            # a client could never learn the revision from a metrics call and would have to re-run the
            # warehouse work to find out nothing had changed.
            response = await call_next(request)
            response.headers[REVISION_HEADER] = stamp.value()
            return response

        etag = stamp.etag()
        if matches(request.headers.get("if-none-match"), etag):
            # DECIDED BEFORE THE HANDLER RUNS, which is the entire saving: a define's profile costs
            # several SQL warehouse statements, and a revalidation that reached the handler would
            # pay all of them only to conclude the browser already had the answer.
            return Response(status_code=304, headers={
                "ETag": etag, "Cache-Control": CACHE_CONTROL, REVISION_HEADER: stamp.value()})
        response = await call_next(request)
        response.headers[REVISION_HEADER] = stamp.value()
        # 200 ONLY for the CACHING headers. A 404, a 409 ("never run against this scope") or a 500 is a
        # statement about the state of the world at this instant, and tagging it would let a browser
        # hold it past the moment it stops being true — the shape of the `KNOWLEDGE-FAIL-LOUD` defect,
        # where a cacheable 200 carrying an empty list survived a plain reload. The REVISION header is
        # unconditional by contrast, because it says nothing about the body: it reports the server's
        # current revision, which is true of a 500 exactly as much as of a 200.
        if response.status_code == 200:
            response.headers["ETag"] = etag
            response.headers["Cache-Control"] = CACHE_CONTROL
        return response

    @app.get("/api/cache/revision")
    def cache_revision_value() -> JSONResponse:
        """The current revision, on its own (`PERF-CLIENT-REVISION`).

        **The one case the header cannot serve.** Every ``/api`` response carries the revision, so a
        client learns it for free from work it was doing anyway — but a client that wants to know
        whether its cache is still valid while doing NOTHING ELSE has nothing to read it from. That is
        precisely the click-away-and-come-back case: on refocus the SPA has no request to make, and
        without this it would have to issue a real read (or several) to discover that nothing changed.

        ``no-store``, and that is not a detail: a cached answer to "what is the current revision" is a
        contradiction, and it is the one response in the app for which a stale value is self-defeating.
        """
        return JSONResponse(content={"revision": stamp.value()},
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/cache/bust")
    def bust_cache() -> JSONResponse:
        """The one hand-operated lever, for the two things the digest cannot see (module docstring)."""
        return JSONResponse(content={"stamp": stamp.bust("POST /api/cache/bust")},
                            headers={"Cache-Control": "no-store"})
