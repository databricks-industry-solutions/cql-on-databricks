"""DATA-CHANGE-LOG — how a data source declares that its data moved, and the exemplar view this repository's
own sources ship.

A retrieve UDTF exposes no change feed, so the implementer declares change and a reader reads the
declaration (`DATA-CONTRACT`). **The customer implements it; this repository implements one for its own
sources** (Conformance, the Synthetic Populations) as the exemplar, with :func:`change_data_log_ddl`.

The compiler's layout check (`cql_databricks.compiler.data_source`) finds the relation by name; this module holds
the exemplar DDL the loader runs for the sources it lands.

**A RELATION, NOT A UDTF, and the reason is what it is asked.** A retrieve UDTF is called per
retrieve, with a value set, a date window and a library closure, because each call asks about a
slice. This is asked ONCE PER RUN about the whole source, so every one of those parameters would be
``NULL`` and CQL on Databricks would be versioning an argument contract for something with no arguments. A
relation is readable in one statement, needs no function resolution, and is a
``CREATE OR REPLACE VIEW`` written once — the same hands-free property as the knowledge views in
``bindings.py``.

**THE SHAPE.** Two columns, exactly these names::

    data_type   STRING       -- the FHIR resource type the change concerns
    updated_at  TIMESTAMP    -- when data of that type last changed

There is no source column: the relation is registered per Data Source, so which source it describes
is the address, not a column (the same reasoning that keeps measurement period and data source id
out of `RUN-FINGERPRINT` — they are part of the address).

**BOTH SHAPES SATISFY IT, and that is deliberate.** The relation may be an append-only LOG the
loader writes one row to per change, or a VIEW that derives the marks from the tables themselves.
Either answers ``SELECT data_type, max(updated_at) GROUP BY data_type``, which is the only way
anything reads it.

**``updated_at`` IS A CHANGE TIME, NEVER A CLINICAL DATE. This is the rule that matters most.**
``max`` over a timestamp only sees a change that lands ABOVE the current maximum. If ``updated_at``
carried an effective or service date, a backdated encounter loaded today would sit below the mark,
move nothing, and every measure that should have changed would report itself current — a stale
quality measure with no error anywhere. It must be the moment the row CHANGED: commit time, load
time, ``meta.lastUpdated``, file modification time. Late-arriving data is normal in claims and it is
exactly the case this rule protects.

**DELETES MUST BE REPRESENTED.** A retracted encounter or a voided procedure changes a denominator
with nothing added. An append-only log satisfies this by logging the deletion as an event; a view
satisfies it if what it maxes over moves on delete (file modification time does, because a delete
rewrites files or writes a deletion vector). A view over ``meta.lastUpdated`` alone does NOT — a
hard-deleted resource takes its timestamp with it — so a source that hard-deletes needs the log
shape or a file-level mark.

**NOTHING VOLATILE MAY APPEAR IN IT.** A well-meaning ``current_timestamp()`` inverts the whole
mechanism: the mark moves on every read, so every fingerprint moves on every run and nothing is ever
current again. It looks like good hygiene and it is the most expensive mistake available here.
"""
from __future__ import annotations

from cql_databricks.compiler.data_source import CHANGE_DATA_LOG
from cql_databricks.result.relation import literal, quote

#: The two column names the relation must expose. Not configurable: a per-source column mapping
#: would be a second contract to get wrong, and the value of this being a relation is that reading
#: it needs no per-source knowledge.
DATA_TYPE_COLUMN = "data_type"
UPDATED_AT_COLUMN = "updated_at"

#: The conventional relation name this repository's sources use, and the name the compiler's layout check
#: looks for beside a data source's mounts (`cql_databricks.compiler.data_source.CHANGE_DATA_LOG`).
CHANGE_DATA_LOG_NAME = CHANGE_DATA_LOG


def change_data_log_ddl(view_fqn: str, tables: dict[str, str]) -> str:
    """The `DATA-CHANGE-LOG` relation for ``tables`` — ``{data_type: table_fqn}``.

    This is the exemplar, and the `DATA-VIEW-SCAFFOLD` rule applies to it: **CQL on Databricks authors the DDL;
    the implementer owns the deployed object.** CQL on Databricks runs it itself only for the sources CQL on Databricks
    owns — Conformance and the synthetic populations — which are its reference realization of the contract, never
    customer structural mapping.

    **``updated_at`` is the newest file modification time of the table**, which is a change time in
    exactly the sense the contract requires, and is chosen because it needs NO column in the data and
    NO table property enabled — so it works on any Delta table:

    * an **insert** writes a file, moving it;
    * an **update** rewrites files, moving it;
    * a **delete** rewrites files or writes a deletion vector, moving it — which is what a mark
      derived from ``meta.lastUpdated`` would miss;
    * it can only move FORWARD in normal operation, so it is a true high-water mark, and a restore
      that moves it backwards still DIFFERS, which is all a fingerprint compares.

    It over-triggers on ``OPTIMIZE`` and ``VACUUM``, which move file identity without moving data.
    That is the safe direction, and it is why this variant is the exemplar rather than one over a
    resource timestamp: the cost is a redundant recompute, where the failure it forecloses is a
    stale measure.
    """
    if not tables:
        raise ValueError(
            f"DATA-CHANGE-LOG: refusing to create {view_fqn} over no tables. An empty log claims "
            f"nothing.")

    selects = [
        f"SELECT {literal(data_type)} AS {quote(DATA_TYPE_COLUMN)},\n"
        f"       max(`_metadata`.`file_modification_time`) AS {quote(UPDATED_AT_COLUMN)}\n"
        f"  FROM {table}"
        for data_type, table in sorted(tables.items())
    ]
    return (f"CREATE OR REPLACE VIEW {view_fqn}\n"
            f"COMMENT 'DATA-CHANGE-LOG: when data of each type last changed. Read once per run by "
            f"CQL on Databricks to decide what to recompute.'\n"
            f"AS\n" + "\nUNION ALL\n".join(selects))

