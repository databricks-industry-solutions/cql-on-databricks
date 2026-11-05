"""CLIENT-ENVIRONMENT, DEPLOY-ENVIRONMENT-TAG-POLICIES — the client warns, never refuses, when the environment tags are
not governed in the caller's account, or when it cannot read whether they are."""
from __future__ import annotations

import warnings

import pytest

from cql_databricks.resolvers import environment
from cql_databricks.resolvers.environment import (DEFAULT_TAG, NAME_TAG, VERSION_TAG, EnvironmentResolver,
                                                   UngovernedEnvironmentWarning, check_governance)

TAGS = [{"catalog": "env", "tag": NAME_TAG, "value": "example", "comment": None},
        {"catalog": "env", "tag": VERSION_TAG, "value": "3", "comment": None},
        {"catalog": "env", "tag": DEFAULT_TAG, "value": "true", "comment": None}]


@pytest.fixture(autouse=True)
def unchecked(monkeypatch):
    monkeypatch.setattr(environment, "_governance_checked", False)


def runner(governed=None, refused=None):
    calls = []

    def run(sql, params=None):
        calls.append(sql)
        if "system.tags.governed_tags" in sql:
            if refused:
                raise PermissionError(refused)
            return [{"tag_key": key} for key in governed]
        return TAGS
    run.calls = calls
    return run


def test_governed_tags_raise_no_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert EnvironmentResolver(runner([NAME_TAG, VERSION_TAG, DEFAULT_TAG])).default().name == "example"


def test_an_ungoverned_tag_is_named_and_the_environment_still_resolves():
    with pytest.warns(UngovernedEnvironmentWarning, match=f"{VERSION_TAG}, {DEFAULT_TAG} are not governed"):
        assert EnvironmentResolver(runner([NAME_TAG])).default().catalog == "env"


def test_a_refused_read_is_a_warning_not_a_failure():
    with pytest.warns(UngovernedEnvironmentWarning, match="could not check.*PermissionError: no access"):
        assert EnvironmentResolver(runner(refused="no access")).default().name == "example"


def test_the_check_runs_once_per_process():
    run = runner([])
    with pytest.warns(UngovernedEnvironmentWarning):
        EnvironmentResolver(run).environments()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        EnvironmentResolver(run).environments()
        check_governance(run)
    assert sum("governed_tags" in sql for sql in run.calls) == 1
