"""Spark I/O for the measure import tables (CONTENT-INCREMENTAL).

**The Lakehouse publishes standards** (``CONTENT-LAKEHOUSE-STANDARD-ONLY``): a table in the
authority schema is a standardized FHIR resource type or it does not belong there.  Each row is
the resource (as ``VARIANT``), its canonical identity, and its provenance — nothing else
(``CONTENT-NO-DERIVED-COLUMNS``).  Junctions, decoded CQL, and parsed identity are derived by the
consumer of the feed (``KNOWLEDGE-FEED-INGEST``), not stored here.

``VARIANT`` rather than a typed STRUCT (``CONTENT-FHIR-VARIANT``): FHIR's open content model —
``contained`` as an array of arbitrary resources, arbitrarily nested extensions, ~50-way
``value[x]`` choices — cannot be expressed in a closed STRUCT without either dropping content or
generating a schema calibrated to today's corpus.  VARIANT is lossless by construction and needs
no schema to maintain.

* ``<catalog>.<authority>.measure`` — FHIR Measure, Knowledge Source type=Measure
* ``<catalog>.<authority>.library`` — FHIR Library, Knowledge Source type=Library

The ``valueset`` table (tall ``valueset_id, system, code``, `DATA-ENVIRONMENT-LAYOUT`) lives
in the schema the caller names: beside the measures, or in a terminology
schema of its own that several measure sets share.  It is the
ONE deliberate exception to the FHIR-resource-shape rule: it is the ENGINE's binding contract, read
directly at run time, so it follows the engine's shape.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.types import StructType, StructField, StringType, IntegerType

from .knowledge_merge import knowledge_merge_sql
from .transform import (MeasureRow, LibraryRow, ValueSetExpansionRow,
                        TransformResult)

log = logging.getLogger("cql_databricks.data_bindings.measure_import.load")


# ── Staging schemas ──────────────────────────────────────────────────────────
# `resource` is staged as a JSON STRING and converted with `parse_json` at INSERT time, so the
# stored column is always a proper VARIANT.  `url`/`version` are projected from the SAME parsed
# expression in that INSERT, which is what keeps them from ever disagreeing with the payload.

_KNOWLEDGE_STAGING_SCHEMA = StructType([
    StructField("resource_json", StringType(), False),
    StructField("source_package", StringType(), True),
    StructField("source_year", IntegerType(), True),
    StructField("source_lineage", StringType(), True),
    StructField("ingested_at", StringType(), True),
])

VALUESET_SCHEMA = StructType([
    StructField("valueset_id", StringType(), False),
    StructField("system", StringType(), False),
    StructField("code", StringType(), False),
    StructField("display", StringType(), True),
    StructField("version", StringType(), True),
    StructField("source_url", StringType(), True),
    StructField("source_package", StringType(), True),
    StructField("ingested_at", StringType(), True),
])

# DDL for the knowledge tables.  `url`/`version` are plain columns rather than GENERATED ALWAYS AS:
# Databricks does not support column generation over VARIANT, so the guarantee that they match the
# payload comes from the INSERT projecting all three from one `parse_json` expression instead.
_KNOWLEDGE_TABLE_DDL = """\
  `url` STRING NOT NULL,
  `version` STRING NOT NULL,
  `resource` VARIANT NOT NULL,
  `source_package` STRING,
  `source_year` INT,
  `source_lineage` STRING,
  `ingested_at` STRING"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _qualify(catalog: str | None, schema: str, table: str) -> str:
    """Backtick-quoted, optionally-catalog-qualified table name."""
    parts = ([catalog] if catalog else []) + [schema, table]
    return ".".join(f"`{p}`" for p in parts)


def _ensure_schema(spark: SparkSession, catalog: str | None, schema: str) -> None:
    parts = ([catalog] if catalog else []) + [schema]
    ns = ".".join(f"`{p}`" for p in parts)
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {ns}")


class DeltaFeaturesError(RuntimeError):
    """Raised when a knowledge or value-set table cannot take the Delta features its incremental merge reads."""


def _enable_delta_features(spark: SparkSession, fqtn: str) -> None:
    """Enable Row Tracking, Change Data Feed and Deletion Vectors on a newly created table.

    A table that refuses them (not Delta, or not writable) raises, naming the table
    (CORE-NO-SILENT-MISLOWER): the merge that follows depends on them."""
    try:
        spark.sql(
            f"ALTER TABLE {fqtn} SET TBLPROPERTIES ("
            f"  'delta.enableRowTracking' = 'true',"
            f"  'delta.enableChangeDataFeed' = 'true',"
            f"  'delta.enableDeletionVectors' = 'true'"
            f")"
        )
    except Exception as e:
        raise DeltaFeaturesError(f"could not enable Delta features on {fqtn}: {e}") from e


class StaleKnowledgeTableError(RuntimeError):
    """Raised when a knowledge table predates ``CONTENT-FHIR-VARIANT`` and must be re-ingested."""


def _assert_variant_schema(spark: SparkSession, fqtn: str) -> None:
    """Fail loudly if ``fqtn`` predates the VARIANT shape (CORE-NO-SILENT-MISLOWER).

    ``CREATE TABLE IF NOT EXISTS`` is a no-op against an existing table, so without this check a
    pre-VARIANT table survives and the MERGE fails later with ``DELTA_MERGE_UNRESOLVED_EXPRESSION``
    — an error that names a missing column but not the actual cause.  Dropping the table is left to
    the operator: it is destructive, and these tables are cheap to rebuild but not to un-delete.
    """
    fields = {f.name: f.dataType for f in spark.table(fqtn).schema.fields}
    resource = fields.get("resource")
    if resource is not None and "variant" in str(resource).lower():
        return
    raise StaleKnowledgeTableError(
        f"{fqtn} has the pre-VARIANT schema (columns: {', '.join(sorted(fields))}).\n"
        f"The knowledge tables are 100% derived from public IG packages, so the migration to "
        f"CONTENT-FHIR-VARIANT is a re-ingest, not a backfill. Drop the table and re-run:\n"
        f"    DROP TABLE {fqtn};\n"
        f"See compiler/client/src/cql_databricks/data_bindings/README.md. The retired `measure_library` and "
        f"`valueset_ref` tables in the same schema can be dropped at the same time."
    )


def ensure_knowledge_table(spark: SparkSession, fqtn: str) -> None:
    """Create a knowledge table with a ``VARIANT`` resource column (DEPLOY-LAKEBASE-SCHEMA)."""
    if spark.catalog.tableExists(fqtn):
        _assert_variant_schema(spark, fqtn)
        return
    spark.sql(
        f"CREATE TABLE {fqtn} (\n{_KNOWLEDGE_TABLE_DDL}\n)\n"
        f"TBLPROPERTIES (\n"
        f"  'delta.enableRowTracking' = 'true',\n"
        f"  'delta.enableChangeDataFeed' = 'true',\n"
        f"  'delta.enableDeletionVectors' = 'true'\n"
        f")"
    )
    log.info("created %s", fqtn)


def _merge_knowledge_rows(spark: SparkSession, df: DataFrame, fqtn: str) -> int:
    """MERGE knowledge rows into a VARIANT table, keyed on FHIR canonical ``(url, version)``.

    Keying on the FHIR canonical identity (rather than a derived one) is what makes re-ingest
    idempotent: new versions insert, re-ingested versions refresh the payload and provenance.
    """
    count = df.count()
    if count == 0:
        return 0

    ensure_knowledge_table(spark, fqtn)

    view = f"__measure_import_stage_{abs(hash(fqtn)) % 10**8}"
    df.createOrReplaceTempView(view)
    spark.sql(knowledge_merge_sql(fqtn, view))
    spark.catalog.dropTempView(view)
    log.info("merged %d rows into %s", count, fqtn)
    return count


def _replace_valuesets(spark: SparkSession, df: DataFrame, fqtn: str) -> int:
    """Replace each value set in ``df`` whole: delete its earlier codes, then append (CONTENT-INCREMENTAL).

    Keyed on ``valueset_id``, not on the import that wrote it, so two imports that share a value set keep one
    copy of its codes, and an import never deletes a value set it did not bring."""
    count = df.count()
    if count == 0:
        return 0
    if not spark.catalog.tableExists(fqtn):
        df.write.mode("overwrite").saveAsTable(fqtn)
        _enable_delta_features(spark, fqtn)
        log.info("created %s with %d rows", fqtn, count)
        return count

    view = f"__measure_import_valuesets_{abs(hash(fqtn)) % 10**8}"
    df.select("valueset_id").distinct().createOrReplaceTempView(view)
    spark.sql(f"MERGE INTO {fqtn} AS target USING {view} AS source "
              f"ON target.valueset_id = source.valueset_id WHEN MATCHED THEN DELETE")
    spark.catalog.dropTempView(view)
    df.write.mode("append").saveAsTable(fqtn)
    log.info("replaced %d rows in %s", count, fqtn)
    return count


def _knowledge_dataframe(spark: SparkSession, rows: list[MeasureRow] | list[LibraryRow]) -> DataFrame:
    """Stage knowledge rows — the resource as JSON text, plus provenance."""
    data = [(r.resource_json, r.source_package, r.source_year, r.source_lineage, r.ingested_at)
            for r in rows]
    return spark.createDataFrame(data, _KNOWLEDGE_STAGING_SCHEMA)


# ── Public API ───────────────────────────────────────────────────────────────

def load(spark: SparkSession, result: TransformResult, *,
         catalog: str | None, authority_schema: str,
         valueset_schema: str) -> dict[str, int]:
    """Write a TransformResult into the authority's schema.  Returns row counts per table.

    ``catalog`` is the UC catalog; ``authority_schema`` is the content
    schema (``knowledge_store`` in an environment, `DATA-ENVIRONMENT-LAYOUT`).
    Together they produce ``<catalog>.<schema>.measure``, etc.

    ValueSet expansions are written to ``valueset_schema`` (``knowledge_store`` in an environment,
    `DATA-ENVIRONMENT-LAYOUT`).
    """
    _ensure_schema(spark, catalog, authority_schema)
    counts: dict[str, int] = {}

    if result.measures:
        df = _knowledge_dataframe(spark, result.measures)
        fqtn = _qualify(catalog, authority_schema, "measure")
        counts["measure"] = _merge_knowledge_rows(spark, df, fqtn)

    if result.libraries:
        df = _knowledge_dataframe(spark, result.libraries)
        fqtn = _qualify(catalog, authority_schema, "library")
        counts["library"] = _merge_knowledge_rows(spark, df, fqtn)

    if result.valueset_expansions:
        _ensure_schema(spark, catalog, valueset_schema)
        rows = [tuple(asdict(vs).values()) for vs in result.valueset_expansions]
        df = spark.createDataFrame(rows, VALUESET_SCHEMA)
        fqtn = _qualify(catalog, valueset_schema, "valueset")
        counts["valueset"] = _replace_valuesets(spark, df, fqtn)

    return counts


def ensure_valueset_table(spark: SparkSession, *, catalog: str | None,
                          valueset_schema: str) -> str:
    """Ensure the ``valueset`` table exists in ``valueset_schema`` (empty if no
    expansions loaded yet).  Returns the fully-qualified table name.  Allows a Knowledge
    Location to point to it immediately."""
    _ensure_schema(spark, catalog, valueset_schema)
    fqtn = _qualify(catalog, valueset_schema, "valueset")
    if not spark.catalog.tableExists(fqtn):
        empty = spark.createDataFrame([], VALUESET_SCHEMA)
        empty.write.saveAsTable(fqtn)
        _enable_delta_features(spark, fqtn)
        log.info("created empty valueset table %s", fqtn)
    return fqtn
