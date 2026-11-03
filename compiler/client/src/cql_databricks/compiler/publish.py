"""BIND-PUBLISH — publish an environment's data over tables, for review or to run.

An environment's data is its ``data`` schema: one published view per FHIR type, named for the type in lower case,
returning the data contract, the resource as the ``resource`` struct and the subject key as ``patient_id``
(`DATA-ENVIRONMENT-LAYOUT`, `BIND-CONTRACT`, `DATA-SUBJECT-KEY`). Publishing writes that mapping and never data: ``CREATE OR REPLACE VIEW <catalog>.data.<type>`` with
the reshape of its table as the body (`BIND-RESHAPE`), replacing the view that selected the store. Runs bind the views by name
(`BIND-PUBLISHED-VIEW`).

Sources are tables only. A published view can't name a session DataFrame or files read on the driver, and a view
over raw JSON would parse it on every query. So a :class:`~.reshape.FhirJson` source is written to a table first
(``FhirJson(...).to_df(spark, type)``), and that table is published.

Every table is checked against the model before anything runs (`BIND-SCHEMA-CHECK`); the plan carries every
finding, and :meth:`PublishPlan.run` refuses on any error. It runs as the caller, who needs ``CREATE TABLE`` on the
environment's ``data`` schema and ``SELECT`` on the tables. Each view returns its whole table, a correct superset
(`BIND-CAPABILITY`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

from cql_databricks.data_bindings.runner import SqlRunner, spark_runner

from cql_databricks.resolvers.environment import DATA
from .reshape import SchemaIssue, model_client, quote_name, require_ok, structured_sql, table_name

_CATALOG_NAME = re.compile(r"^[^./`\s]+$")


@dataclass(frozen=True, kw_only=True)
class PublishPlan:
    """The statements that publish the data views of the environment in ``catalog``, and every finding about their
    tables. Nothing has run until :meth:`run`."""
    spark: object = field(repr=False)
    catalog: str
    views: Mapping[str, str]
    statements: List[str]
    issues: List[SchemaIssue]

    @property
    def sql(self) -> str:
        """The whole script, for review."""
        return "".join(f"{s};\n\n" for s in self.statements).rstrip("\n") + "\n"

    def run(self, runner: Optional[SqlRunner] = None) -> Mapping[str, str]:
        """Run the statements as the caller, on the plan's Spark session or through ``runner``. Refuses before running
        anything when a table has an error. Returns the published view of each type."""
        require_ok(self.issues)
        run = runner if runner is not None else spark_runner(self.spark)
        for statement in self.statements:
            run(statement)
        return self.views


def publish_data_source(spark, catalog: str, *, fhir: Mapping[str, str], client=None) -> PublishPlan:
    """Plan the data views of the environment in ``catalog`` over ``fhir``, a ``{type: table}`` dict whose tables are
    ``catalog.schema.table`` (`BIND-PUBLISH`). ``client`` supplies the model's schema (`CLIENT-RESOURCE-SCHEMA`), else
    the zero-config client.

    Reads each table's schema on ``spark``; writes nothing. Review ``plan.sql`` and ``plan.issues``, then
    ``plan.run()``."""
    if not isinstance(catalog, str) or not _CATALOG_NAME.match(catalog):
        raise ValueError(f"{catalog!r} is not a catalog name: publish into an environment's catalog")
    if not isinstance(fhir, dict):
        raise TypeError(f"fhir= takes a {{type: 'catalog.schema.table'}} dict, not {type(fhir).__name__}: "
                        f"publish reads tables only")
    if not fhir:
        raise ValueError("nothing to publish: fhir= names no table")
    model = model_client(spark, client)
    statements: List[str] = []
    views: Dict[str, str] = {}
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
        view = f"{catalog}.{DATA}.{fhir_type.lower()}"
        views[fhir_type] = view
        statements.append(f"CREATE OR REPLACE VIEW {quote_name(view)} AS {body}")
    return PublishPlan(spark=spark, catalog=catalog, views=views, statements=statements, issues=issues)
