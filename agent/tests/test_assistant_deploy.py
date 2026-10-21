"""APP-ASSISTANT / SEC-OBO — the drawer's model call runs as the READER, and the deploy says so.

The prompt this loop sends carries whatever the tools just read — measure results, define profiles,
the rows behind a rate — so it is a data-touching call, and under `SEC-OBO` those run as the
signed-in user. A SCOPE in the App declares the user's token is forwarded to the endpoint, and no
RESOURCE grants the App its own access — both halves are necessary because each fails silently on its
own. Conversely, each is correct only if the other is present: a scope with a leftover resource is
ambient authority nothing uses; a resource removed without the scope is a drawer that 403s for every
user in the deployed App while local dev (`SEC-LOCAL-IDENTITY`) keeps working. The endpoint name is
a stage-time placeholder like every other env var the deploy bakes in.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cql_databricks_common import config, local_env

# agent/tests/<this file> -> repo root is two levels up. Anchored on a marker (settings.gradle)
# rather than a fixed count to remain stable as the structure changes.
def _repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "settings.gradle").is_file():
            return candidate
    raise RuntimeError("repository root not found (no settings.gradle above this test)")


_REPO_ROOT = _repo_root()
_BUNDLE = yaml.safe_load((_REPO_ROOT / "databricks.yml").read_text())
_APP_YAML = yaml.safe_load((_REPO_ROOT / "designer" / "server" / "deploy" / "app.yaml").read_text())

#: The resource key this App does not declare. Kept as a name so the assertions read as one story.
_RESOURCE = "assistant_model"

#: The Apps user-authorization scope that carries the call now. NOT `model-serving`: both are accepted
#: by the platform, and this is the one that names the API family the loop actually calls.
_SCOPE = "serving.serving-endpoints"

#: Substituted by scripts/deploy/stage-app.sh from BUNDLE_VAR_assistant_endpoint.
_PLACEHOLDER = "__CQL_ASSISTANT_ENDPOINT__"


def _app_resources(app: str) -> dict[str, dict]:
    return {r["name"]: r for r in _BUNDLE["resources"]["apps"][app].get("resources", [])}


def _env(name: str) -> dict | None:
    return next((e for e in _APP_YAML["env"] if e["name"] == name), None)


def test_the_model_call_is_scoped_to_the_user_and_not_granted_to_the_app():
    app = _BUNDLE["resources"]["apps"]["cql_databricks_designer"]
    assert _SCOPE in app["user_api_scopes"], (
        f"the Designer App must declare `{_SCOPE}` (SEC-OBO): the forwarded user token is what the "
        "assistant's FM call authenticates with, and a scope the App does not declare is one the "
        "platform will not forward — every question 403s in the deployed App while local dev, where "
        "the loop is already the developer, keeps working")
    assert _RESOURCE not in _app_resources("cql_databricks_designer"), (
        "the App must NOT hold CAN_QUERY on the endpoint any more: the call runs as the reader, so a "
        "grant here is an authority nothing uses — the ambient authority SEC-OBO gives up on purpose")


def test_the_endpoint_name_is_baked_in_rather_than_bound_to_a_resource():
    entry = _env("CQL_ASSISTANT_ENDPOINT")
    assert entry is not None, "the deployed App has no endpoint to call"
    assert "valueFrom" not in entry, (
        "`valueFrom` binds to an App resource, and the assistant no longer has one — the binding "
        "would fail to resolve at deploy time")
    assert entry.get("value") == _PLACEHOLDER, (
        "CQL_ASSISTANT_ENDPOINT must carry the stage-time placeholder, so the name comes from the "
        "bundle variable rather than being written twice")
    stage = (_REPO_ROOT / "scripts" / "deploy" / "stage-app.sh").read_text()
    assert _PLACEHOLDER in stage and "BUNDLE_VAR_assistant_endpoint" in stage, (
        f"scripts/deploy/stage-app.sh must substitute {_PLACEHOLDER} from BUNDLE_VAR_assistant_endpoint, or "
        "the entry ships as a placeholder and is dropped (PIPE-DEPLOY-NO-PLACEHOLDER-ENV)")
    deploy = (_REPO_ROOT / "scripts" / "deploy" / "deploy.sh").read_text()
    assert "BUNDLE_VAR_assistant_endpoint" in deploy, (
        "scripts/deploy/deploy.sh must export BUNDLE_VAR_assistant_endpoint — stage-app.sh reads it from the "
        "environment and has no other source for the resolved bundle variable")


def test_the_endpoint_has_no_default_anywhere(monkeypatch):
    """APP-ASSISTANT — the workspace names the model; nothing falls back to one nobody picked.

    The bundle variable is empty by default and the deploy refuses it empty. Local runs read the same
    variable (`local_env.BUNDLE_OWNED`), and the code raises rather than guessing when it is unset.
    """
    assert _BUNDLE["variables"]["assistant_endpoint"]["default"] == ""
    assert "assistant_endpoint is empty" in (_REPO_ROOT / "scripts" / "deploy" / "deploy.sh").read_text()
    assert local_env.BUNDLE_OWNED["CQL_ASSISTANT_ENDPOINT"] == ("variables", "assistant_endpoint", "value")
    monkeypatch.delenv("CQL_ASSISTANT_ENDPOINT", raising=False)
    with pytest.raises(RuntimeError, match="CQL_ASSISTANT_ENDPOINT is not set"):
        config.assistant_endpoint()
    monkeypatch.setenv("CQL_ASSISTANT_ENDPOINT", "a-chat-endpoint")
    assert config.assistant_endpoint() == "a-chat-endpoint"


def test_every_app_payload_is_actually_uploaded():
    """SVC-MCP-TOOLS — a declared `source_code_path` reaches the workspace only if `sync.include` says so.

    **This one shipped and cost a whole deploy to find.** `cql_databricks_mcp` declared
    `./app/build/mcp`, `stage-app.sh` assembled it, and `sync.include` listed only the other two
    Apps' payloads — `build/` is gitignored, so nothing uploaded it. `bundle validate` passed and
    `bundle deploy` reported success, because the path either one checks is the LOCAL one; the App was
    created, started, and only then failed on the deploy of its own source:

        Invalid source code path: .../files/app/build/mcp. Path does not exist.

    It hid for as long as it did because `mcp-cql-databricks` had never been deployed — the two Apps that had
    were the two that were listed. So the check is written over EVERY app rather than over the one that
    broke: the next App added is the next one to inherit this, and the failure surfaces at the end of a
    seven-step deploy rather than at validation.
    """
    includes = _BUNDLE["sync"]["include"]
    for name, app in _BUNDLE["resources"]["apps"].items():
        path = app["source_code_path"].removeprefix("./")
        assert f"{path}/**" in includes, (
            f"app `{name}` deploys from {path}, which `sync.include` does not upload. `build/` is "
            "gitignored, so the workspace copy would not exist and the App fails on its own source "
            "deploy — after `bundle deploy` has already reported success.")


def test_the_mcp_app_reaches_no_model_endpoint_at_all():
    """The agent-facing App publishes TOOLS; it runs no model loop (`agent/README.md`).

    Its client brings its own model. Neither a grant nor a scope here — one would be an unused
    permission on a metered resource, the other an unused capability on a forwarded user token, and
    the reason it needs neither is the same reason the tool surface is worth having.
    """
    assert _RESOURCE not in _app_resources("cql_databricks_agent")
    assert _SCOPE not in _BUNDLE["resources"]["apps"]["cql_databricks_agent"]["user_api_scopes"]


def test_only_the_designer_reaches_the_model():
    """APP-PER-FEATURE — the drawer is the Designer's; the compiler App runs no model loop and takes no user scope."""
    assert "user_api_scopes" not in _BUNDLE["resources"]["apps"]["cql_databricks_compiler"]
    # EVALUATOR-RETIRE — no evaluator or point App.
    assert not {"cql_databricks_evaluator", "cql_databricks_point"} & set(_BUNDLE["resources"]["apps"])


def test_each_feature_app_grants_its_users_can_use():
    """APP-PER-FEATURE, APP-TO-APP-OBO — a user reached through another App is reached as themselves."""
    for key in ("cql_databricks_designer", "cql_databricks_agent"):
        perms = _BUNDLE["resources"]["apps"][key].get("permissions", [])
        assert {"level": "CAN_USE", "group_name": "${var.app_users_group}"} in perms, key
    assert "app_users_group" in (_REPO_ROOT / "scripts" / "deploy" / "deploy.sh").read_text()


def test_feature_apps_find_each_other_by_name():
    """APP-PER-FEATURE — `valueFrom` an `app` resource is that App's NAME; config.py reads its URL."""
    designer = {e["name"]: e for e in _APP_YAML["env"]}
    # EVALUATOR-REMOVE — no evaluator App to find: the Designer serves its routes itself.
    assert "CQL_EVALUATOR_APP" not in designer
    assert "evaluator" not in _app_resources("cql_databricks_designer")
    for key, path in (("cql_databricks_designer", "designer/server/deploy/app.yaml"),
                      ("cql_databricks_agent", "agent/deploy/app.yaml")):
        env = {e["name"]: e for e in yaml.safe_load((_REPO_ROOT / path).read_text())["env"]}
        assert env["CQL_COMPILER_APP"].get("valueFrom") == "compiler", path
        assert "compiler" in _app_resources(key), key
        assert "CQL_COMPILER_ENDPOINT" not in env, f"{path}: a literal compiler URL nothing fills in"
