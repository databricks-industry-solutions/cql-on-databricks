"""BIND-CALL-SITE: a run's resolvers, turned into the compile request's ``data`` and ``terminology``.

A resolver that binds by rule is sent as its rule; one that binds per site is asked for each call site ``describe``
reports (`BIND-RETRIEVE-REQUEST`), and its answers are sent by site id. A table relation is sent by name. A volume or
DataFrame relation is checked (`BIND-SCHEMA-CHECK`), reshaped (`BIND-RESHAPE`) and registered as a temporary view on
the session that runs the SQL, under a name the client owns (`BIND-RELATION`). A DataFrame on a SQL warehouse run
fails before the run, naming the resolver. Nothing is materialized.

Part of the client core: SQL goes through the caller's :class:`~cql_databricks.data_bindings.runner.SqlRunner`, and
nothing here imports Spark or a platform SDK.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import reshape
from .resolvers import (CallSite, DataFrameRelation, DataResolver, Relation, TableRelation, TerminologyResolver,
                        VolumeRelation, layers)

log = logging.getLogger(__name__)

#: BIND-RELATION: the prefix of every temporary view the client registers for a relation, so `reset` finds them
#: (`CLIENT-SESSION-RESET`).
VIEW_PREFIX = "__cqlrel_"

#: The value-set relation's columns (`CLIENT-TERMINOLOGY-RESOLVER`).
TERMINOLOGY_COLUMNS = ("valueset_id", "version", "system", "code")

#: The fields of a FHIR ``ValueSet`` the terminology view reads: two levels of ``expansion.contains``.
_VALUESET_DDL = ("url STRING, version STRING, expansion STRUCT<contains: ARRAY<STRUCT<system: STRING, code: STRING, "
                 "contains: ARRAY<STRUCT<system: STRING, code: STRING>>>>>")


class ResolverError(RuntimeError):
    """A resolver cannot serve the run: a missing type, a DataFrame on a warehouse. Raised before the run, never
    swallowed."""


@dataclass(frozen=True, kw_only=True)
class BoundResolvers:
    """What one compile binds: the request's ``data`` and ``terminology`` on the wire, whether its results may be kept
    and reused (`CLIENT-RESOLVER-IDENTITY`), the terminology relations a kept result's value-set digest reads, and the
    temporary views registered for it."""
    data: dict[str, Any] = field(default_factory=dict)
    terminology: dict[str, Any] = field(default_factory=dict)
    reusable: bool = True
    terminology_tables: tuple[str, ...] = ()
    views: tuple[str, ...] = ()


def binds_by_rule(data: DataResolver | None, terminology: TerminologyResolver | None) -> bool:
    """BIND-CALL-SITE: whether every resolver binds by rule, so the compile needs no ``describe`` first."""
    return (data is None or data.rule() is not None) and (terminology is None or terminology.rule() is not None)


def bind(sites: Sequence[CallSite] | None, *, data: DataResolver | None, terminology: TerminologyResolver | None,
         run, schemas: Callable[[Sequence[str]], Mapping[str, Any]], spark=None) -> BoundResolvers:
    """BIND-CALL-SITE: the request's ``data`` and ``terminology`` for one compile.

    ``sites`` are the call sites ``describe`` reported, or None when every resolver binds by rule. ``run`` is the SQL
    runner of the session that runs the compiled SQL; ``spark`` is that session when it is a Spark session, and None
    on a SQL warehouse. ``schemas`` returns the model's :class:`~.providers.ResourceSchema` per FHIR type
    (`CLIENT-RESOURCE-SCHEMA`), for the reshape."""
    views: list[str] = []
    wired_data: dict[str, Any] = {}
    if data is not None:
        identity = data.identity()
        if data.rule() is not None:
            wired_data = {"rule": {"schema": data.rule(), "identity": identity}}
        else:
            retrieves = [s for s in _sites(sites) if s.kind == CallSite.RETRIEVE]
            served = data.types()
            if served is not None:
                lowered = {t.lower() for t in served}
                missing = sorted({s.request.fhir_type for s in retrieves if s.request.fhir_type.lower() not in lowered})
                if missing:
                    raise ResolverError(f"{data} does not serve {', '.join(missing)}, which the CQL retrieves "
                                        f"(CLIENT-DATA-RESOLVER); it serves {', '.join(sorted(served)) or 'nothing'}")
            wired_data = {"sites": {s.id: {**_wire(data.retrieve(s), data, run, spark, schemas, views),
                                           "identity": identity} for s in retrieves}}
    elif sites and any(s.kind == CallSite.RETRIEVE for s in sites):
        types = sorted({s.request.fhir_type for s in sites if s.kind == CallSite.RETRIEVE})
        raise ResolverError(f"the CQL retrieves {', '.join(types)}, and the run has no data resolver")
    wired_terminology: dict[str, Any] = {}
    tables: list[str] = []
    if terminology is not None:
        identity = terminology.identity()

        def wire(answer) -> list[dict[str, Any]]:
            out = [{**_terminology_wire(r, terminology, run, spark, views), "identity": identity} for r in layers(answer)]
            tables.extend(entry["table"] for entry in out)
            return out
        rule = terminology.rule()
        if rule is not None:
            wired_terminology = {"rule": wire(rule)}
        else:
            wired_terminology = {"sites": {s.id: wire(terminology.expansions(s))
                                           for s in _sites(sites) if s.kind == CallSite.VALUE_SET}}
    reusable = data.reusable if data is not None else True
    return BoundResolvers(data=wired_data, terminology=wired_terminology, reusable=reusable,
                          terminology_tables=tuple(dict.fromkeys(tables)), views=tuple(views))


def _sites(sites: Sequence[CallSite] | None) -> Sequence[CallSite]:
    if sites is None:
        raise ResolverError("a resolver that binds per site needs the call sites describe reports (BIND-CALL-SITE)")
    return sites


def _wire(relation: Relation, provider, run, spark, schemas, views: list[str]) -> dict[str, Any]:
    """One data relation as the request carries it."""
    if isinstance(relation, TableRelation):
        return {"table": relation.name}
    if isinstance(relation, VolumeRelation):
        schema = schemas([relation.fhir_type])[relation.fhir_type]
        view = _view_name("volume", relation.path, relation.fhir_type, relation.layout)
        run(f"CREATE OR REPLACE TEMPORARY VIEW {reshape.quote_name(view)} AS "
            + reshape.volume_sql(relation.path, schema, layout=relation.layout))
        views.append(view)
        return {"table": view}
    if isinstance(relation, DataFrameRelation):
        if spark is None:
            raise ResolverError(f"{provider} returned a DataFrame for {relation.fhir_type}, and a SQL warehouse "
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
    raise ResolverError(f"{provider} returned {relation!r}, which is not a relation (BIND-RELATION)")


def _terminology_wire(relation: Relation, source, run, spark, views: list[str]) -> dict[str, Any]:
    """One value-set layer as the request carries it: a table name. A DataFrame is registered as a view."""
    if isinstance(relation, TableRelation):
        return {"table": relation.name}
    if isinstance(relation, VolumeRelation) and relation.layout == "files":
        files = (f"(SELECT from_json(`value`, {reshape._literal(_VALUESET_DDL)}) AS `vs` FROM "
                 f"read_files({reshape._literal(relation.path)}, format => 'text', wholeText => true))")
        bare = run(f"SELECT `vs`.`url` AS url FROM {files} WHERE `vs`.`expansion`.`contains` IS NULL")
        if bare:
            raise ResolverError(f"value sets with no expansion, only compose, in {relation.path}: "
                                f"{', '.join(sorted(str(r['url']) for r in bare))}; expand them first "
                                f"(CLIENT-TERMINOLOGY-RESOLVER)")
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
            raise ResolverError(f"{source} returned a DataFrame of value sets, and a SQL warehouse cannot read a "
                                f"DataFrame (BIND-RELATION)")
        view = _view_name("valuesets", source.identity(), str(id(relation.frame)))
        relation.frame.select(*TERMINOLOGY_COLUMNS).createOrReplaceTempView(view)
        views.append(view)
        return {"table": view}
    raise ResolverError(f"{source} returned {relation!r}; a terminology relation is a table, a view or a DataFrame "
                        f"(CLIENT-TERMINOLOGY-RESOLVER)")


def _view_name(*parts: str) -> str:
    """A temporary view name the client owns, the same for the same parts."""
    return VIEW_PREFIX + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
