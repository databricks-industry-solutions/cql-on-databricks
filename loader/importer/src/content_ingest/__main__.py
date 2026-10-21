"""CLI entry point for content-ingest (CONTENT-INGEST, CONTENT-ECQI-USER-PACKAGE, CONTENT-ECQI-TERMS-GATE).

Two commands:

* ``ecqi <package>`` — import ONE eCQI measure package the user names: an eCQI package file name, or a zip they
  already hold. Shows the third-party terms and the package's own copyright, requires ``I AGREE`` (``ecqi.py``),
  then loads its Measure and Library resources unchanged into ``--schema`` and their value-set expansions from
  VSAC (with the user's own ``UMLS_API_KEY``) into ``--valueset-schema``, and tags both for the resolver.
* ``tag`` — tag ``--schema`` and ``--valueset-schema`` as they already are, and load nothing (for a load that
  predates tagging).

The sample measures are not loaded here: the deploy loads them from ``examples/`` (``samples.py``,
``CONTENT-SAMPLE-PRELOAD``). Synthetic patients come from the synthesizer (``loader/docs/synthetic-data.md``).

Usage:
    python -m content_ingest ecqi <package-file-name-or-zip>
    python -m content_ingest ecqi <package> --schema my_measures --valueset-schema my_vsac
    python -m content_ingest ecqi <package> --accept-terms        # non-interactive; the terms are still shown
    python -m content_ingest ecqi <package> --no-valuesets        # measures only; say so explicitly
    python -m content_ingest tag

Environment variables (exported by scripts/with-local-env.sh — SVC-LOCAL-CONFIG-BUNDLE):
    CONTENT_CATALOG       UC catalog (no default; from uc_catalog in databricks.local.yml)
    CONTENT_CACHE_DIR     local download directory (default: ~/.content-ingest/cache)
    SPARK_REMOTE          Spark Connect URL (omit for local/notebook session)
    UMLS_API_KEY          the user's own VSAC API key; required unless --no-valuesets
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from . import config
from .load import ensure_valueset_table, load, tag_loaded

log = logging.getLogger("content_ingest")

# Default schemas (CONTENT-IMPORT-TARGET-TAG): the user's imported measures, and their VSAC expansions apart from
# them, because the expansions come from VSAC, not the measure publisher (CONTENT-VSAC-AUTHORITY). `--schema` and
# `--valueset-schema` name other targets; either way the loaded schemas are tagged for the resolver.
DEFAULT_SCHEMA = "ecqi_measures"
DEFAULT_VALUESET_SCHEMA = "vsac"


def _get_spark():
    """Build or retrieve a SparkSession.

    Priority: SPARK_REMOTE (raw Spark Connect) > DATABRICKS_HOST (databricks-connect) > local."""
    remote = config.spark_remote()
    if remote:
        from pyspark.sql import SparkSession
        return SparkSession.builder.remote(remote).getOrCreate()
    if config.databricks_host():
        # The CQL on Databricks venv is --system-site-packages with homebrew pyspark visible alongside
        # databricks-connect.  databricks-connect's validation rejects this, but the system pyspark
        # is never used — only the connect session is.  Bypass the validation.
        import databricks.connect.validation as _dcv
        _dcv._validate_pyspark_installation = lambda: None
        from databricks.connect import DatabricksSession
        builder = DatabricksSession.builder
        if config.databricks_serverless():
            builder = builder.serverless(True)
        elif config.databricks_cluster_id():
            builder = builder.clusterId(config.databricks_cluster_id())
        return builder.getOrCreate()
    # Notebook / local session.
    from pyspark.sql import SparkSession
    return SparkSession.builder.appName("content-ingest").getOrCreate()


def _import_package(args, *, catalog: str | None) -> int:
    from .ecqi import TermsNotAccepted, acquire, valueset_urls
    from .transform import ValueSetExpansionRow, transform_resources

    api_key = None if args.no_valuesets else config.umls_api_key()
    if not args.no_valuesets and not api_key:
        log.error("UMLS_API_KEY is not set. Value-set expansions come from VSAC with your own key; set it, or pass "
                  "--no-valuesets to load the measures without their expansions")
        return 1
    try:
        path, resources = acquire(args.package, accept_terms=args.accept_terms,
                                  cache_dir=os.path.join(config.cache_dir(), "ecqi"))
    except TermsNotAccepted as e:
        log.error("%s", e)
        return 1

    package = os.path.basename(path)
    result = transform_resources(resources, source_package=package, source_year=0, source_lineage="ecqm")
    if api_key:
        from datetime import datetime, timezone

        from .vsac import fetch_referenced_valuesets
        now = datetime.now(timezone.utc).isoformat()
        result.valueset_expansions = [
            ValueSetExpansionRow(valueset_id=row["valueset_id"], system=row["system"], code=row["code"],
                                 display=row.get("display"), version=row.get("version"),
                                 source_url=row["valueset_id"], source_package="vsac", ingested_at=now)
            for row in fetch_referenced_valuesets(valueset_urls(resources), api_key=api_key)]
    log.info("%s: %d measures, %d libraries, %d value-set codes", package, len(result.measures),
             len(result.libraries), len(result.valueset_expansions))
    if args.dry_run:
        log.info("dry-run: nothing written")
        return 0

    spark = _get_spark()
    counts = load(spark, result, catalog=catalog, authority_schema=args.schema, valueset_schema=args.valueset_schema)
    ensure_valueset_table(spark, catalog=catalog, valueset_schema=args.valueset_schema)
    log.info("loaded into %s.{%s,%s}: %s", catalog, args.schema, args.valueset_schema,
             ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    # CONTENT-IMPORT-TARGET-TAG — declare what was loaded, so data-bindings finds it by tag. A --no-catalog load
    # is local Spark, where Unity Catalog tags do not exist: say so, loudly.
    if catalog:
        tag_loaded(spark, catalog=catalog, knowledge=(args.schema, args.knowledge_name or args.schema),
                   terminology=(args.valueset_schema, args.terminology_name or args.valueset_schema))
    else:
        log.warning("--no-catalog: nothing was tagged, so data-bindings cannot find this load")
    return 0


def main(argv: list[str] | None = None) -> int:
    config.require_local_env()
    parser = argparse.ArgumentParser(
        prog="content-ingest",
        description="Import an eCQI measure package you choose into Unity Catalog, behind its terms.")
    commands = parser.add_subparsers(dest="command", required=True)

    def targets(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--catalog", type=str, default=None,
                         help="UC catalog name (default: CONTENT_CATALOG env; there is no built-in name). "
                              "Use --no-catalog for local Spark (no catalog prefix).")
        sub.add_argument("--no-catalog", action="store_true",
                         help="Skip catalog prefix (for local Spark which only supports single-part namespaces)")
        # CONTENT-IMPORT-TARGET-TAG — the target is the user's to name; the baked-in names are defaults.
        sub.add_argument("--schema", type=str, default=DEFAULT_SCHEMA,
                         help=f"schema for measures and libraries (default: {DEFAULT_SCHEMA})")
        sub.add_argument("--valueset-schema", type=str, default=DEFAULT_VALUESET_SCHEMA,
                         help=f"schema for ValueSet expansions (default: {DEFAULT_VALUESET_SCHEMA})")
        sub.add_argument("--knowledge-name", type=str, default=None,
                         help="value of the cql_databricks_knowledge tag on --schema (default: the schema name)")
        sub.add_argument("--terminology-name", type=str, default=None,
                         help="value of the cql_databricks_terminology tag on --valueset-schema "
                              "(default: the schema name)")
        sub.add_argument("-v", "--verbose", action="store_true")

    ecqi = commands.add_parser("ecqi", help="import one eCQI measure package, behind its terms")
    ecqi.add_argument("package", help="an eCQI package file name (from the measure's eCQI page), or a local zip")
    ecqi.add_argument("--accept-terms", action="store_true",
                      help="accept the terms without a prompt, for a non-interactive run; the terms and the "
                           "package's copyright are still shown")
    ecqi.add_argument("--no-valuesets", action="store_true",
                      help="load the measures without their VSAC expansions")
    ecqi.add_argument("--dry-run", action="store_true", help="read and transform only; write nothing")
    targets(ecqi)
    targets(commands.add_parser("tag", help="tag --schema and --valueset-schema as they are; load nothing"))

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(name)s %(levelname)s  %(message)s")
    catalog: str | None = None if args.no_catalog else (args.catalog or config.catalog())
    if not args.no_catalog and not catalog:
        parser.error("no catalog: pass --catalog, --no-catalog for local Spark, or run under "
                     "scripts/with-local-env.sh, which sets CONTENT_CATALOG from databricks.local.yml")
    if args.command == "tag":
        tag_loaded(_get_spark(), catalog=catalog, knowledge=(args.schema, args.knowledge_name or args.schema),
                   terminology=(args.valueset_schema, args.terminology_name or args.valueset_schema))
        return 0
    return _import_package(args, catalog=catalog)


if __name__ == "__main__":
    sys.exit(main())
