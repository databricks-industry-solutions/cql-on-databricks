"""DATA-BINDINGS — tests for knowledge view provisioning."""
from __future__ import annotations


# --- Bind step planning (DATA-BIND-PROGRESS) --------------------------------

def test_plan_bind_steps_produces_knowledge_view_steps():
    """plan_bind_steps returns schema + knowledge view steps (DATA-BIND-PROGRESS)."""
    from cql_databricks.data_bindings.bindings import plan_bind_steps

    # CONTENT-RESOLVER — the sources are tagged schemas, one knowledge and one terminology.
    from cql_databricks.data_bindings import KNOWLEDGE_TAG, TERMINOLOGY_TAG, TaggedSchema
    content = type("Content", (), {
        "knowledge_sources": lambda self: [TaggedSchema(KNOWLEDGE_TAG, "cms", "cat", "ecqm")],
        "terminology_sources": lambda self: [TaggedSchema(TERMINOLOGY_TAG, "vsac", "cat", "vsac")],
    })()

    steps = plan_bind_steps(object(), "cat", "runtime", content, warehouse_id="wh-123")

    labels = [s.label for s in steps]
    # 1 schema + 3 knowledge views = 4 steps; no UDTF scaffolds (DATA-SOURCE-UC-TAG)
    assert len(steps) == 4
    assert labels[0] == "cat.runtime schema"
    assert "measures view" in labels
    assert "libraries view" in labels
    assert "valuesets view" in labels


def test_bind_knowledge_views_runs_every_step_in_order():
    """DATA-BIND-AUTO — the deploy and the knowledge reload bind the views without the UI's Bind action."""
    from unittest.mock import MagicMock, patch

    from cql_databricks.data_bindings import KNOWLEDGE_TAG, TERMINOLOGY_TAG, TaggedSchema
    from cql_databricks.data_bindings.bindings import bind_knowledge_views

    content = MagicMock()
    content.knowledge_sources.return_value = [TaggedSchema(KNOWLEDGE_TAG, "ecqm", "cat", "ecqm")]
    content.terminology_sources.return_value = [TaggedSchema(TERMINOLOGY_TAG, "vsac", "cat", "vsac")]
    executed: list[str] = []
    with patch("cql_databricks.data_bindings.bindings._exec",
               side_effect=lambda _w, _wh, sql: executed.append(sql)):
        created = bind_knowledge_views(object(), "cat", "runtime", content, warehouse_id="wh-1")

    assert created[0] == "cat.runtime"
    assert [c.rsplit(".", 1)[-1] for c in created[1:]] == ["measures", "libraries", "valuesets"]
    assert executed[0].startswith("CREATE SCHEMA IF NOT EXISTS cat.runtime")
    assert "CREATE OR REPLACE VIEW cat.runtime.valuesets" in executed[-1]


def test_bind_knowledge_views_raises_on_a_failed_step():
    """DATA-BIND-AUTO — a failed bind is the caller's to report, never a silent partial bind."""
    from unittest.mock import MagicMock, patch

    import pytest

    from cql_databricks.data_bindings.bindings import bind_knowledge_views

    content = MagicMock()
    content.knowledge_sources.return_value = []
    content.terminology_sources.return_value = []
    with patch("cql_databricks.data_bindings.bindings._exec", side_effect=RuntimeError("denied")):
        with pytest.raises(RuntimeError, match="denied"):
            bind_knowledge_views(object(), "cat", "runtime", content, warehouse_id="wh-1")


def test_knowledge_views_name_their_layout_columns_over_tagged_tables():
    """A `SELECT *` union fails when two tagged schemas carry different extra columns; the views name the
    layout's columns instead, over the resolver's tagged tables (CONTENT-RESOLVER)."""
    from cql_databricks.data_bindings.bindings import _view_ddls
    from cql_databricks.data_bindings import KNOWLEDGE_TAG, TERMINOLOGY_TAG, TaggedSchema
    content = type("Content", (), {
        "knowledge_sources": lambda self: [TaggedSchema(KNOWLEDGE_TAG, "a", "c", "a"),
                                           TaggedSchema(KNOWLEDGE_TAG, "b", "c", "b")],
        "terminology_sources": lambda self: [],
    })()
    ddls = {fqn: ddl for fqn, ddl, _ in _view_ddls("cat.runtime", content)}
    assert set(ddls) == {"cat.runtime.measures", "cat.runtime.libraries"}
    assert ddls["cat.runtime.libraries"] == (
        "CREATE OR REPLACE VIEW cat.runtime.libraries AS\n"
        "SELECT url, version, resource FROM (\n"
        "SELECT *, MIN(_scope) OVER (PARTITION BY url, version) AS _first FROM (\n"
        "SELECT url, version, resource, 0 AS _scope FROM `c`.`a`.`library`\nUNION ALL\n"
        "SELECT url, version, resource, 0 AS _scope FROM `c`.`b`.`library`\n))\n"
        "WHERE _scope = _first")


def test_knowledge_views_keep_each_key_from_its_earliest_scope():
    """CLIENT-DISCOVERY-SCOPE — each source carries its scope position, and a view keeps a key only where it is
    lowest: a library by (url, version), a value set by valueset_id. Sources in one scope share a position."""
    from cql_databricks.data_bindings.bindings import _view_ddls
    from cql_databricks.data_bindings import KNOWLEDGE_TAG, TERMINOLOGY_TAG, TaggedSchema
    content = type("Content", (), {
        "knowledge_sources": lambda self: [TaggedSchema(KNOWLEDGE_TAG, "dev", "dev", "ecqm", 0)],
        "terminology_sources": lambda self: [TaggedSchema(TERMINOLOGY_TAG, "local", "dev", "vs", 0),
                                             TaggedSchema(TERMINOLOGY_TAG, "vsac", "shared", "vsac", 1)],
    })()
    ddls = {fqn: ddl for fqn, ddl, _ in _view_ddls("cat.runtime", content)}
    valuesets = ddls["cat.runtime.valuesets"]
    assert "MIN(_scope) OVER (PARTITION BY valueset_id) AS _first" in valuesets
    assert "0 AS _scope FROM `dev`.`vs`.`valueset`" in valuesets
    assert "1 AS _scope FROM `shared`.`vsac`.`valueset`" in valuesets
    assert valuesets.endswith("WHERE _scope = _first")
    assert "PARTITION BY url, version" in ddls["cat.runtime.measures"]
