"""DATA-SYNTH-LARGE, SYNTH-SEED-OUT — land synthesized cohorts as the Synthetic Population Large data source.

The I/O half of the Patient Data Synthesizer (generation is pure and offline; only this
step talks to a workspace). It lives in ``scripts/``, not in the synthesizer, because binding a
population as a data source is the same step a customer runs on their own data, and it is built
from the loader's seed contract and the compiler's mount contract: content writes populations
and depends on no other feature (``DEC-CONTENT-FEATURE``), so the step that needs those features
cannot live in content.

    python loader/scripts/synthetic-population/load.py --ndjson-dir <generate --out dir> --cohort <measure> \
        [--catalog <catalog>] [--profile <profile>]

    python loader/scripts/synthetic-population/load.py --functions-only [--catalog …] [--profile …]

``--functions-only`` lands nothing: it re-creates the retrieve functions over the mount tables already
landed, and creates the ``change_log`` table if it is missing, for when the layout contract moves
(``DATA-CHANGE-LOG-PERIODS`` added the ``change_log``) and the population itself has not. It also drops the
``retrieve_<Type>_ids`` twins an earlier load created (``BIND-RETRIEVE-SUBJECT``, retired).

Run it with the repository venv, which has the loader installed. Uploads the emitted ``<Type>.ndjson`` bulk-data files to a UC
Volume under ``cohorts/<label>/``, reshapes them onto the canonical mount contract
``(__subject_id, __resource)`` (``DATA-CONTRACT``), and provisions one
``retrieve_<Type>`` UDTF per type so the source binds through the only supported bind
path (``BIND-RETRIEVE-UDF``).

The population is CUMULATIVE across measures: every load rebuilds each mount from the
union of ALL cohort directories in the volume, so landing one measure's cohort beside
another's yields one population holding both. The volume is the source of truth
for what is in the population; re-landing a cohort label overwrites exactly that
cohort's files and nothing else. Cohorts must be generated with distinct seeds — the
patient id is ``pds-<seed>-<ordinal>``, so a reused seed would collide subjects across
measures.

Everything structural is the shared population lander's (``content_ingest.population.land``, ``PROC-DRY``),
the same one the sample patients land through (``DATA-SAMPLE-SOURCE``): the reshape, the ``__resource`` struct
from the importer's ``resource-schemas.json``, the empty mounts for types with no rows, the retrieve functions,
the change logs and the data-source declaration (``DATA-SOURCE-UC-TAG``). This script adds only what is
particular to a large population: the cohorts live in a UC Volume, and the mounts are rebuilt from all of them.

Terminology is NOT seeded: value sets are global (``DATA-SOURCE-SCOPE``), and every generated code is a verified
member of the same expansion the run binds (SYNTH-PDS-TERMINOLOGY).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

log = logging.getLogger("synthetic_population.load")

SEED_VOLUME = "seed"


def _source():
    from content_ingest.population import PopulationSource
    from content_ingest.sources import (
        SYNTHETIC_LARGE_DESCRIPTION, SYNTHETIC_LARGE_SCHEMA, SYNTHETIC_LARGE_SOURCE_ID)
    return PopulationSource(source_id=SYNTHETIC_LARGE_SOURCE_ID, schema=SYNTHETIC_LARGE_SCHEMA,
                            description=SYNTHETIC_LARGE_DESCRIPTION)


def _spark(profile: str | None):
    """A serverless Spark Connect session against the workspace the profile names."""
    from databricks.connect import DatabricksSession
    builder = DatabricksSession.builder.profile(profile) if profile else DatabricksSession.builder
    return builder.serverless(True).getOrCreate()


def _generated_types(ndjson_dir: str) -> list[str]:
    """The types the cohort actually emitted (a Patient file is the floor)."""
    from content_ingest.population import RESOURCE_TYPES
    present = [t for t in RESOURCE_TYPES
               if os.path.exists(os.path.join(ndjson_dir, f"{t}.ndjson"))]
    if "Patient" not in present:
        raise FileNotFoundError(f"{ndjson_dir}/Patient.ndjson not found — run `generate` first")
    return present


def _upload(ndjson_dir: str, cohort_path: str, generated: list[str]) -> None:
    # SEC-WS-CLIENT-SHARED: the workspace seam is the only WorkspaceClient constructor.
    # Profile selection reaches it via DATABRICKS_CONFIG_PROFILE (set by the CLI entry).
    from cql_databricks_common.workspace import service_principal_client
    w = service_principal_client()
    # The manifest and coverage report travel WITH the cohort (SYNTH-PDS-ORACLE): the
    # per-patient expected results are the provenance that distinguishes this source.
    provenance = [n for n in ("manifest.jsonl", "coverage.md")
                  if os.path.exists(os.path.join(ndjson_dir, n))]
    for name in [f"{t}.ndjson" for t in generated] + provenance:
        local = os.path.join(ndjson_dir, name)
        remote = f"{cohort_path}/{name}"
        with open(local, "rb") as f:
            w.files.upload(remote, f, overwrite=True)
        log.info("uploaded %s (%.1f MB)", remote, os.path.getsize(local) / 1e6)


def _landed_types(cohorts_path: str) -> set[str]:
    """The FHIR types present in ANY landed cohort directory — what the cumulative
    mounts must carry rows for."""
    from content_ingest.population import RESOURCE_TYPES
    from cql_databricks_common.workspace import service_principal_client
    w = service_principal_client()
    present: set[str] = set()
    for entry in w.files.list_directory_contents(cohorts_path):
        if not entry.is_directory:
            continue
        for file in w.files.list_directory_contents(entry.path):
            name = os.path.basename(file.path or "")
            if name.endswith(".ndjson"):
                present.add(name.removesuffix(".ndjson"))
    return present & set(RESOURCE_TYPES)


def functions_only(*, profile: str | None, catalog: str) -> list[str]:
    """Re-create every type's retrieve functions over the tables already landed, and the change log if it is
    missing; land nothing, so log nothing."""
    from cql_databricks.compiler.data_source import CHANGE_LOG, change_log_ddl
    from content_ingest.population import RESOURCE_TYPES, provision_functions

    spark = _spark(profile)
    source = _source()
    fqns = [provision_functions(spark, catalog, source, fhir_type) for fhir_type in RESOURCE_TYPES]
    spark.sql(change_log_ddl(catalog, source.schema))
    return [*fqns, f"{catalog}.{source.schema}.{CHANGE_LOG}"]


def load(*, profile: str | None, catalog: str, ndjson_dir: str, cohort: str) -> dict[str, int]:
    """Land one cohort under ``cohorts/<cohort>/`` and rebuild the cumulative mounts
    from every landed cohort. Returns per-type row counts (of the whole population)."""
    from content_ingest.population import land

    spark = _spark(profile)
    source = _source()
    schema_fqn = f"{catalog}.{source.schema}"
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema_fqn}")
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {schema_fqn}.{SEED_VOLUME}")

    cohorts_path = f"/Volumes/{catalog}/{source.schema}/{SEED_VOLUME}/cohorts"
    _upload(ndjson_dir, f"{cohorts_path}/{cohort}", _generated_types(ndjson_dir))
    raw = {fhir_type: spark.read.text(f"{cohorts_path}/*/{fhir_type}.ndjson")
           for fhir_type in sorted(_landed_types(cohorts_path))}
    return land(spark, catalog=catalog, source=source, raw_by_type=raw,
                valid_from="2026-01-01", valid_to="2026-12-31")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Land an emitted cohort into the cumulative Synthetic Population Large data source "
                    "(one cohort directory per measure).")
    parser.add_argument("--ndjson-dir",
                        help="the `python -m cql_databricks_synth generate --out` directory")
    parser.add_argument("--cohort",
                        help="cohort directory label in the seed volume (e.g. the measure key); "
                             "re-landing a label replaces exactly that cohort")
    parser.add_argument("--functions-only", action="store_true",
                        help="land nothing; re-create the retrieve functions over the landed tables")
    parser.add_argument("--catalog", default=os.environ.get("UC_CATALOG"),
                        help="UC catalog (default: $UC_CATALOG, set from databricks.local.yml by "
                             "scripts/with-local-env.sh)")
    parser.add_argument("--profile",
                        help="~/.databrickscfg profile (default: databricks.local.yml, then "
                             "DATABRICKS_CONFIG_PROFILE)")
    args = parser.parse_args(argv)
    if not args.functions_only and not (args.ndjson_dir and args.cohort):
        parser.error("--ndjson-dir and --cohort are required unless --functions-only")
    if not args.catalog:
        parser.error("no catalog: pass --catalog or run under scripts/with-local-env.sh, which sets "
                     "UC_CATALOG from databricks.local.yml")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from cql_databricks_common.databricks_profile import databricks_profile

    # The workspace seam (SEC-WS-CLIENT-SHARED) resolves credentials from ambient config; the
    # resolved profile selects it for both it and the Spark session.
    profile = databricks_profile(args.profile)
    if profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
    if args.functions_only:
        for fqn in functions_only(profile=profile, catalog=args.catalog):
            print(f"  {fqn}")
        return 0
    counts = load(profile=profile, catalog=args.catalog, ndjson_dir=args.ndjson_dir, cohort=args.cohort)
    for fhir_type, n in counts.items():
        print(f"  retrieve_{fhir_type}  {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
