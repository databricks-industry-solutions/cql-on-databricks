"""DATA-ENVIRONMENT-LAYOUT — the identity of one content schema an environment holds.

An environment's knowledge and terminology are its ``knowledge`` schema, by its standard name (`CLIENT-ENVIRONMENT`). Nothing is found by tag: :class:`TaggedSchema` names a schema, the kind of content it holds and
the environment it belongs to, so a library read from it can say where it came from.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The kinds of content schema: an environment's knowledge (``layout.KNOWLEDGE``) and terminology
#: (``layout.TERMINOLOGY``).
KNOWLEDGE_TAG = "cql_databricks_knowledge"
TERMINOLOGY_TAG = "cql_databricks_terminology"


@dataclass(frozen=True)
class TaggedSchema:
    """One content schema: its kind (``tag``), the environment's name, and where it is."""
    tag: str
    name: str
    catalog: str
    schema: str
    position: int = 0

    @property
    def fqn(self) -> str:
        return f"`{self.catalog}`.`{self.schema}`"

    def table(self, name: str) -> str:
        return f"{self.fqn}.`{name}`"

    def to_dict(self) -> dict[str, str]:
        return {"tag": self.tag, "name": self.name, "catalog": self.catalog, "schema": self.schema}
