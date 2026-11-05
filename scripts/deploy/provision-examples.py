"""DEPLOY-DEFAULT-ENVIRONMENT, CONTENT-EXAMPLE-PRELOAD — fill the default environment in ``uc_catalog``, as the
deployer.

Run by ``scripts/deploy/deploy.sh`` after the Apps start. The bundle has made the environment's schemas and their
grants (``databricks.yml``). This fills them (``shipped_sources.environment.fill``): the example measures and their
value sets in ``knowledge`` through the import (``CONTENT-IMPORT-MEASURES``) and the
synthetic small population in ``data`` (``DATA-SYNTH-SMALL``), from ``examples/`` (``shipped_sources.examples``,
``DEPLOY-SHIPPED-SOURCES``); the same content as files in each schema's ``files`` volume, first; and the catalog tags, default only if no catalog the
deployer can see is the default already. Then the two grants a bundle cannot make: ``USE CATALOG`` for the App users'
group, on a catalog the bundle does not hold, and ``MANAGE`` on ``draft_results`` for the maintenance principal, which
``deploy.sh`` creates.

It runs as the deployer: ``DATABRICKS_CONFIG_PROFILE`` names the profile (``deploy.sh`` sets it from
``databricks.local.yml``). Idempotent. Any failure exits non-zero, which fails the deploy.

    python scripts/deploy/provision-examples.py --catalog <c> --users-group <g> --maintenance-principal <id>
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

#: The period the example patients' data covers (`DATA-CONTRACT` validity tags on each data table).
EXAMPLE_VALID_FROM, EXAMPLE_VALID_TO = "2025-01-01", "2027-12-31"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True, help="the default environment's catalog (uc_catalog)")
    parser.add_argument("--users-group", required=True, help="app_users_group: USE CATALOG, to read the environment")
    parser.add_argument("--maintenance-principal", required=True,
                        help="the maintenance Job's principal: MANAGE on draft_results (RUN-CLIENT-MAINTENANCE)")
    parser.add_argument("--examples", type=Path, default=Path(__file__).resolve().parents[2] / "examples")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="    %(message)s")

    from cql_databricks.data_bindings.runner import spark_runner
    from databricks.connect import DatabricksSession
    from shipped_sources.environment import catalog_grant, fill, maintenance_grant
    from databricks.sdk import WorkspaceClient
    from shipped_sources.examples import load_examples, publish_example_files
    from shipped_sources.sources import SYNTHETIC_SMALL_DESCRIPTION, SYNTHETIC_SMALL_ENVIRONMENT

    profile = os.environ.get("DATABRICKS_CONFIG_PROFILE")
    builder = DatabricksSession.builder.profile(profile) if profile else DatabricksSession.builder
    spark = builder.serverless(True).getOrCreate()
    # DEPLOY-DEFAULT-ENVIRONMENT — the files before the tables, so the catalog tags are still written last.
    files = publish_example_files(WorkspaceClient(profile=profile).files, catalog=args.catalog, examples=args.examples)
    counts = fill(spark.sql, spark_runner(spark), catalog=args.catalog, name=SYNTHETIC_SMALL_ENVIRONMENT,
                  description=SYNTHETIC_SMALL_DESCRIPTION, default=True,
                  load=lambda: load_examples(spark, catalog=args.catalog, examples=args.examples,
                                             valid_from=EXAMPLE_VALID_FROM, valid_to=EXAMPLE_VALID_TO))
    spark.sql(catalog_grant(args.catalog, args.users_group))
    spark.sql(maintenance_grant(args.catalog, args.maintenance_principal))
    counts.update(files)
    print(f"==> DEPLOY-DEFAULT-ENVIRONMENT: {SYNTHETIC_SMALL_ENVIRONMENT} in {args.catalog}: "
          f"{', '.join(f'{k}={v}' for k, v in sorted(counts.items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
