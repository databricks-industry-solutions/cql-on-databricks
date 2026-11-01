"""BIND-PUBLISH — write a tagged data source from tables, for review or to run.

A data source is a Unity Catalog schema of ``retrieve_<Type>`` functions, tagged
``cql_databricks_data_source = <name>`` (`BIND-DATA-SOURCE-LAYOUT`, `DATA-SOURCE-UC-TAG`). Publishing writes
that declaration and never data: the schema, one function per type with the contract signature
(`BIND-EXPLICIT-PARAMS`) and the model's return struct (`BIND-CONTRACT`), then the tag and the description
(`DATA-SOURCE-DESCRIPTION`). Each function's body is the reshape of its table (`BIND-RESHAPE`).

Sources are tables only. A published function can't name a session DataFrame or files read on the driver, and
a function over raw JSON would parse it on every query. So a :class:`~.reshape.FhirJson` source is written to
a table first (``FhirJson(...).to_df(spark, type)``), and that table is published.

Every table is checked against the model before anything runs (`BIND-SCHEMA-CHECK`); the plan carries every
finding, and :meth:`PublishPlan.run` refuses on any error. After the run, the schema is re-read and checked
against the layout. It runs as the caller, who needs ``CREATE SCHEMA``, ``CREATE FUNCTION`` and ``APPLY TAG``.
The functions ignore the pushdown arguments: the whole table is a correct superset (`BIND-CAPABILITY`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

from cql_databricks.data_bindings import DATA_SOURCE_TAG, declaration_statements, tag_statement
from cql_databricks.data_bindings.runner import SqlRunner, spark_runner

from .data_source import DataSourceLayout, DataSourceLayoutError, check_layout
from .reshape import SchemaIssue, model_client, quote_name, require_ok, sql_type, structured_sql, table_name

_SCHEMA_NAME = re.compile(r"^[^./`\s]+\.[^./`\s]+$")


@dataclass(frozen=True, kw_only=True)
class PublishPlan:
    """The statements that publish ``catalog.schema`` as the data source ``name``, and every finding about its
    tables. Nothing has run until :meth:`run`."""
    spark: object = field(repr=False)
    catalog: str
    schema: str
    name: str
    functions: Mapping[str, str]
    statements: List[str]
    issues: List[SchemaIssue]
    modeled_types: List[str] = field(repr=False)

    @property
    def sql(self) -> str:
        """The whole script, for review."""
        return "".join(f"{s};\n\n" for s in self.statements).rstrip("\n") + "\n"

    def run(self, runner: Optional[SqlRunner] = None) -> DataSourceLayout:
        """Run the statements as the caller, on the plan's Spark session or through ``runner``, then check the
        schema against the layout (`BIND-DATA-SOURCE-LAYOUT`). Refuses before running anything when a table
        has an error. Returns the layout the schema now holds."""
        require_ok(self.issues)
        run = runner if runner is not None else spark_runner(self.spark)
        for statement in self.statements:
            run(statement)
        layout = check_layout(run, self.catalog, self.schema, self.modeled_types)
        missing = sorted(set(self.functions) - set(layout.bindings))
        if missing:
            raise DataSourceLayoutError(f"{self.catalog}.{self.schema} has no retrieve function for "
                                        f"{', '.join(missing)} after publishing")
        return layout


def publish_data_source(spark, target: str, *, name: str, fhir: Mapping[str, str],
                        description: Optional[str] = None, client=None) -> PublishPlan:
    """Plan the data source ``name`` in ``target`` (``catalog.schema``) over ``fhir``, a ``{type: table}`` dict
    whose tables are ``catalog.schema.table`` (`BIND-PUBLISH`). ``description`` becomes the schema's comment.
    ``client`` supplies the model's schema (`CLIENT-RESOURCE-SCHEMA`), else the zero-config client.

    Reads each table's schema on ``spark``; writes nothing. Review ``plan.sql`` and ``plan.issues``, then
    ``plan.run()``."""
    catalog, schema = _schema_name(target)
    if not isinstance(fhir, dict):
        raise TypeError(f"fhir= takes a {{type: 'catalog.schema.table'}} dict, not {type(fhir).__name__}: "
                        f"publish reads tables only")
    if not fhir:
        raise ValueError("nothing to publish: fhir= names no table")
    model = model_client(spark, client)
    modeled = sorted(model.resource_schema())
    statements = [f"CREATE SCHEMA IF NOT EXISTS {quote_name(target)}"]
    functions: Dict[str, str] = {}
    issues: List[SchemaIssue] = []
    for fhir_type, relation in sorted(fhir.items()):
        if not isinstance(relation, str):
            raise TypeError(f"fhir[{fhir_type!r}] is a {type(relation).__name__}; publish reads tables only. "
                            f"Write it to a table first (for raw JSON, FhirJson(...).to_df(spark, "
                            f"{fhir_type!r})), then publish that table")
        source = table_name(relation)
        resource = model.resource_schema(fhir_type)
        body, found = structured_sql(quote_name(source), spark.table(source).schema, resource)
        issues.extend(found)
        function = f"{catalog}.{schema}.retrieve_{fhir_type}"
        functions[fhir_type] = function
        statements.append(_function_ddl(function, resource, source, body))
    if description is None:
        statements.append(tag_statement(DATA_SOURCE_TAG, name, catalog, schema))
    else:
        statements.extend(declaration_statements(name=name, catalog=catalog, schema=schema,
                                                 description=description))
    return PublishPlan(spark=spark, catalog=catalog, schema=schema, name=name, functions=functions,
                       statements=statements, issues=issues, modeled_types=modeled)


def _function_ddl(function: str, resource, source: str, body: str) -> str:
    comment = (f"BIND-PUBLISH: retrieve_{resource.fhir_type} over {source}; the whole table, declaring no request "
               f"field (BIND-NAMED-ARGS, BIND-CAPABILITY)")
    return (f"CREATE OR REPLACE FUNCTION {quote_name(function)}()\n"
            f"RETURNS TABLE (`__subject_id` STRING, `__resource` {sql_type(resource.struct)})\n"
            f"LANGUAGE SQL\n"
            f"COMMENT {_literal(comment)}\n"
            f"RETURN {body}")


def _schema_name(value: str):
    """``value`` as ``(catalog, schema)``. It must be ``catalog.schema``; a string is never guessed at."""
    if not isinstance(value, str) or not _SCHEMA_NAME.match(value):
        raise ValueError(f"{value!r} is not a schema name: write the target as catalog.schema")
    catalog, schema = value.split(".")
    return catalog, schema


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
