"""CONTENT-INCREMENTAL, DOC-OPEN-SAVE — the one MERGE that writes a knowledge row, keyed on FHIR ``(url, version)``.

The import stages rows in a Spark view; the Designer's Save stages one row as SQL parameters on a warehouse. Both
hand this the staged relation, so there is one statement for what a knowledge row is and how it is written. No
Spark import here: the Designer has none.

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

#: One knowledge row as SQL parameters, the staged relation a single-row write passes.
SINGLE_ROW = ("(SELECT :resource_json AS resource_json, :source_package AS source_package, "
              "CAST(:source_year AS INT) AS source_year, :source_lineage AS source_lineage, "
              ":ingested_at AS ingested_at)")


def knowledge_merge_sql(table: str, staged: str, *, unchanged_digest: bool = False, insert_only: bool = False) -> str:
    """The MERGE of ``staged`` into the knowledge ``table``: new versions insert, an existing version's payload and
    provenance are replaced.

    ``unchanged_digest`` (DOC-OPEN-SAVE) replaces an existing row only while its resource still hashes to the
    ``:resource_sha256`` parameter, so a save over a row someone else changed since it was read updates nothing. The
    caller reads ``num_updated_rows`` to tell.

    ``insert_only`` (DOC-OPEN-SAVE) never replaces a row: a new library saved into a store inserts, and one whose
    ``(url, version)`` is already there writes nothing. The caller reads ``num_inserted_rows`` to tell."""
    if unchanged_digest and insert_only:
        raise ValueError("a MERGE that never replaces a row has no replace to guard")
    guard = " AND sha2(to_json(target.`resource`), 256) = :resource_sha256" if unchanged_digest else ""
    replace = "" if insert_only else f"""
        WHEN MATCHED{guard} THEN UPDATE SET
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
