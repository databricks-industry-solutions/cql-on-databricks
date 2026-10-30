"""DATA-BINDINGS — provision knowledge views in the ``runtime`` schema.

On bind, this module creates or replaces:

1. **Knowledge views** — ``measures``, ``libraries`` and ``valuesets``, each a UNION ALL over the
   matching table of every schema data-bindings finds by tag, so the pipeline can resolve
   knowledge artifacts without knowing the underlying tables.

These views live in the ``runtime`` schema (alongside cql_* UDFs) to avoid schema explosion.
Data-source retrieve UDTFs are NOT created here: a data source is a tagged schema that already holds
them (`DATA-SOURCE-UC-TAG`).

**Who binds** is the caller's runner, ``run`` (`DEC-EVALUATOR-APP-REHOME`): the deploy binds once as the deployer
(``scripts/deploy/provision-examples.py``, `DATA-BIND-AUTO`), and the Designer's Bind action and knowledge reload
rebind as the signed-in user (`SEC-OBO`). ``CREATE OR REPLACE VIEW`` needs ownership of the view, so only a
principal that owns the views can rebind them; anyone else gets Unity Catalog's permission error.
Moved from the evaluator App by EVALUATOR-REMOVE.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from cql_databricks.data_bindings import ContentResolver, SqlRunner

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bind step model (DATA-BIND-PROGRESS)
# ---------------------------------------------------------------------------

@dataclass
class BindStep:
    """One discrete unit of work in the bind provisioning plan.

    ``label`` is a short human-readable description (e.g. "Measures view",
    "retrieve_Patient UDTF") shown in the progress UI.  ``execute`` runs the
    SQL against the warehouse; it returns the object name on success or raises.
    """
    label: str
    execute: Callable[[], str]


# The knowledge views, the layout table each one unions, and the columns it carries. Columns are named,
# never `*`: a tagged schema may carry more than its layout requires (provenance, `display`), and a
# `SELECT *` union over two such schemas fails on the first one that differs.
# The last field is the view's key: CLIENT-DISCOVERY-SCOPE keeps each key from its earliest scope only.
_KNOWLEDGE_VIEWS = (
    ("measures", "knowledge", "measure", ("url", "version", "resource"), "url, version"),
    ("libraries", "knowledge", "library", ("url", "version", "resource"), "url, version"),
    ("valuesets", "terminology", "valueset", ("valueset_id", "system", "code", "version"), "valueset_id"),
)


def _view_ddls(schema_fqn: str, content: ContentResolver) -> list[tuple[str, str, int]]:
    """``(view fqn, DDL, source count)`` for each knowledge view that has at least one tagged source.

    The sources are the tagged schemas (`CONTENT-RESOLVER`): ``cql_databricks_knowledge`` for
    measures and libraries, ``cql_databricks_terminology`` for value sets. CLIENT-DISCOVERY-SCOPE: a key
    held in more than one scope comes from the earliest only; sources in the same scope are all kept.
    """
    sources = {"knowledge": content.knowledge_sources(), "terminology": content.terminology_sources()}
    out = []
    for view, kind, table, columns, key in _KNOWLEDGE_VIEWS:
        tagged = sources[kind]
        if not tagged:
            continue
        names = ", ".join(columns)
        selects = [f"SELECT {names}, {s.position} AS _scope FROM {s.table(table)}" for s in tagged]
        out.append((f"{schema_fqn}.{view}",
                    f"CREATE OR REPLACE VIEW {schema_fqn}.{view} AS\n"
                    f"SELECT {names} FROM (\nSELECT *, MIN(_scope) OVER (PARTITION BY {key}) AS _first FROM (\n"
                    + "\nUNION ALL\n".join(selects) + "\n))\nWHERE _scope = _first",
                    len(tagged)))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan_bind_steps(run: SqlRunner, catalog: str, schema: str, content: ContentResolver) -> list[BindStep]:
    """Build an ordered list of discrete bind steps for the knowledge views in ``catalog.schema``
    (DATA-BIND-PROGRESS), each run through ``run`` (RUN-CLIENT-WAREHOUSE-CONNECTOR: a warehouse session).

    Each step is a callable that executes one SQL statement (knowledge view).
    The caller drives the loop and can emit progress events between steps.
    """
    schema_fqn = f"{catalog}.{schema}"
    steps: list[BindStep] = []

    # Step 0: ensure the runtime schema exists.
    def _create_schema() -> str:
        run(f"CREATE SCHEMA IF NOT EXISTS {schema_fqn}")
        return schema_fqn
    steps.append(BindStep(label=f"{schema_fqn} schema", execute=_create_schema))

    for fqn, ddl, n in _view_ddls(schema_fqn, content):
        def _make_view(ddl=ddl, fqn=fqn, n=n):
            def _run() -> str:
                log.info("Creating knowledge view %s (%d source(s))", fqn, n)
                run(ddl)
                return fqn
            return _run
        steps.append(BindStep(label=f"{fqn.rsplit('.', 1)[-1]} view", execute=_make_view()))

    return steps


def bind_knowledge_views(run: SqlRunner, catalog: str, schema: str, content: ContentResolver) -> list[str]:
    """DATA-BIND-AUTO — run every bind step in order and return what each created; raise on the first failure.

    The views union the tagged schemas that exist WHEN they are created, so the deploy runs this once (a
    fresh deploy must not need a manual Bind before its first run) and the Designer runs it after every
    knowledge reload (a newly imported terminology schema joins ``valuesets`` without one either). The UI's
    Bind action runs the same steps one at a time for its progress stream (`plan_bind_steps`).
    """
    return [step.execute() for step in plan_bind_steps(run, catalog, schema, content)]
