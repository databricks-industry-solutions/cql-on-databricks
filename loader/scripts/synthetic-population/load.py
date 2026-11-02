"""DATA-SYNTH-LARGE, SYNTH-SEED-OUT — land synthesized cohorts as the Synthetic Population Large environment.

The I/O half of the Patient Data Synthesizer (generation is pure and offline; only this step talks to a workspace).
It lives in ``scripts/``, not in the synthesizer, because landing a population is the same step a customer runs on
their own data, and it is built from the shipped sources' lander (``DEPLOY-SHIPPED-SOURCES``).

    python loader/scripts/synthetic-population/load.py --ndjson-dir <generate --out dir> --cohort <measure> \
        --catalog <its own catalog> --users-group <group> --designer-app <app> [--profile <profile>]

The large population is an environment of its own, in its own catalog, never the default (``DATA-SYNTH-LARGE``,
``CLIENT-ENVIRONMENT``): this builds it there (``shipped_sources.environment.build``) with the example measures and
their value sets in its stores, so the measures run over it, and refuses the deployment's ``uc_catalog``, which is the
default environment's. Run it with the repository venv, which has ``shipped_sources`` installed. It uploads the
emitted ``<Type>.ndjson`` bulk-data files to a UC Volume in the environment's ``data_store``, under
``cohorts/<label>/``, gzipped as ``<Type>.ndjson.gz`` (the synthetic NDJSON shrinks about 38 times, so the upload is
not the slow step), and lands them on the contract ``(__subject_id, __resource)`` (``DATA-CONTRACT``).

The population is CUMULATIVE across measures: every load rebuilds each store table from the union of ALL cohort
directories in the volume, so landing one measure's cohort beside another's yields one population holding both. The
volume is the source of truth for what is in the population; re-landing a cohort label overwrites exactly that
cohort's files and nothing else. Cohorts must be generated with distinct seeds — the patient id is
``pds-<seed>-<ordinal>``, so a reused seed would collide subjects across measures.

Everything structural is the shared lander's (``shipped_sources.population.land``, ``PROC-DRY``), the same one the
example patients land through (``DATA-SYNTH-SMALL``). This script adds only what is particular to a large
population: the cohorts live in a UC Volume, and the store tables are rebuilt from all of them.
"""
from __future__ import annotations

import argparse
import gzip
import logging
import os
import shutil
import sys
import tempfile

log = logging.getLogger("synthetic_population.load")

SEED_VOLUME = "seed"
#: The one form a cohort's resources and manifest take in the volume (DATA-SYNTH-LARGE).
GZIP_SUFFIX = ".gz"


def _spark(profile: str | None):
    """A serverless Spark Connect session against the workspace the profile names."""
    from databricks.connect import DatabricksSession
    builder = DatabricksSession.builder.profile(profile) if profile else DatabricksSession.builder
    return builder.serverless(True).getOrCreate()


def _generated_types(ndjson_dir: str) -> list[str]:
    """The types the cohort actually emitted (a Patient file is the floor)."""
    from shipped_sources.population import RESOURCE_TYPES
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
    # The resources and the manifest are gzipped; the coverage report is a few KB and stays readable.
    packed = [f"{t}.ndjson" for t in generated] + \
        [n for n in ("manifest.jsonl",) if os.path.exists(os.path.join(ndjson_dir, n))]
    with tempfile.TemporaryDirectory() as scratch:
        for name in packed:
            local = os.path.join(ndjson_dir, name)
            gzipped = os.path.join(scratch, name + GZIP_SUFFIX)
            with open(local, "rb") as src, gzip.open(gzipped, "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            remote = f"{cohort_path}/{name}{GZIP_SUFFIX}"
            with open(gzipped, "rb") as f:
                w.files.upload(remote, f, overwrite=True)
            log.info("uploaded %s (%.1f MB, %.1f MB gzipped)", remote, os.path.getsize(local) / 1e6,
                     os.path.getsize(gzipped) / 1e6)
    if os.path.exists(os.path.join(ndjson_dir, "coverage.md")):
        with open(os.path.join(ndjson_dir, "coverage.md"), "rb") as f:
            w.files.upload(f"{cohort_path}/coverage.md", f, overwrite=True)


def _landed_types(cohorts_path: str) -> set[str]:
    """The FHIR types present in ANY landed cohort directory — what the cumulative
    mounts must carry rows for. A cohort landed uncompressed (by a load.py from before the gzip form) fails the
    load rather than drop out of the population unread."""
    from shipped_sources.population import RESOURCE_TYPES
    from cql_databricks_common.workspace import service_principal_client
    w = service_principal_client()
    present: set[str] = set()
    for entry in w.files.list_directory_contents(cohorts_path):
        if not entry.is_directory:
            continue
        for file in w.files.list_directory_contents(entry.path):
            name = os.path.basename(file.path or "")
            if name.endswith(".ndjson"):
                raise RuntimeError(f"{file.path} is uncompressed — re-land cohort {os.path.basename(entry.path)} "
                                   f"so it is stored as {name}{GZIP_SUFFIX}")
            if name.endswith(".ndjson" + GZIP_SUFFIX):
                present.add(name.removesuffix(".ndjson" + GZIP_SUFFIX))
    return present & set(RESOURCE_TYPES)


def load(*, profile: str | None, catalog: str, ndjson_dir: str, cohort: str, users_group: str,
         designer_app: str) -> dict[str, int]:
    """Land one cohort under ``cohorts/<cohort>/`` and rebuild the environment's store tables from every landed
    cohort, then its views, tags and grants. Returns per-type row counts (of the whole population)."""
    from pathlib import Path

    from cql_databricks.resolvers.environment import DATA_STORE
    from cql_databricks.data_bindings.runner import spark_runner
    from cql_databricks_common.workspace import service_principal_client
    from shipped_sources.environment import build, quote
    from shipped_sources.examples import import_example_measures
    from shipped_sources.population import land
    from shipped_sources.sources import SYNTHETIC_LARGE_DESCRIPTION, SYNTHETIC_LARGE_ENVIRONMENT

    designer = service_principal_client().apps.get(designer_app).service_principal_client_id
    if not designer:
        raise RuntimeError(f"the App {designer_app} has no service principal")
    spark = _spark(profile)
    cohorts_path = f"/Volumes/{catalog}/{DATA_STORE}/{SEED_VOLUME}/cohorts"
    examples = Path(__file__).resolve().parents[3] / "examples"

    def stores() -> dict[str, int]:
        spark.sql(f"CREATE VOLUME IF NOT EXISTS {quote(catalog, DATA_STORE, SEED_VOLUME)}")
        _upload(ndjson_dir, f"{cohorts_path}/{cohort}", _generated_types(ndjson_dir))
        raw = {fhir_type: spark.read.text(f"{cohorts_path}/*/{fhir_type}.ndjson{GZIP_SUFFIX}")
               for fhir_type in sorted(_landed_types(cohorts_path))}
        counts = import_example_measures(spark, catalog=catalog, examples=examples)
        counts.update(land(spark, catalog=catalog, raw_by_type=raw, valid_from="2026-01-01", valid_to="2026-12-31"))
        return counts

    return build(spark.sql, spark_runner(spark), catalog=catalog, name=SYNTHETIC_LARGE_ENVIRONMENT,
                 description=SYNTHETIC_LARGE_DESCRIPTION, default=False, load=stores, users=users_group,
                 content_readers=[designer])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Land an emitted cohort into the cumulative Synthetic Population Large environment "
                    "(one cohort directory per measure).")
    parser.add_argument("--ndjson-dir", required=True,
                        help="the `python -m cql_databricks_synth generate --out` directory")
    parser.add_argument("--cohort", required=True,
                        help="cohort directory label in the seed volume (e.g. the measure key); "
                             "re-landing a label replaces exactly that cohort")
    parser.add_argument("--catalog", required=True,
                        help="the environment's own catalog; never uc_catalog, the default environment's")
    parser.add_argument("--users-group", required=True,
                        help="the account group that reads the environment (app_users_group)")
    parser.add_argument("--designer-app", required=True,
                        help="the Designer App, whose service principal reads the environment's knowledge")
    parser.add_argument("--profile",
                        help="~/.databrickscfg profile (default: databricks.local.yml, then "
                             "DATABRICKS_CONFIG_PROFILE)")
    args = parser.parse_args(argv)
    if args.catalog.casefold() == (os.environ.get("UC_CATALOG") or "").casefold():
        parser.error(f"{args.catalog} is uc_catalog, the default environment's catalog; the large population is an "
                     "environment of its own, in its own catalog (DATA-SYNTH-LARGE)")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from cql_databricks_common.databricks_profile import databricks_profile

    # The workspace seam (SEC-WS-CLIENT-SHARED) resolves credentials from ambient config; the
    # resolved profile selects it for both it and the Spark session.
    profile = databricks_profile(args.profile)
    if profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
    counts = load(profile=profile, catalog=args.catalog, ndjson_dir=args.ndjson_dir, cohort=args.cohort,
                  users_group=args.users_group, designer_app=args.designer_app)
    for name, n in counts.items():
        print(f"  {name}  {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
