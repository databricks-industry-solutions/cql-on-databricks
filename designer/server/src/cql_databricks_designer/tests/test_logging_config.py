"""CORE-NO-SILENT-MISLOWER — the app's own loggers must reach the terminal in the process that
serves requests.

The defect these guard against was not a wrong log LINE, it was a wrong PROCESS. `uvicorn --reload`
serves requests from a worker subprocess that imports `create_app` through the import string and
never calls `main()`, so the `basicConfig` in `main` configured only the reloader parent. Every
`cql_databricks_designer.*` INFO record in local dev was dropped, and the absence was indistinguishable from
the code never running — which is exactly how it was found (a schema step that appeared never to
run and was simply never heard).
"""
from __future__ import annotations

import logging.config

from cql_databricks_designer.serve import log_config


def test_app_logger_is_declared_at_info() -> None:
    """`cql_databricks_designer` logs at INFO — the level ensure_results_schema and the orchestrator use."""
    loggers = log_config()["loggers"]
    assert loggers["cql_databricks_designer"]["level"] == "INFO"
    assert loggers["cql_databricks_designer"]["handlers"], "declared with no handler — records go nowhere"


def test_app_logger_does_not_propagate() -> None:
    """No double printing in the non-reload case, where basicConfig also installs a root handler."""
    assert log_config()["loggers"]["cql_databricks_designer"]["propagate"] is False


def test_noisy_libraries_stay_at_warning() -> None:
    for noisy in ("pyspark", "databricks.sdk", "py4j"):
        assert log_config()["loggers"][noisy]["level"] == "WARNING"


def test_config_applies_cleanly_and_yields_an_enabled_info_logger() -> None:
    """The dict is valid dictConfig input, and a module logger is live at INFO under it.

    Applying it is the assertion: a malformed handler or formatter reference raises here rather
    than at startup, and `isEnabledFor` is what actually decides whether a record survives.

    The applied config is deliberately left in place rather than "restored". Swapping the logger
    registry out and back is what makes a logging test poison its neighbours — modules bind their
    logger at import (`log = logging.getLogger(__name__)`) and keep that object, so clearing the
    registry desynchronizes those live references from the ones later lookups return. This config
    is the one the app runs with, so applying it is a no-op for every other test.
    """
    logging.config.dictConfig(log_config())
    # ONE ASSERTION PER PACKAGE, and that is the point of the loop rather than a single spot-check.
    # Module loggers are named from `__name__`, so SPLIT-PACKAGES gave the code four logging roots where
    # it had one. A config naming only the old root leaves the other three at the default WARNING: every
    # INFO record goes nowhere and nothing reports that it did. This caught exactly that regression when
    # `results.query` moved to another package, and it is what will catch the next package.
    for package in ("cql_databricks_designer", "cql_databricks.result",
                    "cql_databricks.data_bindings", "cql_databricks_common", "cql_databricks.agent"):
        assert logging.getLogger(f"{package}.some.module").isEnabledFor(logging.INFO), \
            f"{package} records below WARNING would go nowhere"
