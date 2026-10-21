"""RUN-CLIENT-RESULT-IDENTITY — adapt compiler recipes without parsing their SQL."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
import json

from cql_databricks.compiler.providers import PipelinePlan

from ._durable import Definition, _selected


#: RUN-CLIENT-RESULT-IDENTITY — the compiler's identity format this client reads.
IDENTITY_FORMAT = "cql-result-identity-v1"


def _quoted(part: str) -> str:
    return "`" + part.replace("`", "``") + "`"


class PreparedPlan:
    """RUN-CLIENT-RESULT-IDENTITY — compiler-issued fingerprints and declared persisted-input seams.

    Each bound query embeds the compiler query unchanged. Outer CTEs bind its declared reads to their
    result tables; there is no replacement of names or syntax inside generated SQL.
    """

    def __init__(self, plan: PipelinePlan) -> None:
        self._recipes = {recipe.name: recipe for recipe in plan.datasets}
        if len(self._recipes) != len(plan.datasets):
            raise ValueError("duplicate compiler dataset names")
        definitions = []
        for recipe in plan.datasets:
            identity = recipe.durable
            if identity is None or identity.format != IDENTITY_FORMAT:
                raise ValueError("compiler does not supply supported durable identities; compile with durable=True "
                                 "against a compiler service from the same release as this client")
            if not recipe.query.strip():
                raise ValueError(f"compiler supplied an empty query for {recipe.name!r}")
            missing = set(recipe.reads) - self._recipes.keys()
            if missing:
                raise ValueError(f"unknown compiler dependencies for {recipe.name!r}: {sorted(missing)}")
            origin = {"library": recipe.library, "version": recipe.library_version, "define": recipe.define,
                      "kind": recipe.kind, "value_column": recipe.value_column}
            if recipe.retrieve_read is not None:
                origin["retrieve_read"] = {name: value for name, value in asdict(recipe.retrieve_read).items()
                                           if name not in ("consumed_by", "call_sites")}
            definitions.append(Definition(
                key=recipe.name, fingerprint=identity.fingerprint, reusable=identity.reusable,
                reads=tuple(recipe.reads), origin_json=json.dumps(origin)))
        self.definitions = tuple(definitions)
        if not definitions:
            raise ValueError("compiler supplied no durable datasets")
        _selected(self.definitions, tuple(self._recipes))  # Validate cycles before any storage DDL.

    def query_for(self, key: str, upstream: Mapping) -> str:
        """RUN-CLIENT-CATALOG-CHECKPOINTS — bind each declared read to its current result table."""
        recipe = self._recipes[key]
        if set(upstream) != set(recipe.reads):
            raise ValueError(f"persisted inputs do not match declared reads for {key!r}")
        bindings = []
        for read in sorted(upstream):
            table = ".".join(_quoted(part) for part in upstream[read].table)
            bindings.append(f"{_quoted(read)} AS (SELECT * FROM {table})")
        if not bindings:
            return recipe.query
        return "WITH " + ",\n".join(bindings) + "\nSELECT * FROM (\n" + recipe.query + "\n) AS __cql_durable_output"
