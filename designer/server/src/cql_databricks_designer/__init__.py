"""Designer — the living Specification document, and the process that serves it (`SVC-APP-TOPOLOGY`).

What it owns (`systems.md`): the Specification document — narrative and its provenance, lenses, outline,
composition; the derivations for READING it (`insights/`, `explain/`, the narration loader, the sample
deck); the reader's cache stamp; and the built SPA it serves at the root after `/api`
(`DEC-SPA-OUTPUT`, `APP-URL-ROUTING`). It owns no bindings, no settings, no measure repository, and it
does not run measures — it asks calculation, like any other client.

**This package is also the ASSEMBLY, and that is temporary.** `create_app` still builds one FastAPI
holding every system's routers, because in step (b) there is still one App. `CALC-CONSOLE` gives
calculation its own, and `DEC-CONSOLE-TOPOLOGY` takes the App count from three to four (compiler,
Designer, calculation, MCP). Until then the import list in `app.py` reaching across all four packages is
the honest picture: one process, four systems.

**It is what `cql_databricks_pipeline` became**, and the rename is the point rather than bookkeeping.
"Pipeline" named a mechanism — and by the end it named a package holding the document model, the
orchestrator, the measure repository and the config chain all at once, which is the same fault
`service/` and `server/` had one level up: a name you cannot say to a user, covering things that do not
belong together. What stayed here is the half you can: Designer.
"""

__all__ = ["create_app"]


def create_app():
    """Lazy re-export so `python -m cql_databricks_designer` and tests can import the package without
    importing FastAPI/pyspark until the app is actually built."""
    from .app import create_app as _create

    return _create()
