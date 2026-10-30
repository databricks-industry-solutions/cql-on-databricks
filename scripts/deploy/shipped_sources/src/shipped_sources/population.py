"""DATA-SOURCE-UC-TAG, DATA-CONTRACT — land a FHIR bulk-data population as a data source.

One path for every population this repository ships: the small synthesized population (``DATA-SYNTH-SMALL``, from
``examples/data``) and the large one (``DATA-SYNTH-LARGE``). A caller hands over one raw
NDJSON text frame per FHIR type that has data (a ``value`` column, one resource per row), and this module:

- reshapes each onto the mount contract ``(__subject_id, __resource)`` through the client's reshape
  (``BIND-RESHAPE``), with the ``__resource`` struct taken from this package's ``resource-schemas.json``, the
  same static model the compiler's ``/resource-schema`` serves (``SVC-RESOURCE-SCHEMA``);
- lands every bound type as a mount table, EMPTY when the population has no rows of that type: a measure's
  bind manifest covers every type its compiled closure retrieves, and an unbound type fails the plan
  (``UNRESOLVABLE_TABLE_VALUED_FUNCTION``) rather than returning less data;
- creates one ``retrieve_<Type>`` UDTF per type on the engine's parameter contract (``BIND-RETRIEVE-UDF``);
- creates the change logs (``DATA-CHANGE-LOG``, ``DATA-CHANGE-LOG-PERIODS``) and, last, declares the schema a
  data source (``DATA-SOURCE-UC-TAG``).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

log = logging.getLogger("shipped_sources.population")

#: The bound type set: every population lands a mount for each, so any measure that binds one binds all.
RESOURCE_TYPES = ["Patient", "Condition", "Encounter", "Observation", "Procedure",
                  "Coverage", "Medication", "MedicationRequest",
                  "MedicationAdministration", "DeviceRequest", "ServiceRequest", "Immunization"]

#: The retrieve parameter contract (BIND-RETRIEVE-UDF): named so the emitted SQL binds by name. Every mount
#: declares EXACTLY this list, in this order: the engine binds by name, but the parameter TYPES are the contract
#: and a mount that declares them differently fails to resolve the call. The DEFAULTs make the contract
#: additive: the engine omits an argument it knows nothing about statically, and the body reads the declared
#: default. `DEFAULT array()` / `DEFAULT map()` are the engine's own spelling and type-check as written
#: (verified against DBSQL, 2026-08); do NOT "fix" them into explicit CASTs. Every parameter
#: is ADVISORY pushdown (BIND-CAPABILITY): the compiled plan re-applies every predicate above the call, so a mount
#: that ignores `value_set` / `codes` / the date window still returns a correct superset. None names a library
#: (EMIT-ROOT-INDEPENDENT): a mount's rows depend only on its arguments and its data.
RETRIEVE_PARAMS_DDL = (
    "`value_set` STRING DEFAULT NULL, "
    "`code_property` STRING DEFAULT NULL, "
    "`codes` ARRAY<STRUCT<system STRING, code STRING>> DEFAULT array(), "
    "`date_property` STRING DEFAULT NULL, "
    "`date_low` STRING DEFAULT NULL, "
    "`date_high` STRING DEFAULT NULL, "
    "`parameters` MAP<STRING, VARIANT> DEFAULT map()"
)

#: BIND-RETRIEVE-SUBJECT is retired. Earlier loads created a `retrieve_<Type>_ids` twin beside every
#: `retrieve_<Type>`; provisioning drops them, because the data-source layout check reads each as an unmodeled
#: type and rejects the schema. Remove this, and the drop that uses it, once no workspace holds one.
RETIRED_SUBJECT_IDS_SUFFIX = "_ids"


@dataclass(frozen=True)
class PopulationSource:
    """A data source a population lands as: its name (the tag value, part of every result's address), the
    schema that holds its mounts, and the description callers see (`DATA-SOURCE-DESCRIPTION`)."""
    source_id: str
    schema: str
    description: str


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


def mount_table(catalog: str, schema: str, fhir_type: str) -> str:
    from .seed_fhir import contract
    return f"{catalog}.{schema}.{contract.retrieve_table_name(fhir_type).lower()}_data"


def retrieve_udtf_ddl(fqn: str, fhir_type: str, source_table: str, columns_ddl: str,
                      description: str) -> str:
    """A retrieve UDTF over the whole mount: one population, every row in scope for every measure."""
    comment = f"{description}: retrieve UDTF for FHIR {fhir_type}, from {source_table}."
    return f"""\
CREATE OR REPLACE FUNCTION {fqn}({RETRIEVE_PARAMS_DDL})
RETURNS TABLE ({columns_ddl})
LANGUAGE SQL
COMMENT '{comment.replace("'", "''")}'
RETURN SELECT `__subject_id`, `__resource` FROM {source_table}
"""


def provision_functions(spark, catalog: str, source: PopulationSource, fhir_type: str) -> str:
    """Create ``retrieve_<Type>`` over the landed mount table and return its FQN. The return type is read from
    the table, so the function matches what landed."""
    table = mount_table(catalog, source.schema, fhir_type)
    struct_ddl = spark.table(table).schema["__resource"].dataType.simpleString()
    columns_ddl = f"`__subject_id` STRING, `__resource` {struct_ddl}"
    fqn = f"{catalog}.{source.schema}.retrieve_{fhir_type}"
    spark.sql(retrieve_udtf_ddl(fqn, fhir_type, table, columns_ddl, source.description))
    log.info("provisioned %s", fqn)
    spark.sql(f"DROP FUNCTION IF EXISTS {fqn}{RETIRED_SUBJECT_IDS_SUFFIX}")
    return fqn


def land(spark, *, catalog: str, source: PopulationSource, raw_by_type: dict,
         valid_from: str, valid_to: str) -> dict[str, int]:
    """Land ``raw_by_type`` (FHIR type -> raw NDJSON text frame with a ``value`` column) as ``source`` and
    declare it. Every bound type gets a mount, empty when absent from ``raw_by_type``. Returns per-type row
    counts. ``valid_from``/``valid_to`` are the period the population's data covers (`DATA-CONTRACT`)."""
    from cql_databricks.compiler.data_source import change_log_all_statement, change_log_ddl
    from cql_databricks.compiler.reshape import json_frame, schema_from_ddl
    from cql_databricks.data_bindings import declaration_statements

    from .change_data_log import change_data_log_ddl
    from .sources import change_data_log_fqn
    from .seed_fhir import contract

    unknown = sorted(set(raw_by_type) - set(RESOURCE_TYPES))
    if unknown:
        raise ValueError(f"{source.source_id}: no mount for FHIR types {unknown}; bound types are {RESOURCE_TYPES}")
    if "Patient" not in raw_by_type:
        raise ValueError(f"{source.source_id}: a population needs Patient rows")

    schema_fqn = f"{catalog}.{source.schema}"
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema_fqn}")
    counts: dict[str, int] = {}
    for fhir_type in RESOURCE_TYPES:
        ddl = resource_ddl(fhir_type)
        raw = raw_by_type.get(fhir_type)
        if raw is None:
            raw = spark.createDataFrame([], "value STRING")
        schema = schema_from_ddl(fhir_type, ddl, patient_context=fhir_type != "Patient")
        frame = json_frame(spark, raw, schema, column="value")
        table = mount_table(catalog, source.schema, fhir_type)
        frame.write.mode("overwrite").saveAsTable(table)

        props = contract.cql_tblproperties(fhir_type, valid_from=valid_from, valid_to=valid_to)
        kv = ", ".join(f"'{k}' = '{v}'" for k, v in props.items())
        spark.sql(f"ALTER TABLE {table} SET TBLPROPERTIES ({kv})")
        counts[fhir_type] = spark.table(table).count()
        log.info("landed %s (%d rows)", table, counts[fhir_type])
        provision_functions(spark, catalog, source, fhir_type)

    # DATA-CHANGE-LOG — when the data last moved. Every type gets a row, the empty mounts included: an empty
    # table's max is NULL, recorded as `absent`, so a type later gaining data moves the digest.
    log_fqn = change_data_log_fqn(catalog, source.schema)
    spark.sql(change_data_log_ddl(log_fqn, {t: mount_table(catalog, source.schema, t) for t in RESOURCE_TYPES}))
    # DATA-CHANGE-LOG-PERIODS — a load rebuilds every mount, so every open evaluation set changed: one row with
    # an empty map, after the mounts, so a refresh that reads it sees the new data.
    spark.sql(change_log_ddl(catalog, source.schema))
    spark.sql(change_log_all_statement(catalog, source.schema))

    # DATA-SOURCE-UC-TAG — declare the schema a data source, last, once its mounts exist. Not best-effort:
    # without the tag the schema is not a data source at all.
    for statement in declaration_statements(name=source.source_id, catalog=catalog, schema=source.schema,
                                            description=source.description):
        spark.sql(statement)
    log.info("declared %s as data source %s", schema_fqn, source.source_id)
    return counts
