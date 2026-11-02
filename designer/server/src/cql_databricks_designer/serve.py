"""How the Designer's FastAPI App runs under uvicorn.

`APP-PER-FEATURE` gave each feature its own App and its own `python -m` entry point. They differ only in
the app factory, the local port and the directory a local reload watches, so the rest lives here: the
logging config, the port and host a Databricks App expects, and reload in local dev only.
"""
from __future__ import annotations

import copy
import logging
import os

import uvicorn

_NOISY = ("pyspark", "databricks.sdk", "py4j")

_OURS = ("cql_databricks_designer", "cql_databricks.data_bindings",
         "cql_databricks_common", "cql_databricks.agent", "cql_databricks.result")


def log_config() -> dict:
    """uvicorn's logging config, extended with the app's own loggers.

    **The app's loggers are declared HERE rather than only via ``basicConfig`` because of
    ``--reload``.** With reload on, uvicorn runs the application in a WORKER SUBPROCESS that imports
    ``create_app`` through the import string and never calls :func:`serve` — so a ``basicConfig`` in
    ``serve`` configures the reloader PARENT, the one process that handles no requests. The worker was
    left with no root handler at all, which means every ``cql_databricks_*`` INFO record in local dev
    would have gone nowhere without this fix, with only WARNING and above reaching ``logging.lastResort``.
    Local dev sets ``reload=local``, so this only affects local-dev behavior, not deployed apps.

    uvicorn applies ``log_config`` inside the worker, so declaring the loggers here is what puts them
    where the work happens. ``propagate: False`` keeps the record from also reaching the root handler
    ``basicConfig`` installs in the non-reload case, which would print every line twice.
    """
    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["formatters"]["cql-databricks"] = {"format": "%(levelname)s %(name)s: %(message)s"}
    config["handlers"]["cql-databricks"] = {
        "formatter": "cql-databricks", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"}
    for ours in _OURS:
        config["loggers"][ours] = {
            "handlers": ["cql-databricks"], "level": "INFO", "propagate": False}
    for noisy in _NOISY:
        config["loggers"][noisy] = {"level": "WARNING"}
    return config


def serve(factory: str, *, default_port: int, reload_dir: str) -> None:
    """Run ``factory`` (an ``module:function`` import string) under uvicorn.

    ``default_port`` is the local port; ``reload_dir`` is the package directory a local run watches.
    """
    # Configures THIS process (the reloader parent, and anything logged before uvicorn starts).
    # The worker that actually serves requests is configured by `log_config` — see its docstring
    # for why the two are not the same process.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in _NOISY:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Local config (SVC-LOCAL-CONFIG-BUNDLE): workspace settings from databricks.local.yml, local-run
    # settings from the optional root .env. A no-op when run-app.sh already loaded it, and in a
    # deployed App (no checkout, so neither file) — the App's injected env is all there is.
    from cql_databricks_common.local_env import load_local_env

    load_local_env()

    # A Databricks App injects the port it wants the process to listen on as DATABRICKS_APP_PORT and
    # requires binding 0.0.0.0 (the container is reached over the network, not loopback). Locally it
    # is PORT or ``default_port`` on 127.0.0.1 (IPv4 loopback): the Vite dev server targets 127.0.0.1, and a
    # "localhost" bind would resolve to ::1 first and be refused.
    port = int(os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("PORT") or default_port)
    host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("DATABRICKS_APP_PORT") else "127.0.0.1")
    # Import string so uvicorn can manage the app; factory=True calls it.
    # Local dev: reload watches the package directory and restarts automatically.
    # Deployed (DATABRICKS_APP_PORT set): no reload — the container runs a fixed artifact.
    local = not os.environ.get("DATABRICKS_APP_PORT")
    uvicorn.run(factory, host=host, port=port, factory=True,
                reload=local, reload_dirs=[reload_dir] if local else None,
                log_config=log_config())
