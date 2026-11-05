"""CLIENT-RESOLVER-IDENTITY, LANG-VALUESET-VERSION — a kept result's value-set digest covers the codes the compiled SQL
reads: the first relation that holds the value set, and only the version its declaration pins."""
import unittest

from cql_databricks.resolvers.interfaces import ValueSetRef
from cql_databricks.resolvers.value_set_digest import value_set_digests

VS = "urn:vs:a"


def _runner(rows):
    statements = []

    def run(statement, params=None):
        statements.append(statement)
        return rows
    run.statements = statements
    return run


class ValueSetDigestTest(unittest.TestCase):

    def test_a_pinned_value_set_digests_only_its_version(self):
        """LANG-VALUESET-VERSION — another version's codes do not move a pinned value set's digest."""
        run = _runner([{"layer": 0, "valueset_id": VS, "version": "1", "digest": "d1"},
                       {"layer": 0, "valueset_id": VS, "version": "2", "digest": "d2"}])
        pinned = ValueSetRef(id=VS, version="2")
        assert value_set_digests(run, ["acme.knowledge.valueset"], [pinned]) == {pinned: "d2"}
        assert "GROUP BY layer, valueset_id, version" in run.statements[0]

    def test_an_unpinned_value_set_with_one_version_digests_it(self):
        unpinned = ValueSetRef(id=VS)
        run = _runner([{"layer": 0, "valueset_id": VS, "version": "", "digest": "d0"}])
        assert value_set_digests(run, ["acme.knowledge.valueset"], [unpinned]) == {unpinned: "d0"}

    def test_the_first_relation_that_holds_a_value_set_supplies_its_digest(self):
        """BIND-CALL-SITE — the layer is chosen by id, as the compiled SQL chooses it, then the version inside it."""
        pinned = ValueSetRef(id=VS, version="1")
        run = _runner([{"layer": 1, "valueset_id": VS, "version": "1", "digest": "base"},
                       {"layer": 0, "valueset_id": VS, "version": "2", "digest": "overlay"}])
        assert value_set_digests(run, ["dev.valueset", "acme.knowledge.valueset"], [pinned]) == {pinned: ""}

    def test_two_pins_of_one_value_set_digest_apart(self):
        one, two = ValueSetRef(id=VS, version="1"), ValueSetRef(id=VS, version="2")
        run = _runner([{"layer": 0, "valueset_id": VS, "version": "1", "digest": "d1"},
                       {"layer": 0, "valueset_id": VS, "version": "2", "digest": "d2"}])
        assert value_set_digests(run, ["acme.knowledge.valueset"], [one, two]) == {one: "d1", two: "d2"}
        assert len(run.statements) == 1


if __name__ == "__main__":
    unittest.main()
