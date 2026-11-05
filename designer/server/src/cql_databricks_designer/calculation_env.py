"""RUN-CLIENT-MIGRATION, SEC-OBO — durable calculations use the signed-in caller's SQL transport.

APP-COMPUTE-TARGET — a request may name the SQL warehouse to run on. None means the deployment's
``WAREHOUSE_ID``. A named warehouse the caller cannot use fails on its own statements; nothing retries
on the default.

APP-RUN-LOG — the run log is written by the App's service principal (``app_sql_runner``), never the user,
on one background writer per process.

RUN-CLIENT-SESSION-REUSE — every calculation and result read runs on the signed-in user's held warehouse
session (``cql_databricks_common.warehouse``), so the runtime functions' sandbox starts once per user.
"""
from __future__ import annotations

from cql_databricks_common import config
from cql_databricks_common.environments import user_environments
from cql_databricks_common.warehouse import user_session
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
    return CalculationSettings(catalog, config.runtime_schema())


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
    """RUN-CLIENT-DRAFT-RESULTS — deployment config only; results go to the picked environment's ``draft_results``."""
    run_log = RunLog(app_sql_runner, config.designer_catalog)
    return CalculationEnv(user_session=lambda warehouse=None: user_session(_chosen(warehouse)),
                          settings=_settings, warehouses=_warehouses,
                          run_context=_run_context, run_log=run_log.submit, environments=user_environments)
