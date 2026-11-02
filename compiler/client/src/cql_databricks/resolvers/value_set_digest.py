"""CLIENT-RESOLVER-IDENTITY, PIPE-DURABLE-IDENTITY: a kept result's fingerprint completed with the codes of its value
sets.

The compiler reads no catalog, so its fingerprint covers a value set's relation by name only, and lists, for each
recipe, the value sets its define or any define it reads uses. The client reads a digest of each listed value set's
codes in one query per kept run and folds them into the fingerprint. So a value set edited in a volume, or republished
in place, gets a new fingerprint, and an unchanged one keeps its own. A value set is read as the compiled SQL reads it:
from the first terminology relation that holds it (`BIND-CALL-SITE`).

Part of the client core: SQL goes through the caller's SQL runner.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace

from cql_databricks.compiler.providers import PipelinePlan
from cql_databricks.compiler.reshape import quote_name


def value_set_digests(run, tables: Sequence[str], ids: Sequence[str]) -> dict[str, str]:
    """The digest of each value set in ``ids``: a SHA-256 of its sorted ``(version, system, code)`` rows in the first
    of ``tables`` that holds it. One query. A value set no table holds digests as empty."""
    ids = sorted(set(ids))
    if not ids or not tables:
        return {i: "" for i in ids}
    listed = ", ".join("'" + i.replace("\\", "\\\\").replace("'", "\\'") + "'" for i in ids)
    layers = " UNION ALL ".join(
        f"SELECT {n} AS layer, valueset_id, coalesce(version, '') AS version, system, code "
        f"FROM {quote_name(t)} WHERE valueset_id IN ({listed})" for n, t in enumerate(tables))
    sql = (f"SELECT layer, valueset_id, sha2(concat_ws('\\n', sort_array(collect_list("
           f"concat_ws('|', version, system, code)))), 256) AS digest FROM ({layers}) GROUP BY layer, valueset_id")
    first: dict[str, tuple[int, str]] = {}
    for row in run(sql):
        held = first.get(row["valueset_id"])
        if held is None or int(row["layer"]) < held[0]:
            first[row["valueset_id"]] = (int(row["layer"]), row["digest"])
    return {i: first[i][1] if i in first else "" for i in ids}


def complete_fingerprints(plan: PipelinePlan, tables: Sequence[str], run) -> PipelinePlan:
    """``plan`` with each durable recipe's fingerprint folded with the digests of its value sets. A recipe that uses
    none keeps the compiler's fingerprint."""
    wanted = {v.id for recipe in plan.datasets if recipe.durable for v in recipe.durable.value_sets}
    if not wanted:
        return plan
    digests = value_set_digests(run, tables, sorted(wanted))
    datasets = []
    for recipe in plan.datasets:
        identity = recipe.durable
        if identity is None or not identity.value_sets:
            datasets.append(recipe)
            continue
        folded = json.dumps({"fingerprint": identity.fingerprint,
                             "valueSets": sorted([v.id, digests[v.id]] for v in identity.value_sets)},
                            sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(folded.encode("utf-8")).hexdigest()
        datasets.append(replace(recipe, durable=replace(identity, fingerprint=fingerprint)))
    return replace(plan, datasets=tuple(datasets) if isinstance(plan.datasets, tuple) else datasets)
