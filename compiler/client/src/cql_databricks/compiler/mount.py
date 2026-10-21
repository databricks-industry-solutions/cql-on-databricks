"""CLIENT-SESSION-MOUNT, CLIENT-SESSION-RESET — FHIR data and value sets on a Spark session, with no catalog
writes.

A mount is session state. Every client on the session reads it, the ``%%cql`` magic's included, and it
replaces discovery (`CLIENT-EXEC-DISCOVERY`): each mounted type and the value-set view are bound explicitly
(`BIND-MANIFEST`), and a retrieve of a type that was not mounted fails, naming the mounted types. A session
holds one mount; mounting again replaces it. An explicit ``data_source=`` or ``source_binding`` still beats it.

Each type is checked against the model (`BIND-SCHEMA-CHECK`), reshaped (`BIND-RESHAPE`), and registered as a
``TEMPORARY`` function whose name the mount owns (``retrieve_Observation__cqlmount``), so a user's own
``retrieve_Observation`` is untouched. Temp functions and views belong to the Spark session, so the mount ends
with it. The manifest is kept in the session record (`CLIENT-SESSION-STATE`), beside them.

How a string is read, with no guessing:

- ``fhir=``: a string is a folder of ``<Type>.ndjson`` files and must contain ``/``. A dict maps a type to a
  DataFrame or a table name, and a table name must be ``catalog.schema.table``. Raw JSON of mixed types is a
  :class:`~.reshape.FhirJson`.
- ``valuesets=``: a string containing ``/`` is a folder of NDJSON ``{valueset_id, system, code}`` rows; a string
  ``catalog.schema.table`` is a table; a DataFrame has those three columns. Anything else fails.

Files under ``/Volumes`` are read by Spark. Other files are read on the driver, up to
:data:`DRIVER_READ_LIMIT` bytes.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .reshape import (FhirJson, SchemaIssue, json_sql, model_client, quote_name, require_ok, sql_type,
                      structured_sql, table_name, type_predicate)
from .runtime import retrieve_params_ddl
from .session_state import SessionRecord

FUNCTION_SUFFIX = "__cqlmount"
# Views carry the mount's generation, so a new mount's sources can be read (even ones built on the old mount)
# before the old mount's objects are dropped.
_VALUESETS_VIEW = "__cqlmount_valuesets_{}"
_SOURCE_VIEW = "__cqlmount_src_{}_{}"
VALUESET_COLUMNS = ("valueset_id", "system", "code")
#: Files outside ``/Volumes`` are read on the driver; past this many bytes in one mount, the mount fails.
DRIVER_READ_LIMIT = 64 * 1024 * 1024


def mount(spark, fhir, *, valuesets=None, client=None) -> Dict[str, str]:
    """Mount FHIR data, and optionally value sets, on ``spark``'s session (`CLIENT-SESSION-MOUNT`). Replaces
    the session's earlier mount. Returns ``{fhir_type: function}``.

    ``fhir`` is a folder path, a ``{type: DataFrame or table}`` dict, a :class:`~.reshape.FhirJson`, or a list of
    these; two sources of one type fail. ``valuesets`` is a folder path, a table name or a DataFrame.
    ``client`` supplies the model's schema (`CLIENT-RESOURCE-SCHEMA`), else the zero-config client.

    Every source is checked first, and any error refuses the whole mount, listing every problem."""
    model = model_client(spark, client)
    plans = _plans(spark, fhir, model)
    require_ok([issue for plan in plans for issue in plan[3]])
    record = SessionRecord(spark)
    previous = record.mount()
    generation = record.version() + 1
    # Every source is staged before any function changes, so a source built on the old mount reads it.
    views: List[str] = []
    bodies: Dict[str, str] = {}
    for fhir_type, _, relation, _ in plans:
        if not isinstance(relation, str):
            view = _SOURCE_VIEW.format(fhir_type, generation)
            relation[0].createOrReplaceTempView(view)
            views.append(view)
            relation = relation[1](_ident(view))
        bodies[fhir_type] = relation
    vs_view = _mount_valuesets(spark, valuesets, _VALUESETS_VIEW.format(generation))
    if vs_view is not None:
        views.append(vs_view)
    functions: Dict[str, str] = {}
    for fhir_type, schema, _, _ in plans:
        name = f"retrieve_{fhir_type}{FUNCTION_SUFFIX}"
        spark.sql(f"CREATE OR REPLACE TEMPORARY FUNCTION {_ident(name)}({retrieve_params_ddl()}) "
                  f"RETURNS TABLE (`__subject_id` STRING, `__resource` {sql_type(schema.struct)}) "
                  f"RETURN {bodies[fhir_type]}")
        functions[fhir_type] = name
    manifest = {"types": functions, "valuesets": vs_view, "views": views}
    _drop(spark, previous, keep=manifest)
    record.set_mount(manifest)
    return functions


def reset(spark) -> None:
    """Start the session clean (`CLIENT-SESSION-RESET`): drop the mount, its temp functions and views, and the
    libraries ``%%cql`` cells declared, then the session record. Safe to call twice. Define result views are
    left alone; each run re-creates them."""
    record = SessionRecord(spark)
    _drop(spark, record.mount())
    record.drop()


# -- planning: every source checked before anything is registered ---------------------------------------

Plan = Tuple[str, object, object, List[SchemaIssue]]


def _plans(spark, fhir, model) -> List[Plan]:
    sources = fhir if isinstance(fhir, list) else [fhir]
    plans: List[Plan] = []
    origin: Dict[str, str] = {}
    for source in sources:
        for plan in _source_plans(spark, source, model):
            where = repr(source) if not isinstance(source, dict) else "the type map"
            if plan[0] in origin:
                raise ValueError(f"{plan[0]} is mounted twice: from {origin[plan[0]]} and from {where}")
            origin[plan[0]] = where
            plans.append(plan)
    if not plans:
        raise ValueError("nothing to mount: the sources supply no FHIR type")
    return plans


def _source_plans(spark, source, model) -> List[Plan]:
    if isinstance(source, str):
        return _folder_plans(spark, _path(source, "fhir="), model)
    if isinstance(source, FhirJson):
        modeled = set(model.resource_schema())
        types = source.found_types(spark)
        if source.types is not None:
            _modeled(source.types, modeled, f"{source!r}")
        plans = []
        for fhir_type in (t for t in types if t in modeled):
            schema = model.resource_schema(fhir_type)
            predicate = type_predicate(fhir_type, json_column=source.column, type_column=source.type_column)
            if source.is_table:
                sql = json_sql(quote_name(source.relation), schema, column=source.column, type_filter=predicate)
                plans.append((fhir_type, schema, sql, []))
            else:
                plans.append((fhir_type, schema, (source.relation, _json(schema, source.column, predicate)), []))
        return plans
    if isinstance(source, dict):
        plans = []
        for fhir_type, relation in sorted(source.items()):
            schema = model.resource_schema(fhir_type)
            if isinstance(relation, str):
                name = table_name(relation)
                sql, issues = structured_sql(quote_name(name), spark.table(name).schema, schema)
                plans.append((fhir_type, schema, sql, issues))
            else:
                _, issues = structured_sql("`_`", relation.schema, schema)
                plans.append((fhir_type, schema, (relation, _structured(relation.schema, schema)), issues))
        return plans
    raise TypeError(f"cannot mount {type(source).__name__}: give a folder path, a {{type: DataFrame or table}} "
                    f"dict, or a FhirJson")


def _folder_plans(spark, folder: str, model) -> List[Plan]:
    files = sorted(f for f in os.listdir(folder) if f.endswith(".ndjson"))
    if not files:
        raise ValueError(f"{folder} holds no <Type>.ndjson files")
    modeled = set(model.resource_schema())
    _modeled([f[:-len(".ndjson")] for f in files], modeled, folder)
    reader = _Reader(spark)
    plans = []
    for f in files:
        fhir_type = f[:-len(".ndjson")]
        schema = model.resource_schema(fhir_type)
        plans.append((fhir_type, schema, (reader.lines(os.path.join(folder, f)), _json(schema, "value", None)), []))
    return plans


def _json(schema, column, predicate):
    return lambda relation: json_sql(relation, schema, column=column, type_filter=predicate)


def _structured(source_schema, schema):
    return lambda relation: structured_sql(relation, source_schema, schema)[0]


def _modeled(types, modeled, where) -> None:
    unknown = sorted(set(types) - modeled)
    if unknown:
        raise ValueError(f"{where} supplies {', '.join(unknown)}, which the compiler does not model")


# -- value sets ----------------------------------------------------------------------------------------------

def _mount_valuesets(spark, valuesets, view: str) -> Optional[str]:
    if valuesets is None:
        return None
    if isinstance(valuesets, str):
        if "/" in valuesets:
            folder = _path(valuesets, "valuesets=")
            files = sorted(f for f in os.listdir(folder) if f.endswith(".ndjson"))
            if not files:
                raise ValueError(f"{folder} holds no value-set .ndjson files")
            reader = _Reader(spark)
            lines = reader.union([reader.lines(os.path.join(folder, f)) for f in files])
            ddl = ", ".join(f"{c} STRING" for c in VALUESET_COLUMNS)
            frame = spark.sql(f"SELECT `v`.* FROM (SELECT from_json(`value`, '{ddl}') AS `v` FROM {{source}})",
                              source=lines)
        elif "." in valuesets:
            frame = spark.table(table_name(valuesets))
        else:
            raise ValueError(f"valuesets={valuesets!r} is neither a folder (it has no '/') nor a table "
                             f"(catalog.schema.table)")
    else:
        frame = valuesets
    missing = [c for c in VALUESET_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"the value sets have no {', '.join(missing)} column; they need "
                         f"{', '.join(VALUESET_COLUMNS)}")
    frame.select(*VALUESET_COLUMNS).createOrReplaceTempView(view)
    return view


# -- files ---------------------------------------------------------------------------------------------------

def _path(value: str, where: str) -> str:
    if "/" not in value:
        raise ValueError(f"{where}{value!r} is not a path: a folder path contains '/' (write ./{value})")
    folder = os.path.abspath(value)
    if not os.path.isdir(folder):
        raise ValueError(f"{where}{value!r} is not a folder ({folder})")
    return folder


class _Reader:
    """NDJSON files as one-column (``value``) DataFrames: by Spark under ``/Volumes``, else on the driver."""

    def __init__(self, spark) -> None:
        self._spark = spark
        self._read = 0

    def lines(self, path: str):
        if path.startswith("/Volumes/"):
            return self._spark.read.text(path)
        self._read += os.path.getsize(path)
        if self._read > DRIVER_READ_LIMIT:
            raise ValueError(f"the files read on the driver pass {DRIVER_READ_LIMIT} bytes at {path}; put large "
                             f"data in a /Volumes folder or a table")
        with open(path, encoding="utf-8") as f:
            rows = [(line,) for line in f.read().splitlines() if line.strip()]
        return self._spark.createDataFrame(rows, "value STRING")

    @staticmethod
    def union(frames):
        out = frames[0]
        for frame in frames[1:]:
            out = out.unionByName(frame)
        return out


# -- teardown ------------------------------------------------------------------------------------------------

def _drop(spark, manifest: Optional[dict], keep: Optional[dict] = None) -> None:
    """Drop what a mount registered, except what ``keep`` (the mount replacing it) uses. Every name is the
    mount's own."""
    if not manifest:
        return
    kept = keep or {}
    for name in set(manifest.get("types", {}).values()) - set(kept.get("types", {}).values()):
        spark.sql(f"DROP TEMPORARY FUNCTION IF EXISTS {_ident(name)}")
    for view in set(manifest.get("views", [])) - set(kept.get("views", [])):
        spark.catalog.dropTempView(view)


def _ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"
