"""data-bindings (CONTENT-RESOLVER, DEC-DATA-BINDINGS-HOME): the resolver, a feature of its own since SPLIT-CONTENT.

No store, no Lakebase and no App: the tagged UC tables are the store of record, written by ``measure_import`` (``CONTENT-IMPORT-MEASURES``). This
library finds them by tag, checks them against the layouts it owns (DEC-KNOWLEDGE-LAYOUT-HOME), and reads the
knowledge corpus. Every read goes through an injected SQL runner, so a notebook (``spark_runner``) and a SQL
warehouse (``warehouse_session``) share one implementation. data-bindings depends on no other feature.
"""
from .corpus import Corpus, Library, LibraryId, ResolvedClosure, ResolvedLibrary, load_corpus
from .cql import (IncludeRef, compare_versions, parse_identity, parse_includes, parse_model,
                  parse_resource_types, parse_value_set_declarations, parse_value_sets)
from .layout import KNOWLEDGE, TERMINOLOGY, check_layout, layout_problems
from .resolver import ContentResolver
from .runner import (ExecutedStatement, ResolverError, RunningStatement, SessionRunner, SqlRunner, StatementObserver,
                     WarehouseSession, background_runner, spark_runner, start_statement, warehouse_session)
from .tags import (DATA_SOURCE_TAG, EVERYWHERE, KNOWLEDGE_TAG, TERMINOLOGY_TAG, CatalogScope, TaggedSchema,
                   declaration_statements, find_tagged, parse_scopes, tag_statement)

__all__ = [
    "CatalogScope", "ContentResolver", "Corpus", "DATA_SOURCE_TAG", "EVERYWHERE", "ExecutedStatement", "IncludeRef", "KNOWLEDGE",
    "KNOWLEDGE_TAG", "Library", "LibraryId", "ResolvedClosure", "ResolvedLibrary", "ResolverError", "RunningStatement", "SessionRunner", "SqlRunner",
    "StatementObserver", "TERMINOLOGY", "TERMINOLOGY_TAG", "TaggedSchema", "WarehouseSession", "check_layout", "compare_versions",
    "background_runner", "declaration_statements",
    "find_tagged", "layout_problems", "load_corpus", "parse_identity", "parse_includes", "parse_model",
    "parse_resource_types", "parse_scopes", "parse_value_set_declarations", "parse_value_sets", "spark_runner", "start_statement", "tag_statement",
    "warehouse_session",
]
