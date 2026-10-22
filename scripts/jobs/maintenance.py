"""RUN-CLIENT-MAINTENANCE — the daily `cql-databricks-designer-maintenance` Job's entry point (`databricks.yml`).

It runs as the deployment's maintenance principal (`scripts/deploy/maintenance_principal.py`) and calls the
client's expiry, ``cql_databricks.result.maintenance.maintain_private_results``. Locally, preview with
``scripts/with-local-env.sh python scripts/jobs/maintenance.py --private-catalog <c> --warehouse <id>``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict

from cql_databricks.data_bindings.runner import warehouse_runner
from cql_databricks.result.maintenance import maintain_private_results


def parser() -> argparse.ArgumentParser:
    """RUN-CLIENT-MAINTENANCE — deployment arguments only; no user session or Compiler credentials."""
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--private-catalog", required=True)
    command.add_argument("--warehouse", required=True)
    command.add_argument("--retention-days", type=int, default=7)
    command.add_argument("--limit", type=int, default=1000)
    command.add_argument("--apply", action="store_true", help="delete expired results; default is preview")
    command.add_argument("--job-run-as", action="store_true")
    return command


def main(argv=None) -> int:
    """RUN-CLIENT-MAINTENANCE — run as the deployment-granted maintenance principal; never substitute credentials."""
    args = parser().parse_args(argv)
    profile = os.environ.get("DATABRICKS_CONFIG_PROFILE")
    if args.job_run_as and profile:
        raise ValueError("Job run-as cannot use a local Databricks profile")
    if not args.job_run_as and not profile:
        raise ValueError("select a profile in databricks.local.yml and use scripts/with-local-env.sh")
    from databricks.sdk import WorkspaceClient
    workspace = WorkspaceClient() if args.job_run_as else WorkspaceClient(profile=profile)
    logging.basicConfig(level=logging.INFO)
    report = maintain_private_results(run=warehouse_runner(workspace, args.warehouse),
        catalog=args.private_catalog,
        retention_days=args.retention_days, limit=args.limit, dry_run=not args.apply)
    print(json.dumps(asdict(report), default=lambda value: value.isoformat()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
