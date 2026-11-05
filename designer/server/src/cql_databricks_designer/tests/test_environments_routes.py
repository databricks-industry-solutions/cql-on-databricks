"""DOC-ENVIRONMENT-PICKER, CLIENT-ENVIRONMENT-RESOLVER, SEC-OBO — the environments the Designer's picker offers.

The list is read as the signed-in user and cached per user; a reload drops only the caller's entry. Content is held
per environment, as the App's principal (`KNOWLEDGE-LAKE-DIRECT`).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cql_databricks.resolvers.environment import EnvironmentCache
from cql_databricks_common.environments import process_environments, user_environments
from cql_databricks_designer.data_bindings import EnvironmentContents
from cql_databricks_designer.routes import environments as routes


def _tags(user: str) -> list[dict]:
    """The catalog tags ``user`` can see: the shared example environment and their own team's."""
    about = "Synthetic patients"  # the catalog comment, on every row the join returns for the catalog
    return [{"catalog": "example_cat", "tag": "cql_databricks_environment", "value": "example", "comment": about},
            {"catalog": "example_cat", "tag": "cql_databricks_environment_version", "value": "3", "comment": about},
            {"catalog": "example_cat", "tag": "cql_databricks_environment_default", "value": "true", "comment": about},
            {"catalog": f"{user}_cat", "tag": "cql_databricks_environment", "value": f"team_{user}", "comment": None},
            {"catalog": f"{user}_cat", "tag": "cql_databricks_environment_version", "value": "3", "comment": None}]


class _Users:
    """Who is signed in, and how many lookups each user's reads cost."""

    def __init__(self) -> None:
        self.current = "a@example.com"
        self.loads: list[str] = []
        self.opened = 0

    def runner(self):
        """The signed-in user's SQL runner; opening one is counted, so a held list is seen to open none."""
        self.opened += 1
        user = self.current

        def run(sql, params=None):
            if "catalog_tags" in sql:
                self.loads.append(user)
                return _tags(user.split("@")[0])
            return [{"tag_key": key} for key in ("cql_databricks_environment", "cql_databricks_environment_version",
                                                 "cql_databricks_environment_default")]
        return run


def _client(cache: EnvironmentCache, users: _Users, caller=None) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router(environments=cache, caller=caller or (lambda: users.current),
                                     runner=users.runner))
    return TestClient(app)


def test_each_user_sees_their_own_environments_cached_until_their_reload():
    """CLIENT-ENVIRONMENT-CACHE — the picker reads the process's cache, per user; a held list opens no session."""
    users = _Users()
    cache = EnvironmentCache()
    client = _client(cache, users)
    listed = client.get("/api/environments").json()
    assert listed == [{"name": "example", "catalog": "example_cat", "version": "3", "default": True,
                       "description": "Synthetic patients"},
                      {"name": "team_a", "catalog": "a_cat", "version": "3", "default": False}]
    opened = users.opened
    client.get("/api/environments")
    assert users.opened == opened
    users.current = "b@example.com"
    assert [e["name"] for e in client.get("/api/environments").json()] == ["example", "team_b"]
    assert users.loads == ["a@example.com", "b@example.com"]
    generation = cache.generation
    assert client.post("/api/environments/reload").json() == {"ok": True}
    assert cache.generation > generation
    client.get("/api/environments")
    assert users.loads == ["a@example.com", "b@example.com", "b@example.com"]


def test_the_picker_and_a_run_share_one_list():
    """CLIENT-ENVIRONMENT-CACHE — a run resolving through the process's cache finds what the picker already found."""
    users = _Users()
    cache = process_environments()
    # Nothing is forwarded in a test, so the run's key is the local one, "" (SEC-LOCAL-IDENTITY); so is the picker's.
    _client(cache, users, caller=lambda: "").get("/api/environments")
    assert user_environments(users.runner()).get("team_a").catalog == "a_cat"
    assert users.loads == ["a@example.com"]


#: CLIENT-ENVIRONMENT — the catalog tags the App's principal can see.
TAGS = [{"catalog": "example_cat", "tag": "cql_databricks_environment", "value": "example", "comment": None},
        {"catalog": "example_cat", "tag": "cql_databricks_environment_version", "value": "3", "comment": None},
        {"catalog": "example_cat", "tag": "cql_databricks_environment_default", "value": "true", "comment": None},
        {"catalog": "other_cat", "tag": "cql_databricks_environment", "value": "other", "comment": None},
        {"catalog": "other_cat", "tag": "cql_databricks_environment_version", "value": "3", "comment": None}]


def test_content_is_held_per_environment_and_none_is_the_default():
    """KNOWLEDGE-LAKE-DIRECT — one resolver per environment, by its standard schemas; a reload finds them again."""
    queries = []

    def run(sql, params=None):
        queries.append(sql)
        return TAGS

    contents = EnvironmentContents(lambda: run)
    default = contents.get(None)
    assert default.catalog == "example_cat"
    assert contents.get("example") is default
    assert contents.get("other").catalog == "other_cat"
    assert sum("catalog_tags" in q for q in queries) == 1
    before = contents.generation
    contents.reload()
    assert contents.generation != before
    assert contents.get(None) is not default and sum("catalog_tags" in q for q in queries) == 2
