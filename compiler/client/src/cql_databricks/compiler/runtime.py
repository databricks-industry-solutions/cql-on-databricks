"""The retrieve UDTF signature (`BIND-RETRIEVE-UDF`, `BIND-EXPLICIT-PARAMS`).

A client whose retrieves are bound to UDTFs emits ``FROM [catalog.]retrieve_<Type>(<7 explicit args>)``
calls, so SOMETHING must provide those SQL UDTFs at the executor. On Databricks they are precreated,
UC-governed SQL functions in a tagged data source (`BIND-UC-PRECREATE`, `CLIENT-EXEC-DISCOVERY`). In a session,
``mount`` registers them over files, tables or DataFrames (`CLIENT-SESSION-MOUNT`).

THE SIGNATURE (`BIND-EXPLICIT-PARAMS`) is fixed and POSITIONAL — :data:`RETRIEVE_PARAMS` is the single
source of truth, and any hand-written binding must declare the same seven parameters in the same order. None names
a library (`EMIT-ROOT-INDEPENDENT`): a retrieve's rows depend only on its arguments and its data source, so a
population is chosen by choosing a data source::

    CREATE FUNCTION retrieve_<Type>(
      value_set     STRING                                   DEFAULT NULL,    -- canonical id / OID
      code_property STRING                                   DEFAULT NULL,    -- RESOLVED physical slot
      codes         ARRAY<STRUCT<system STRING, code STRING>> DEFAULT array(), -- direct declared codes
      date_property STRING                                   DEFAULT NULL,
      date_low      STRING                                   DEFAULT NULL,    -- inclusive ISO bound
      date_high     STRING                                   DEFAULT NULL,    -- inclusive ISO bound
      parameters    MAP<STRING, VARIANT>                     DEFAULT map())   -- CQL parameter defaults
    RETURNS TABLE (__subject_id STRING, __resource STRUCT<…>)
    RETURN <your query>

Every argument arrives as a compile-time LITERAL, so a guard on one folds at analysis time and Catalyst
eliminates the non-matching ``UNION`` arms of the body — that is the whole point of the seam. Read a
scalar directly (``value_set = '…'``), an array by membership (``cardinality(codes) > 0``), and the parameter map with **try_variant_get**
(``try_variant_get(parameters['Measurement Period'], '$.low', 'string')``) — plain ``variant_get`` raises
`INVALID_VARIANT_CAST` under ANSI on a type mismatch, and because the argument is folded that failure
happens at PLAN time, killing the whole query rather than the one arm. An absent value is NULL (or an
empty array/map), so test presence; never assume a sentinel.

An interval-valued parameter carries its CLOSURE — ``{low, lowClosed, high, highClosed}`` — because the
standard eCQM Measurement Period is HALF-OPEN (``Interval[@2026-01-01T00:00:00.0,
@2027-01-01T00:00:00.0)``); reading ``high`` as inclusive would over-select the boundary instant. For the
same reason ``date_low``/``date_high`` are INCLUSIVE-ONLY and stay NULL for a half-open range rather than
shift a bound: to prune on the measurement window, read the parameter map.

A function that ignores all seven arguments returns the whole source — the un-pruned but correct SUPERSET
the plan re-filters above the call (`BIND-CAPABILITY`). A SQL UDTF inlines in Catalyst, so column pruning
and predicate pushdown still reach the base table (a Python/Scala UDTF would be opaque).
"""
from __future__ import annotations

#: `BIND-EXPLICIT-PARAMS` — the retrieve UDTF's parameters as ``(name, sql_type)``, in POSITIONAL order.
#: The Python peer of ``SqlEvalEnv.retrieveParams``; the engine always passes all seven positionally
#: (it reads no catalog to discover a signature, `ARCH-PURITY`), so a binding must declare all seven.
RETRIEVE_PARAMS = (
    ("value_set", "STRING"),
    ("code_property", "STRING"),
    ("codes", "ARRAY<STRUCT<system STRING, code STRING>>"),
    ("date_property", "STRING"),
    ("date_low", "STRING"),
    ("date_high", "STRING"),
    ("parameters", "MAP<STRING, VARIANT>"),
)


def retrieve_params_ddl() -> str:
    """The parameter list as SQL DDL text — what goes between the parentheses of ``CREATE FUNCTION``.

    Each parameter carries a DEFAULT (an empty array/map for a collection, NULL for a scalar) so a
    hand-written call can omit trailing arguments; the engine itself always passes all seven."""
    return ", ".join(f"{n} {t} DEFAULT {_param_default(t)}" for n, t in RETRIEVE_PARAMS)


def _param_default(sql_type: str) -> str:
    if sql_type.startswith("ARRAY"):
        return "array()"
    return "map()" if sql_type.startswith("MAP") else "NULL"
