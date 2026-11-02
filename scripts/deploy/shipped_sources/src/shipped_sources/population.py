"""DATA-ENVIRONMENT-LAYOUT, DATA-CONTRACT — land a FHIR bulk-data population as an environment's data.

One path for every population this repository ships: the small synthesized population (``DATA-SYNTH-SMALL``, from
``examples/data``, the default environment's data) and the large one (``DATA-SYNTH-LARGE``, an environment of its
own). A caller hands over one raw NDJSON text frame per FHIR type that has data (a ``value`` column, one resource per
row), and this module:

- reshapes each onto the contract ``(__subject_id, __resource)`` (``BIND-CONTRACT``) through the client's reshape
  (``BIND-RESHAPE``), with the ``__resource`` struct taken from this package's ``resource-schemas.json``, the
  same static model the compiler's ``/resource-schema`` serves (``SVC-RESOURCE-SCHEMA``);
- lands every bound type as a store table ``<catalog>.data_store.<type>``, EMPTY when the population has no rows of
  that type, so a retrieve of any bound type reads a view that exists and returns no rows rather than failing;
- creates each type's published view ``<catalog>.data.<type>`` over its store table (``BIND-PUBLISHED-VIEW``);
- logs the reload in the change log (``DATA-CHANGE-LOG-PERIODS``).

The environment's schemas, its other views, its tags and its grants are ``environment.build``'s.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("shipped_sources.population")

#: The bound type set: every population lands a store table and a view for each.
RESOURCE_TYPES = ["Patient", "Condition", "Encounter", "Observation", "Procedure",
                  "Coverage", "Medication", "MedicationRequest",
                  "MedicationAdministration", "DeviceRequest", "ServiceRequest", "Immunization"]


def load_resource_schemas() -> dict[str, dict]:
    """This package's own ``resource-schemas.json`` (``CONTENT-IMPORTER-FHIR-SCHEMAS``): ``{Type: {modeled,
    patientContext, ddl}}``, the static FHIR model the compiler's ``/resource-schema`` endpoint serves."""
    with open(os.path.join(os.path.dirname(__file__), "resource-schemas.json")) as f:
        return json.load(f)


def resource_ddl(fhir_type: str) -> str:
    """The engine's ``__resource`` struct type for ``fhir_type``, from ``resource-schemas.json``."""
    entry = load_resource_schemas().get(fhir_type)
    if not entry or not entry.get("modeled") or not entry.get("ddl"):
        raise ValueError(f"{fhir_type} is not a modeled FHIR type in resource-schemas.json")
    ddl = entry["ddl"]
    return ddl if ddl.strip().upper().startswith("STRUCT<") else f"STRUCT<{ddl}>"


def store_table(catalog: str, fhir_type: str) -> str:
    """DATA-ENVIRONMENT-LAYOUT — ``fhir_type``'s store table, named for the type in lower case."""
    from .environment import quote
    from cql_databricks.compiler.environment import DATA_STORE
    return quote(catalog, DATA_STORE, fhir_type.lower())


def land(spark, *, catalog: str, raw_by_type: dict, valid_from: str, valid_to: str) -> dict[str, int]:
    """Land ``raw_by_type`` (FHIR type -> raw NDJSON text frame with a ``value`` column) as the data of the
    environment in ``catalog``, whose schemas exist (``environment.build``). Every bound type gets a store table and a
    view, the table empty when the type is absent from ``raw_by_type``. Returns per-type row counts.
    ``valid_from``/``valid_to`` are the period the population's data covers (`DATA-CONTRACT`)."""
    from cql_databricks.compiler.reshape import json_frame, schema_from_ddl

    from .environment import change_log_all_statement, change_log_statements, data_view
    from .seed_fhir import contract

    unknown = sorted(set(raw_by_type) - set(RESOURCE_TYPES))
    if unknown:
        raise ValueError(f"no store table for FHIR types {unknown}; bound types are {RESOURCE_TYPES}")
    if "Patient" not in raw_by_type:
        raise ValueError("a population needs Patient rows")

    counts: dict[str, int] = {}
    for fhir_type in RESOURCE_TYPES:
        ddl = resource_ddl(fhir_type)
        raw = raw_by_type.get(fhir_type)
        if raw is None:
            raw = spark.createDataFrame([], "value STRING")
        schema = schema_from_ddl(fhir_type, ddl, patient_context=fhir_type != "Patient")
        frame = json_frame(spark, raw, schema, column="value")
        table = store_table(catalog, fhir_type)
        frame.write.mode("overwrite").saveAsTable(table)

        props = contract.cql_tblproperties(fhir_type, valid_from=valid_from, valid_to=valid_to)
        kv = ", ".join(f"'{k}' = '{v}'" for k, v in props.items())
        spark.sql(f"ALTER TABLE {table} SET TBLPROPERTIES ({kv})")
        counts[fhir_type] = spark.table(table).count()
        log.info("landed %s (%d rows)", table, counts[fhir_type])
        spark.sql(data_view(catalog, fhir_type))

    # DATA-CHANGE-LOG-PERIODS — a load rebuilds every store table, so every open evaluation set changed: one row with
    # an empty map, after the tables, so a refresh that reads it sees the new data.
    for statement in change_log_statements(catalog):
        spark.sql(statement)
    spark.sql(change_log_all_statement(catalog))
    return counts
