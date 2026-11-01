"""BIND-PROVIDER-REQUEST: a run's providers, turned into the compile request's ``data`` and ``terminology``.

The client asks ``describe`` for the retrieves the compiled CQL makes (`BIND-RETRIEVE-REQUEST`), asks the data
provider once for each, and sends the relations keyed by retrieve id. A table or function relation is sent by
name; a function's parameters are read from the catalog (`BIND-NAMED-ARGS`). A volume or DataFrame relation is
checked (`BIND-SCHEMA-CHECK`), reshaped (`BIND-RESHAPE`) and registered as a temporary view on the session that
runs the SQL, under a name the client owns (`BIND-RELATION`). A DataFrame on a SQL warehouse run fails before
the run, naming the provider. Nothing is materialized.

Part of the client core: SQL goes through the caller's :class:`~cql_databricks.data_bindings.runner.SqlRunner`,
and nothing here imports Spark or a platform SDK.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any

from . import reshape
from .roles import (DataFrameRelation, DataProvider, FunctionRelation, Relation, Retrieve, RetrieveRequest,
                    TableRelation, TerminologySource, ValueSetRef, VolumeRelation, terminology_relations)

log = logging.getLogger(__name__)

#: BIND-RELATION: the prefix of every temporary view the client registers for a relation, so `reset` finds them
#: (`CLIENT-SESSION-RESET`).
VIEW_PREFIX = "__cqlrel_"

#: BIND-NAMED-ARGS: the request fields a function relation may declare, by name.
REQUEST_FIELDS = frozenset(f.name for f in fields(RetrieveRequest))

#: The value-set relation's columns (`CLIENT-TERMINOLOGY-PROVIDER`).
TERMINOLOGY_COLUMNS = ("valueset_id", "version", "system", "code")

#: The fields of a FHIR ``ValueSet`` the terminology view reads: two levels of ``expansion.contains``.
_VALUESET_DDL = ("url STRING, version STRING, expansion STRUCT<contains: ARRAY<STRUCT<system: STRING, code: STRING, "
                 "contains: ARRAY<STRUCT<system: STRING, code: STRING>>>>>")


_PARAMETERS = """
SELECT r.routine_name AS routine, p.parameter_name AS parameter, p.ordinal_position AS position,
       p.parameter_default AS dflt
  FROM system.information_schema.routines r
  LEFT JOIN system.information_schema.parameters p
    ON p.specific_catalog = r.specific_catalog AND p.specific_schema = r.specific_schema
   AND p.specific_name = r.specific_name
 WHERE r.routine_catalog = :catalog AND r.routine_schema = :schema
"""


class ProviderError(RuntimeError):
    """A provider cannot serve the run: a missing type, a DataFrame on a warehouse, a function parameter no
    request field supplies. Raised before the run, never swallowed."""


@dataclass(frozen=True, kw_only=True)
class BoundProviders:
    """What one compile binds: the request's ``data`` and ``terminology`` on the wire, whether its results may be
    kept and reused (`CLIENT-PROVIDER-IDENTITY`), and the temporary views registered for it."""
    data: dict[str, dict[str, Any]] = field(default_factory=dict)
    terminology: list[dict[str, Any]] = field(default_factory=list)
    reusable: bool = True
    views: tuple[str, ...] = ()


def bind(retrieves: Sequence[Retrieve], value_sets: Sequence[ValueSetRef], *,
         data: DataProvider | None, terminology: TerminologySource | None, run,
         schemas: Callable[[Sequence[str]], Mapping[str, Any]], spark=None) -> BoundProviders:
    """BIND-PROVIDER-REQUEST: the relations for one compile.

    ``run`` is the SQL runner of the session that runs the compiled SQL; ``spark`` is that session when it is a
    Spark session, and None on a SQL warehouse. ``schemas`` returns the model's
    :class:`~.providers.ResourceSchema` per FHIR type (`CLIENT-RESOURCE-SCHEMA`), for the reshape."""
    views: list[str] = []
    relations: dict[str, dict[str, Any]] = {}
    if retrieves:
        if data is None:
            types = sorted({r.request.fhir_type for r in retrieves})
            raise ProviderError(f"the CQL retrieves {', '.join(types)}, and the run has no data provider")
        served = {t.lower() for t in data.types()}
        missing = sorted({r.request.fhir_type for r in retrieves if r.request.fhir_type.lower() not in served})
        if missing:
            raise ProviderError(f"{data} does not serve {', '.join(missing)}, which the CQL retrieves "
                                f"(CLIENT-DATA-PROVIDER); it serves {', '.join(sorted(data.types())) or 'nothing'}")
        answered = {r.id: data.retrieve(r.request) for r in retrieves}
        parameters = _function_parameters(run, sorted({rel.name for rel in answered.values()
                                                       if isinstance(rel, FunctionRelation)}))
        identity = data.identity()
        for rid, relation in answered.items():
            relations[rid] = {**_wire(relation, data, run, spark, schemas, views, parameters), "identity": identity}
    layers: list[dict[str, Any]] = []
    if terminology is not None:
        identity = terminology.identity()
        for relation in terminology_relations(terminology.expansions(list(value_sets))):
            wired = _terminology_wire(relation, terminology, run, spark, views)
            layers.append({**wired, "identity": identity})
    reusable = data.reusable if data is not None else True
    return BoundProviders(data=relations, terminology=layers, reusable=reusable, views=tuple(views))


def _wire(relation: Relation, provider, run, spark, schemas, views: list[str],
          parameters: Mapping[str, list[str]]) -> dict[str, Any]:
    """One data relation as the request carries it."""
    if isinstance(relation, TableRelation):
        return {"table": relation.name}
    if isinstance(relation, FunctionRelation):
        return {"function": relation.name, "parameters": parameters[relation.name]}
    if isinstance(relation, VolumeRelation):
        schema = schemas([relation.fhir_type])[relation.fhir_type]
        view = _view_name("volume", relation.path, relation.fhir_type, relation.layout)
        run(f"CREATE OR REPLACE TEMPORARY VIEW {reshape.quote_name(view)} AS "
            + reshape.volume_sql(relation.path, schema, layout=relation.layout))
        views.append(view)
        return {"table": view}
    if isinstance(relation, DataFrameRelation):
        if spark is None:
            raise ProviderError(f"{provider} returned a DataFrame for {relation.fhir_type}, and a SQL warehouse "
                                f"cannot read a DataFrame (BIND-RELATION): publish the data to a table, or run on "
                                f"Spark")
        schema = schemas([relation.fhir_type])[relation.fhir_type]
        source = _view_name("frame", provider.identity(), relation.fhir_type, str(id(relation.frame)))
        relation.frame.createOrReplaceTempView(source)
        sql, issues = reshape.structured_sql(reshape.quote_name(source), relation.frame.schema, schema)
        reshape.require_ok(issues)
        for issue in issues:
            log.info("%s: %s", provider, issue)
        view = source + "_r"
        run(f"CREATE OR REPLACE TEMPORARY VIEW {reshape.quote_name(view)} AS {sql}")
        views.extend([source, view])
        return {"table": view}
    raise ProviderError(f"{provider} returned {relation!r}, which is not a relation (BIND-RELATION)")


def _terminology_wire(relation: Relation, source, run, spark, views: list[str]) -> dict[str, Any]:
    """One value-set layer as the request carries it: a table name. A DataFrame is registered as a view."""
    if isinstance(relation, TableRelation):
        return {"table": relation.name}
    if isinstance(relation, VolumeRelation) and relation.layout == "files":
        files = (f"(SELECT from_json(`value`, {reshape._literal(_VALUESET_DDL)}) AS `vs` FROM "
                 f"read_files({reshape._literal(relation.path)}, format => 'text', wholeText => true))")
        bare = run(f"SELECT `vs`.`url` AS url FROM {files} WHERE `vs`.`expansion`.`contains` IS NULL")
        if bare:
            raise ProviderError(f"value sets with no expansion, only compose, in {relation.path}: "
                                f"{', '.join(sorted(str(r['url']) for r in bare))}; expand them first "
                                f"(CLIENT-TERMINOLOGY-PROVIDER)")
        view = _view_name("valuesets", source.identity(), relation.path)
        run(f"CREATE OR REPLACE TEMPORARY VIEW {reshape.quote_name(view)} AS "
            f"SELECT `vs`.`url` AS valueset_id, `vs`.`version` AS version, `c`.`system` AS system, "
            f"`c`.`code` AS code FROM {files} "
            f"LATERAL VIEW explode(concat(transform(`vs`.`expansion`.`contains`, "
            f"x -> named_struct('system', x.`system`, 'code', x.`code`)), flatten(transform(`vs`.`expansion`.`contains`, "
            f"x -> coalesce(x.`contains`, array()))))) AS `c` WHERE `c`.`code` IS NOT NULL")
        views.append(view)
        return {"table": view}
    if isinstance(relation, DataFrameRelation):
        if spark is None:
            raise ProviderError(f"{source} returned a DataFrame of value sets, and a SQL warehouse cannot read a "
                                f"DataFrame (BIND-RELATION)")
        view = _view_name("valuesets", source.identity(), str(id(relation.frame)))
        relation.frame.select(*TERMINOLOGY_COLUMNS).createOrReplaceTempView(view)
        views.append(view)
        return {"table": view}
    raise ProviderError(f"{source} returned {relation!r}; a terminology relation is a table, a view or a DataFrame "
                        f"(CLIENT-TERMINOLOGY-PROVIDER)")


def _function_parameters(run, names: Sequence[str]) -> dict[str, list[str]]:
    """BIND-NAMED-ARGS: each function's parameters that name a request field, in declared order, read from
    ``information_schema`` in one query per schema. A parameter that names no request field must have a
    default and is left to it; a function that is not there, or a parameter with no default that no request
    field supplies, fails here."""
    by_schema: dict[tuple[str, str], list[str]] = {}
    for name in names:
        parts = name.split(".")
        if len(parts) != 3:
            raise ProviderError(f"a function relation is named catalog.schema.function, not {name!r}")
        by_schema.setdefault((parts[0], parts[1]), []).append(name)
    out: dict[str, list[str]] = {}
    for (catalog, schema), wanted in by_schema.items():
        rows = run(_PARAMETERS, {"catalog": catalog, "schema": schema})
        declared: dict[str, list[tuple[int, str, Any]]] = {}
        for row in rows:
            entries = declared.setdefault(row["routine"].lower(), [])
            if row["parameter"] is not None:
                entries.append((int(row["position"]), row["parameter"], row["dflt"]))
        for name in wanted:
            routine = name.split(".")[2].lower()
            if routine not in declared:
                raise ProviderError(f"function {name} is not in {catalog}.{schema}, or you cannot see it")
            kept = []
            for _, parameter, default in sorted(declared[routine]):
                if parameter.lower() in REQUEST_FIELDS:
                    kept.append(parameter.lower())
                elif default is None:
                    raise ProviderError(
                        f"function {name} declares parameter {parameter}, which names no retrieve request field "
                        f"({', '.join(sorted(REQUEST_FIELDS))}) and has no default (BIND-NAMED-ARGS)")
            out[name] = kept
    return out


def _view_name(*parts: str) -> str:
    """A temporary view name the client owns, the same for the same parts."""
    return VIEW_PREFIX + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
