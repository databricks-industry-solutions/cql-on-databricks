"""Spark I/O for the content-ingest tables (CONTENT-CATALOG-LAYOUT, CONTENT-INCREMENTAL).

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

The ``valueset`` table (tall ``valueset_id, system, code`` matching BIND-TERMINOLOGY-TABLE) lives
in its own authority schema (``vsac`` by default) because the expansions come from the NLM Value
Set Authority Center, not the measure-publishing authority (``CONTENT-VSAC-AUTHORITY``).  It is the
ONE deliberate exception to the FHIR-resource-shape rule: it is the ENGINE's binding contract, read
directly at run time, so it follows the engine's shape.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.types import StructType, StructField, StringType, IntegerType

from .transform import (MeasureRow, LibraryRow, ValueSetExpansionRow,
                        TransformResult)

log = logging.getLogger("content_ingest.load")


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


def _enable_delta_features(spark: SparkSession, fqtn: str) -> None:
    """Enable Change Data Feed and Row Tracking on a newly created table."""
    try:
        spark.sql(
            f"ALTER TABLE {fqtn} SET TBLPROPERTIES ("
            f"  'delta.enableRowTracking' = 'true',"
            f"  'delta.enableChangeDataFeed' = 'true',"
            f"  'delta.enableDeletionVectors' = 'true'"
            f")"
        )
    except Exception as e:
        log.debug("could not enable delta features on %s (non-Delta?): %s", fqtn, e)


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


# The MERGE source: `url`, `version` and `resource` all projected from ONE `parse_json`, which is
# what keeps the identity columns from ever drifting from the payload they describe.
#
# `variant_get` rather than the `resource:url` path operator: `:` is Databricks-only SQL and fails
# to parse on OSS Spark, so the portable form is the only form (one path, per design-simplicity).
_KNOWLEDGE_SOURCE_SQL = """(
    SELECT
      variant_get(parsed, '$.url', 'string')     AS url,
      variant_get(parsed, '$.version', 'string') AS version,
      parsed                                     AS resource,
      source_package, source_year, source_lineage, ingested_at
    FROM (
      SELECT parse_json(resource_json) AS parsed,
             source_package, source_year, source_lineage, ingested_at
      FROM {view}
    )
)"""


def _merge_knowledge_rows(spark: SparkSession, df: DataFrame, fqtn: str) -> int:
    """MERGE knowledge rows into a VARIANT table, keyed on FHIR canonical ``(url, version)``.

    Keying on the FHIR canonical identity (rather than a derived one) is what makes re-ingest
    idempotent: new versions insert, re-ingested versions refresh the payload and provenance.
    """
    count = df.count()
    if count == 0:
        return 0

    ensure_knowledge_table(spark, fqtn)

    view = f"__content_ingest_stage_{abs(hash(fqtn)) % 10**8}"
    df.createOrReplaceTempView(view)
    source = _KNOWLEDGE_SOURCE_SQL.format(view=view)
    spark.sql(f"""
        MERGE INTO {fqtn} AS target
        USING {source} AS source
        ON target.url = source.url AND target.version = source.version
        WHEN MATCHED THEN UPDATE SET
          target.`resource` = source.`resource`,
          target.`source_package` = source.`source_package`,
          target.`source_year` = source.`source_year`,
          target.`source_lineage` = source.`source_lineage`,
          target.`ingested_at` = source.`ingested_at`
        WHEN NOT MATCHED THEN INSERT (
          `url`, `version`, `resource`,
          `source_package`, `source_year`, `source_lineage`, `ingested_at`
        ) VALUES (
          source.`url`, source.`version`, source.`resource`,
          source.`source_package`, source.`source_year`, source.`source_lineage`,
          source.`ingested_at`
        )
    """)
    spark.catalog.dropTempView(view)
    log.info("merged %d rows into %s", count, fqtn)
    return count


def _overwrite_by_package(spark: SparkSession, df: DataFrame, fqtn: str) -> int:
    """Delete-then-append rows for each ``source_package`` present in ``df``."""
    count = df.count()
    if count == 0:
        return 0
    if not spark.catalog.tableExists(fqtn):
        df.write.mode("overwrite").saveAsTable(fqtn)
        _enable_delta_features(spark, fqtn)
        log.info("created %s with %d rows", fqtn, count)
        return count

    packages = {row[0] for row in df.select("source_package").distinct().collect()}
    for pkg in packages:
        spark.sql(f"DELETE FROM {fqtn} WHERE source_package = '{pkg}'")
    df.write.mode("append").saveAsTable(fqtn)
    log.info("refreshed %d rows in %s", count, fqtn)
    return count


def _knowledge_dataframe(spark: SparkSession, rows: list[MeasureRow] | list[LibraryRow]) -> DataFrame:
    """Stage knowledge rows — the resource as JSON text, plus provenance."""
    data = [(r.resource_json, r.source_package, r.source_year, r.source_lineage, r.ingested_at)
            for r in rows]
    return spark.createDataFrame(data, _KNOWLEDGE_STAGING_SCHEMA)


# ── Public API ───────────────────────────────────────────────────────────────

def load(spark: SparkSession, result: TransformResult, *,
         catalog: str | None, authority_schema: str,
         valueset_schema: str = "vsac") -> dict[str, int]:
    """Write a TransformResult into the authority's schema.  Returns row counts per table.

    ``catalog`` is the UC catalog; ``authority_schema`` is the content
    schema (``sample_measures`` for the samples, ``ecqi_measures`` by default for an eCQI import).
    Together they produce ``<catalog>.<schema>.measure``, etc.

    ValueSet expansions are written to ``valueset_schema`` (default ``vsac``) — a separate
    authority because they originate from the NLM Value Set Authority Center, not the measure
    publisher (``CONTENT-VSAC-AUTHORITY``).
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
        counts["valueset"] = _overwrite_by_package(spark, df, fqtn)

    return counts


def ensure_valueset_table(spark: SparkSession, *, catalog: str | None,
                          valueset_schema: str = "vsac") -> str:
    """Ensure the ``valueset`` table exists in the VSAC authority schema (empty if no
    expansions loaded yet).  Returns the fully-qualified table name.  Allows a Knowledge
    Location to point to it immediately (CONTENT-VSAC-AUTHORITY)."""
    _ensure_schema(spark, catalog, valueset_schema)
    fqtn = _qualify(catalog, valueset_schema, "valueset")
    if not spark.catalog.tableExists(fqtn):
        empty = spark.createDataFrame([], VALUESET_SCHEMA)
        empty.write.saveAsTable(fqtn)
        _enable_delta_features(spark, fqtn)
        log.info("created empty valueset table %s", fqtn)
    return fqtn


def tag_loaded(spark: SparkSession, *, catalog: str | None,
               knowledge: tuple[str, str] | None,
               terminology: tuple[str, str] | None) -> list[str]:
    """CONTENT-IMPORT-TARGET-TAG — declare loaded schemas so data-bindings finds them by tag.

    ``knowledge`` and ``terminology`` are ``(schema, tag value)``; None skips that one. A schema is a
    knowledge source once tagged ``cql_databricks_knowledge`` (``library`` + ``measure``) and a
    terminology source once tagged ``cql_databricks_terminology`` (``valueset``). Returns the statements
    run. Tags are a Unity Catalog feature, so a load with no catalog (local Spark) cannot be tagged, and
    asking it to be is an error rather than a load nobody can find.
    """
    from cql_databricks.data_bindings import KNOWLEDGE_TAG, TERMINOLOGY_TAG, tag_statement

    wanted = [(KNOWLEDGE_TAG, knowledge), (TERMINOLOGY_TAG, terminology)]
    wanted = [(tag, target) for tag, target in wanted if target is not None]
    if wanted and not catalog:
        raise ValueError("tagging needs a Unity Catalog catalog; this load has none (--no-catalog). "
                         "A schema with no tag is invisible to data-bindings.")
    statements = [tag_statement(tag, name, catalog, schema) for tag, (schema, name) in wanted]
    for statement in statements:
        log.info("tag: %s", statement)
        spark.sql(statement)
    return statements
