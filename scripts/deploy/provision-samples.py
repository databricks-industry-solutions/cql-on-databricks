"""CONTENT-SAMPLE-PRELOAD, DATA-BIND-AUTO — the deploy's Unity Catalog provisioning, as the deployer.

Run by ``scripts/deploy/deploy.sh`` after the Apps start. Two idempotent steps:

1. the sample measures, their value sets and their patients from ``examples/`` (``content_ingest.samples``): the
   tagged knowledge and terminology in ``<catalog>.sample_measures`` and the data source ``sample-patients``;
2. the knowledge views in the runtime schema (``cql_databricks.data_bindings.bindings``), so a fresh deploy runs
   measures without a manual Bind.

Both run as the deployer: ``DATABRICKS_CONFIG_PROFILE`` names the profile (``deploy.sh`` sets it from
``databricks.local.yml``). Any failure exits non-zero, which fails the deploy.

    python scripts/deploy/provision-samples.py --catalog <c> --runtime-schema runtime --warehouse <id>
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

#: The period the sample patients' data covers (`DATA-CONTRACT` validity tags on each mount).
SAMPLE_VALID_FROM, SAMPLE_VALID_TO = "2025-01-01", "2027-12-31"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True, help="the runtime catalog (uc_catalog)")
    parser.add_argument("--runtime-schema", default="runtime")
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--examples", type=Path, default=Path(__file__).resolve().parents[2] / "examples")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="    %(message)s")

    from cql_databricks.compiler.config import resolve_catalog_scopes
    from cql_databricks.data_bindings import ContentResolver, warehouse_runner
    from cql_databricks.data_bindings.bindings import bind_knowledge_views
    from content_ingest.samples import load_samples
    from cql_databricks_common.workspace import service_principal_client
    from databricks.connect import DatabricksSession

    profile = os.environ.get("DATABRICKS_CONFIG_PROFILE")
    builder = DatabricksSession.builder.profile(profile) if profile else DatabricksSession.builder
    spark = builder.serverless(True).getOrCreate()
    counts = load_samples(spark, catalog=args.catalog, examples=args.examples,
                          valid_from=SAMPLE_VALID_FROM, valid_to=SAMPLE_VALID_TO)
    print(f"==> CONTENT-SAMPLE-PRELOAD: {', '.join(f'{k}={v}' for k, v in sorted(counts.items()))}")

    # Locally the shared client IS the deployer (`SEC-LOCAL-IDENTITY`): the profile's OAuth.
    w = service_principal_client()
    # CLIENT-DISCOVERY-SCOPE — the views union only the knowledge and terminology in the deployment's scopes
    # (deploy.sh exports CQL_DATABRICKS_CATALOG_SCOPES from the catalog_scopes bundle variable).
    content = ContentResolver(lambda: warehouse_runner(w, args.warehouse), resolve_catalog_scopes())
    views = bind_knowledge_views(w, args.catalog, args.runtime_schema, content, warehouse_id=args.warehouse)
    print(f"==> DATA-BIND-AUTO: bound {', '.join(views)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
