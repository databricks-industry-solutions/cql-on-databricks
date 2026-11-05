"""Everything a CQL run reads and writes through: data, terminology, knowledge and results (`CLIENT-RESOLVERS`).

A run takes resolvers and nothing else. :mod:`.interfaces` holds the four roles, the result store and the
:class:`Resolvers` one run takes; :mod:`.data`, :mod:`.terminology` and :mod:`.knowledge` the shipped resolvers;
:mod:`.environment` the tagged environments and the resolvers each one builds (`CLIENT-ENVIRONMENT-RESOLVER`);
:mod:`.value_set_digest` a kept result's value-set codes (`CLIENT-RESOLVER-IDENTITY`). The Delta result resolver and
store are :mod:`cql_databricks.result.store`. The compiler itself is stateless and reads none of these
(`cql_databricks.compiler` calls it).

Part of the client core: SQL goes through the caller's SQL runner; nothing here imports Spark or a platform SDK.
"""
# The compiler package first: its modules import these resolvers, and the knowledge resolvers read its libraries.
import cql_databricks.compiler  # noqa: F401

from .interfaces import (CallSite, Code, DataFrameRelation, DataResolver, IncludeSite, KeptResult, KnowledgeResolver,
                         KnowledgeStore, Overlay, PublishReport, ResultKey, ResultResolver, Resolvers, ResultStore,
                         ResultWrites, RetrieveRequest, TableRef, TableRelation, TerminologyResolver, TerminologyStore,
                         ValueSet, ValueSetRef, VolumeRelation)
from .data import DataFrameDataResolver, SchemaDataResolver, TableDataResolver, VolumeDataResolver
from .terminology import DataFrameTerminologyResolver, FolderTerminologyResolver, TableTerminologyResolver
from .knowledge import CellsKnowledgeResolver, FolderKnowledgeResolver, TableKnowledgeResolver
from .environment import (Environment, EnvironmentCache, EnvironmentLookupError, EnvironmentResolver,
                          UngovernedEnvironmentWarning)

__all__ = [
    # CLIENT-RESOLVERS: implement these to bring your own data, value sets, knowledge or results.
    "DataResolver", "TerminologyResolver", "TerminologyStore", "KnowledgeResolver", "KnowledgeStore",
    "ResultResolver", "ResultStore", "ResultWrites", "ResultKey", "KeptResult", "Resolvers", "Overlay",
    "RetrieveRequest", "CallSite", "IncludeSite", "Code", "TableRelation", "VolumeRelation", "DataFrameRelation",
    "ValueSetRef", "ValueSet", "PublishReport", "TableRef",
    # The shipped ones.
    "SchemaDataResolver", "TableDataResolver", "VolumeDataResolver", "DataFrameDataResolver",
    "TableTerminologyResolver", "FolderTerminologyResolver", "DataFrameTerminologyResolver",
    "CellsKnowledgeResolver", "FolderKnowledgeResolver", "TableKnowledgeResolver",
    # CLIENT-ENVIRONMENT-RESOLVER: the environments, and the resolvers each one builds. CLIENT-ENVIRONMENT-CACHE: an
    # App's environments, kept per caller.
    "Environment", "EnvironmentResolver", "EnvironmentCache", "EnvironmentLookupError", "UngovernedEnvironmentWarning",
]
