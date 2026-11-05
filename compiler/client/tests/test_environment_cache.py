"""CLIENT-ENVIRONMENT-CACHE, CLIENT-DISCOVERY-PARALLEL — an App keeps each caller's environments for its process, and
discovery runs the governance check beside the environment query."""
from __future__ import annotations

import threading
import unittest
import warnings

from cql_databricks.resolvers import environment
from cql_databricks.resolvers.environment import (DEFAULT_TAG, NAME_TAG, VERSION_TAG, EnvironmentCache,
                                                   EnvironmentLookupError, EnvironmentResolver,
                                                   UngovernedEnvironmentWarning, discover_environments)

GOVERNED = [{"tag_key": tag} for tag in (NAME_TAG, VERSION_TAG, DEFAULT_TAG)]


def _tags(*environments: tuple[str, str, bool]) -> list[dict]:
    rows = []
    for name, catalog, default in environments:
        rows += [{"catalog": catalog, "tag": NAME_TAG, "value": name, "comment": None},
                 {"catalog": catalog, "tag": VERSION_TAG, "value": "3", "comment": None}]
        if default:
            rows.append({"catalog": catalog, "tag": DEFAULT_TAG, "value": "true", "comment": None})
    return rows


class _Catalogs:
    """The catalogs one caller can see, changeable between reads; counts the environment queries."""

    def __init__(self, *environments: tuple[str, str, bool]) -> None:
        self.rows = _tags(*environments)
        self.reads = 0

    def __call__(self, sql, params=None):
        if "governed_tags" in sql:
            return GOVERNED
        self.reads += 1
        return self.rows


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Checked(unittest.TestCase):
    """Each test starts with the process's governance check done, unless it says otherwise."""

    def setUp(self) -> None:
        self._before = environment._governance_checked
        environment._governance_checked = True

    def tearDown(self) -> None:
        environment._governance_checked = self._before


class EnvironmentCacheTest(_Checked):
    def test_a_callers_list_is_found_once_for_many_calls(self):
        """CLIENT-ENVIRONMENT-CACHE — each call builds a new resolver, as an App's tool calls do; one query in all."""
        cache, run = EnvironmentCache(), _Catalogs(("example", "example_cat", True))
        for _ in range(3):
            self.assertEqual(cache.resolver("a@example.com", run).default().catalog, "example_cat")
        self.assertEqual(run.reads, 1)

    def test_callers_never_share_a_list(self):
        """CLIENT-ENVIRONMENT-CACHE — Unity Catalog shows each caller their own catalogs."""
        cache = EnvironmentCache()
        a, b = _Catalogs(("team", "a_cat", True)), _Catalogs(("team", "b_cat", True))
        self.assertEqual(cache.resolver("a", a).get("team").catalog, "a_cat")
        self.assertEqual(cache.resolver("b", b).get("team").catalog, "b_cat")
        self.assertEqual(cache.resolver("a", a).get("team").catalog, "a_cat")
        self.assertEqual((a.reads, b.reads), (1, 1))

    def test_a_list_older_than_its_max_age_is_found_again(self):
        """CLIENT-ENVIRONMENT-CACHE — a catalog retagged while the old one still exists is seen after max_age."""
        clock = _Clock()
        cache, run = EnvironmentCache(max_age=300, clock=clock), _Catalogs(("example", "old_cat", True))
        self.assertEqual(cache.resolver("a", run).default().catalog, "old_cat")
        run.rows = _tags(("example", "new_cat", True))
        clock.now = 299
        self.assertEqual(cache.resolver("a", run).default().catalog, "old_cat")
        clock.now = 300
        self.assertEqual(cache.resolver("a", run).default().catalog, "new_cat")
        self.assertEqual(run.reads, 2)

    def test_a_name_not_held_is_looked_up_again_once_before_it_fails(self):
        """CLIENT-ENVIRONMENT-CACHE — an environment tagged after the list was held is found; a missing one still fails,
        naming what the caller can see."""
        cache, run = EnvironmentCache(), _Catalogs(("example", "example_cat", True))
        cache.resolver("a", run).environments()
        run.rows = _tags(("example", "example_cat", True), ("fresh", "fresh_cat", False))
        self.assertEqual(cache.resolver("a", run).get("fresh").catalog, "fresh_cat")
        self.assertEqual(run.reads, 2)
        with self.assertRaisesRegex(EnvironmentLookupError, "no environment named 'gone'.*example, fresh"):
            cache.resolver("a", run).get("gone")
        self.assertEqual(run.reads, 3)

    def test_a_default_missing_from_a_held_list_is_looked_up_again(self):
        """CLIENT-ENVIRONMENT-CACHE — the default tag was set after the list was held; two defaults still fail."""
        cache, run = EnvironmentCache(), _Catalogs(("one", "one_cat", False), ("two", "two_cat", False))
        cache.resolver("a", run).environments()
        run.rows = _tags(("one", "one_cat", False), ("two", "two_cat", True))
        self.assertEqual(cache.resolver("a", run).default().catalog, "two_cat")
        self.assertEqual(run.reads, 2)
        run.rows = _tags(("one", "one_cat", True), ("two", "two_cat", True))
        cache.drop("a")
        with self.assertRaisesRegex(EnvironmentLookupError, "2 catalogs are tagged"):
            cache.resolver("a", run).default()

    def test_a_resolver_with_no_cache_never_looks_again(self):
        """CLIENT-ENVIRONMENT-RESOLVER — a notebook's resolver reads once, and a missing name fails at once."""
        run = _Catalogs(("example", "example_cat", True))
        with self.assertRaises(EnvironmentLookupError):
            EnvironmentResolver(run).get("gone")
        self.assertEqual(run.reads, 1)

    def test_the_generation_moves_when_a_list_changes_or_is_dropped_only(self):
        """CLIENT-ENVIRONMENT-CACHE — a cache stamp keyed on it is not busted by an expiry that found the same list."""
        clock = _Clock()
        cache, run = EnvironmentCache(max_age=10, clock=clock), _Catalogs(("example", "example_cat", True))
        cache.environments("a", run)
        first = cache.generation
        clock.now = 20
        cache.environments("a", run)
        self.assertEqual(cache.generation, first)
        run.rows = _tags(("example", "other_cat", True))
        clock.now = 40
        cache.environments("a", run)
        self.assertGreater(cache.generation, first)
        changed = cache.generation
        cache.drop("a")
        self.assertGreater(cache.generation, changed)


class ParallelDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._before = environment._governance_checked
        environment._governance_checked = False

    def tearDown(self) -> None:
        environment._governance_checked = self._before

    def test_the_governance_check_runs_beside_the_environment_query(self):
        """CLIENT-DISCOVERY-PARALLEL — each query waits for the other to start; run one after the other, neither
        would, and the barrier breaks."""
        both = threading.Barrier(2, timeout=5)
        threads = set()

        def run(sql, params=None):
            threads.add(threading.get_ident())
            both.wait()
            return GOVERNED if "governed_tags" in sql else _tags(("example", "example_cat", True))

        found = discover_environments(run)
        self.assertEqual([e.name for e in found.environments], ["example"])
        self.assertIsNone(found.note)
        self.assertEqual(len(threads), 2)

    def test_the_governance_warning_is_given_on_the_thread_that_takes_the_list(self):
        """CLIENT-DISCOVERY-PARALLEL — a discovery run on another thread warns only when its list is taken."""
        def run(sql, params=None):
            return [] if "governed_tags" in sql else _tags(("example", "example_cat", True))

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            found = discover_environments(run)
        with self.assertWarnsRegex(UngovernedEnvironmentWarning, "are not governed"):
            self.assertEqual([e.name for e in found.emit()], ["example"])

    def test_the_check_runs_once_per_process(self):
        """CLIENT-ENVIRONMENT — a second discovery reads only the environments."""
        calls = []

        def run(sql, params=None):
            calls.append(sql)
            return GOVERNED if "governed_tags" in sql else _tags(("example", "example_cat", True))

        discover_environments(run)
        discover_environments(run)
        self.assertEqual(sum("governed_tags" in sql for sql in calls), 1)
        self.assertEqual(sum("catalog_tags" in sql for sql in calls), 2)


if __name__ == "__main__":
    unittest.main()
