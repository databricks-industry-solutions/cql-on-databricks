"""The BFF's HTTP surface, one `APIRouter` per concern (`SPLIT-PACKAGES`).

**Why routers at all.** `app.py` held 45 routes in a single `create_app()`, every handler closing over
a local defined further up the same function. That is what made the package split hard to even start:
there was no seam at which Designer's derivations could leave while calculation's registry stayed, and
no way to construct one system's surface without constructing all three. The handlers themselves were
never the problem — the wiring was.

**Each module exposes `router(...)`, a factory taking exactly the collaborators its handlers need.**
Not a module-level `APIRouter` with globals behind it, and not `Depends` on a shared container:

* A factory's parameter list is a **declaration of what this concern depends on**, checked by the
  interpreter at call time rather than discovered when a request arrives. `routes/results.py` takes
  nothing; `routes/runs.py` takes eight things, and that asymmetry is the honest measure of which
  concern is ready to move to its own package and which is not.
* It **inverts the late binding** that `app.py` needed. There, `mcp_server` had to be built before the
  registries it reads, because the parent `FastAPI` must be constructed with the MCP app's lifespan —
  so the registries were passed as `lambda: ds_registry`, closing over a local that did not exist yet
  (`agent/wiring.py` documents that inversion and is the precedent for this one). With routers built
  from an already-assembled set of collaborators, the ordering constraint disappears instead of being
  worked around: nothing here closes over anything that is not already a value.
* It keeps **one app per system** achievable without a second copy of any handler. A Designer App
  includes Designer's routers and never constructs a `PipelineOrchestrator`.

**Route ORDER is `create_app`'s, not a router's** — and it is load-bearing. The SPA catch-all matches
`/{path:path}`, so anything registered after it is unreachable; that is how `/mcp` ends up answering an
agent's JSON-RPC with `index.html`. `tests/test_app_routes.py` pins the order for exactly this reason.
"""
