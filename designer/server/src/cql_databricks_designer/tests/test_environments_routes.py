"""DOC-ENVIRONMENT-PICKER, CLIENT-ENVIRONMENT-RESOLVER, SEC-OBO — the environments the Designer's picker offers.

The list is read as the signed-in user and cached per user; a reload drops only the caller's entry. Content is held
per environment, as the App's principal (`KNOWLEDGE-LAKE-DIRECT`).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cql_databricks.resolvers.environment import Environment
from cql_databricks_designer.data_bindings import EnvironmentContents
from cql_databricks_designer.routes import environments as routes


def _environments(user: str) -> list[Environment]:
    return [Environment(name="example", catalog="example_cat", version="1", default=True,
                        description="Synthetic patients"),
            Environment(name=f"team_{user}", catalog=f"{user}_cat", version="1")]


class _Users:
    """Who is signed in, and how many lookups each user's reads cost."""

    def __init__(self) -> None:
        self.current = "a@example.com"
        self.loads: list[str] = []

    def load(self) -> list[Environment]:
        self.loads.append(self.current)
        return _environments(self.current.split("@")[0])


def _client(cache: routes.EnvironmentCache) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router(environments=cache))
    return TestClient(app)


def test_each_user_sees_their_own_environments_cached_until_their_reload():
    users = _Users()
    cache = routes.EnvironmentCache(users.load, lambda: users.current)
    client = _client(cache)
    listed = client.get("/api/environments").json()
    assert listed == [{"name": "example", "catalog": "example_cat", "version": "1", "default": True,
                       "description": "Synthetic patients"},
                      {"name": "team_a", "catalog": "a_cat", "version": "1", "default": False}]
    client.get("/api/environments")
    users.current = "b@example.com"
    assert [e["name"] for e in client.get("/api/environments").json()] == ["example", "team_b"]
    assert users.loads == ["a@example.com", "b@example.com"]
    generation = cache.generation
    assert client.post("/api/environments/reload").json() == {"ok": True}
    assert cache.generation > generation
    client.get("/api/environments")
    assert users.loads == ["a@example.com", "b@example.com", "b@example.com"]


#: CLIENT-ENVIRONMENT — the catalog tags the App's principal can see.
TAGS = [{"catalog": "example_cat", "tag": "cql_databricks_environment", "value": "example", "comment": None},
        {"catalog": "example_cat", "tag": "cql_databricks_environment_version", "value": "1", "comment": None},
        {"catalog": "example_cat", "tag": "cql_databricks_environment_default", "value": "true", "comment": None},
        {"catalog": "other_cat", "tag": "cql_databricks_environment", "value": "other", "comment": None},
        {"catalog": "other_cat", "tag": "cql_databricks_environment_version", "value": "1", "comment": None}]


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
