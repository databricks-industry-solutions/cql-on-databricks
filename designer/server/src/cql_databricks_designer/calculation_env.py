"""RUN-CLIENT-MIGRATION, SEC-OBO — durable calculations use the signed-in caller's SQL transport.

APP-COMPUTE-TARGET — a request may name the SQL warehouse to run on. None means the deployment's
``WAREHOUSE_ID``. A named warehouse the caller cannot use fails on its own statements; nothing retries
on the default.

APP-RUN-LOG — the run log is written by the App's service principal (``app_sql_runner``), never the user,
on one background writer per process.
"""
from __future__ import annotations

from cql_databricks.data_bindings import warehouse_runner
from cql_databricks.result.results import warehouse_execute
from cql_databricks_common import config
from cql_databricks_common.workspace import forwarded_identity, user_client

from .data_bindings import app_sql_runner
from .routes.runs import CalculationEnv, CalculationSettings
from .run_log import RunContext, RunLog


def _warehouse() -> str:
    warehouse = config.warehouse_id()
    if not warehouse:
        raise RuntimeError("WAREHOUSE_ID is not set: durable calculations require a SQL warehouse")
    return warehouse


def _chosen(warehouse: str | None) -> str:
    """APP-COMPUTE-TARGET — the requested warehouse, or the deployment default when none was named."""
    if warehouse is None:
        return _warehouse()
    if not warehouse.strip():
        raise ValueError("warehouse_id must name a SQL warehouse")
    return warehouse


def _settings() -> CalculationSettings:
    catalog = config.runtime_catalog()
    if not catalog:
        raise RuntimeError("CQL_RUNTIME_CATALOG is not configured")
    return CalculationSettings(config.designer_results_catalog(), catalog, config.runtime_schema())


#: APP-COMPUTE-TARGET — warehouse types that cannot run the runtime's ``cql_*`` UC functions, so are not offered.
UNSUPPORTED_WAREHOUSE_TYPES = frozenset({"REALTIME"})


def _supported(warehouses: list[dict]) -> list[dict]:
    """APP-COMPUTE-TARGET — the listable warehouses, minus types that do not support the UC functions.

    Takes the REST API's own records: the SDK's ``EndpointInfoWarehouseType`` (0.138) has no ``REALTIME``
    member and parses that type as None, which would hide exactly the type this filters.
    """
    listed = [{"id": w["id"], "name": w.get("name"), "state": w.get("state")}
              for w in warehouses if w.get("warehouse_type") not in UNSUPPORTED_WAREHOUSE_TYPES]
    return sorted(listed, key=lambda w: (w["name"] or "").lower())


def _warehouses() -> dict:
    """APP-COMPUTE-TARGET — SQL warehouses the signed-in user can see (SEC-OBO), never the App's list."""
    body = user_client().api_client.do("GET", "/api/2.0/sql/warehouses")
    return {"default": _warehouse(), "warehouses": _supported(body.get("warehouses") or [])}


def _run_context(warehouse: str | None) -> RunContext:
    """APP-RUN-LOG — the user is the email the Apps platform forwards on every request (none locally)."""
    identity = forwarded_identity()
    return RunContext(user=(identity[1] or None) if identity else None, warehouse_id=_chosen(warehouse),
                      app_version=config.app_version())


def calculation_env() -> CalculationEnv:
    """RUN-CLIENT-PRIVATE-PROVISIONING — deployment config only; no per-user destination setting."""
    run_log = RunLog(app_sql_runner, config.designer_catalog)
    return CalculationEnv(user_execute=lambda warehouse=None: warehouse_execute(user_client(), _chosen(warehouse)),
                          user_run=lambda warehouse=None, observer=None: warehouse_runner(
                              user_client(), _chosen(warehouse), observer=observer),
                          settings=_settings, warehouses=_warehouses,
                          run_context=_run_context, run_log=run_log.submit)
