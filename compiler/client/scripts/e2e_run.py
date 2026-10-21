"""End-to-end runtime-compile driver (PHASE-10, BUILD-CLIENT, CONF-GATE-SELFDIFF).

Runs the WHOLE client loop against live infrastructure: ``load_library(elm)`` -> ``cql(target)`` through a
``RemoteProvider`` (HTTP to a running compile host) -> the compiled Spark SQL script is run via
``spark.sql`` in THIS session on the SAME Connect server. Collects the per-(subject, define) result and
prints it as JSON on stdout, so a harness can self-diff it against the in-JVM SQL execution truth (the
Scala ``SqlDifferential.engineResults``).

This is a thin, dependency-light CLI over the public client API — it deliberately holds no test logic;
the assertion lives in the conformance gate that launches it. Needs pyspark + a reachable Connect server
and compile host.

Usage::

    python scripts/e2e_run.py --endpoint http://127.0.0.1:PORT --remote sc://localhost:15002 \
        --elm /path/to/Arithmetic.elm.json [--targets "Numerator,Denominator"] [--context Unfiltered]

Output (stdout, one JSON object): with ``--targets`` (a comma-separated list) each define is compiled
and executed via its own ``cql(define)`` call in ONE session -> ``{subject_id: {define: value}}`` (the
faithful ``cql(library, "define")`` usage, so no internal context column leaks in). Without ``--targets``
the whole assembled frame is executed once -> ``{subject_id: {define: value}}``. All diagnostics go to
stderr.
"""
from __future__ import annotations

import argparse
import decimal
import json
import sys

# The engine's subject-key column (SystemColumns.SubjectId) — excluded from the define columns.
SUBJECT_ID = "__subject_id"


def _jsonable(v):
    """Coerce a collected Spark cell into a JSON-comparable value. Scalars pass through; a Decimal (a CQL
    decimal divide) becomes a float so it compares to the Scala side's ujson number; a nested Row/dict/list
    is recursed. Anything exotic falls back to its string form (a mismatch then surfaces loudly, never
    silently)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    as_dict = getattr(v, "asDict", None)  # pyspark Row
    if callable(as_dict):
        return {k: _jsonable(x) for k, x in as_dict().items()}
    return str(v)


def _as_dict(row):
    """A collected row as a dict — a pyspark ``Row`` (``.asDict()``) or already a mapping (test fakes)."""
    as_dict = getattr(row, "asDict", None)
    return as_dict() if callable(as_dict) else dict(row)


def _run_mounts(spark, mounts_sql: str) -> None:
    """Run the external retrieve/value-set MOUNT script (the runtime contract the SQL path assumes,
    EMIT-SQL-EXECUTE): the ``retrieve_<Type>`` / ``valuesets`` tables the compiled script references. The
    script (the harness's ``SparkFhirData.mountsSql``) is ``;\\n``-terminated statements with one-line JSON
    VALUES, so we split on ``;\\n`` and run each (comment/blank lines dropped — ``spark.sql`` takes one
    statement at a time)."""
    for chunk in mounts_sql.split(";\n"):
        code = "\n".join(line for line in chunk.splitlines()
                         if line.strip() and not line.strip().startswith("--"))
        if code.strip():
            spark.sql(code)


def run_e2e(spark, provider, elm: str, targets, context: str = "Unfiltered",
            mounts_sql=None, conf=None) -> dict:
    """The client loop, factored out of ``main`` so it is unit-testable with fakes (no pyspark): mount the
    runtime contract, then ``load_library(elm)`` → ``cql(target)`` per target, and assemble
    ``{subject -> {define -> value}}``. ``targets`` is define names (empty/None → the whole assembled
    frame). ``conf`` is passed to ``load_library`` (``None`` → read the active Spark conf; a test passes
    ``{}`` to stay pyspark-free)."""
    from cql_databricks.compiler import cql, load_library

    library = load_library(elm=elm, provider=provider, context=context, conf=conf)
    if mounts_sql:
        _run_mounts(spark, mounts_sql)

    out: dict = {}
    names = [t.strip() for t in (targets or []) if t.strip()]
    if names:
        # The faithful cql(library).exec("define") usage: compile + execute each define on its own so the
        # result is exactly {subject -> that define's value} — no internal context-resource column.
        query = cql(library, spark=spark)
        for name in names:
            rows = query.exec(name).collect()
            print(f"[e2e_run] {len(rows)} row(s) from cql({name!r})", file=sys.stderr)
            for r in rows:
                d = _as_dict(r)
                subj = d[SUBJECT_ID]
                value_col = next(k for k in d if k != SUBJECT_ID)
                out.setdefault(subj, {})[name] = _jsonable(d[value_col])
    else:
        # The whole assembled frame in one shot: subject -> {column -> value} (may include internal
        # columns such as the context-resource struct — used for inspection, not the self-diff).
        rows = cql(library, None, spark=spark).collect()
        print(f"[e2e_run] {len(rows)} row(s) from cql(whole-frame)", file=sys.stderr)
        for r in rows:
            d = _as_dict(r)
            subj = d[SUBJECT_ID]
            out[subj] = {k: _jsonable(v) for k, v in d.items() if k != SUBJECT_ID}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cql-databricks runtime-compile E2E driver")
    ap.add_argument("--endpoint", required=True, help="compile host base URL (RemoteProvider target)")
    ap.add_argument("--remote", required=True, help="Spark Connect endpoint to execute on (sc://...)")
    ap.add_argument("--elm", required=True, help="path to the library's ELM JSON")
    ap.add_argument("--targets", default=None,
                    help="comma-separated defines to build one-by-one; omit for the whole frame")
    ap.add_argument("--context", default="Unfiltered", help="the CQL context")
    # SQL is the deploy substrate (PIPE-RUNTIME-COMPILE/EXEC-SQL-BACKEND). --mounts provides the
    # retrieve_<Type>/valuesets views the compiled script references before compiling.
    ap.add_argument("--mounts", default=None,
                    help="path to the external mount SQL script the SQL path runs before compiling")
    args = ap.parse_args(argv)

    from pyspark.sql import SparkSession
    from cql_databricks.compiler import RemoteProvider

    with open(args.elm, "r", encoding="utf-8") as f:
        elm = f.read()
    mounts_sql = None
    if args.mounts:
        with open(args.mounts, "r", encoding="utf-8") as f:
            mounts_sql = f.read()

    spark = SparkSession.builder.remote(args.remote).getOrCreate()
    provider = RemoteProvider(args.endpoint)  # NoAuth by default (localhost host)
    targets = args.targets.split(",") if args.targets else None
    out = run_e2e(spark, provider, elm, targets, context=args.context, mounts_sql=mounts_sql)

    json.dump(out, sys.stdout)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
