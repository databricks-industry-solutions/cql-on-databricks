"""CONTENT-INCREMENTAL — the one MERGE that writes a knowledge row, keyed on FHIR ``(url, version)``, for the deploy's
writer of the ``knowledge`` tables (`knowledge_tables.py`), which stages its rows in a Spark view.

A staged relation has the columns ``resource_json, source_package, source_year, source_lineage, ingested_at``.
"""
from __future__ import annotations

#: The MERGE source: `url`, `version` and `resource` all projected from ONE `parse_json`, which is what keeps the
#: identity columns from ever drifting from the payload they describe.
#:
#: `variant_get` rather than the `resource:url` path operator: `:` is Databricks-only SQL and fails to parse on OSS
#: Spark, so the portable form is the only form (one path, per design-simplicity).
_SOURCE = """(
    SELECT
      variant_get(parsed, '$.url', 'string')     AS url,
      variant_get(parsed, '$.version', 'string') AS version,
      parsed                                     AS resource,
      source_package, source_year, source_lineage, ingested_at
    FROM (
      SELECT parse_json(resource_json) AS parsed,
             source_package, source_year, source_lineage, ingested_at
      FROM {staged}
    )
)"""

def knowledge_merge_sql(table: str, staged: str) -> str:
    """The MERGE of ``staged`` into the knowledge ``table``: new versions insert, an existing version's payload and
    provenance are replaced, so the deploy can load the same content again (`CONTENT-INCREMENTAL`)."""
    replace = """
        WHEN MATCHED THEN UPDATE SET
          target.`resource` = source.`resource`,
          target.`source_package` = source.`source_package`,
          target.`source_year` = source.`source_year`,
          target.`source_lineage` = source.`source_lineage`,
          target.`ingested_at` = source.`ingested_at`"""
    return f"""
        MERGE INTO {table} AS target
        USING {_SOURCE.format(staged=staged)} AS source
        ON target.url = source.url AND target.version = source.version{replace}
        WHEN NOT MATCHED THEN INSERT (
          `url`, `version`, `resource`,
          `source_package`, `source_year`, `source_lineage`, `ingested_at`
        ) VALUES (
          source.`url`, source.`version`, source.`resource`,
          source.`source_package`, source.`source_year`, source.`source_lineage`,
          source.`ingested_at`
        )
    """
