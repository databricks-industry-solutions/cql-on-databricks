"""DOC-EDITABLE-IN-SCOPE — whether the user's own grants make a write to a tagged store.

The rows are what `access_sql` returns: the user's privileges and ownerships at each level, their account groups'
included. These pin the combination, which is the only judgment the Designer adds to Databricks' record.
"""
from cql_databricks_designer.store_access import access_sql, store_refusal, write_refusal

CAT, SCH = "k", "measures"


def rows(*held):
    return [{"level": level, "name": name, "privilege": privilege} for level, name, privilege in held]


WRITER = rows(("catalog", CAT, "USE_CATALOG"), ("schema", SCH, "USE_SCHEMA"),
              ("table", "library", "SELECT"), ("table", "library", "MODIFY"),
              ("table", "measure", "SELECT"), ("table", "measure", "MODIFY"))


def test_use_select_and_modify_at_every_level_make_a_write():
    assert write_refusal(WRITER, CAT, SCH) is None


def test_all_privileges_or_ownership_stand_for_every_privilege_on_that_object():
    held = rows(("catalog", CAT, "OWNER"), ("schema", SCH, "ALL_PRIVILEGES"),
                ("table", "library", "ALL_PRIVILEGES"), ("table", "measure", "OWNER"))
    assert write_refusal(held, CAT, SCH) is None


def test_a_reader_is_told_what_they_lack():
    held = rows(("catalog", CAT, "USE_CATALOG"), ("schema", SCH, "USE_SCHEMA"),
                ("table", "library", "SELECT"), ("table", "measure", "SELECT"))
    assert write_refusal(held, CAT, SCH) == (
        "You cannot write k.measures: you do not hold MODIFY on k.measures.library, MODIFY on k.measures.measure.")


def test_manage_is_not_a_write():
    held = rows(("catalog", CAT, "MANAGE"), ("schema", SCH, "MANAGE"),
                ("table", "library", "MANAGE"), ("table", "measure", "MANAGE"))
    reason = write_refusal(held, CAT, SCH)
    assert reason is not None and "USE CATALOG on k" in reason and "SELECT on k.measures.library" in reason


def test_no_grants_at_all_refuses():
    assert write_refusal([], CAT, SCH).startswith("You cannot write k.measures: you do not hold USE CATALOG on k")


def test_the_check_asks_as_the_user_with_the_store_bound_as_parameters():
    asked = []

    def run(sql, params):
        asked.append((sql, params))
        return WRITER

    assert store_refusal(run, CAT, SCH) is None
    sql, params = asked[0]
    assert params == {"catalog": CAT, "schema": SCH, "tables": '["library", "measure"]'}
    assert "`k`.information_schema.table_privileges" in sql and "current_user()" in sql


def test_a_catalog_name_cannot_break_out_of_its_identifier():
    assert "`we``ird`.information_schema" in access_sql("we`ird")
