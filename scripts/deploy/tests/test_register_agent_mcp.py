"""DEPLOY-AGENT-REGISTRATION — the agent App under Unity Gateway, per user, with no shared credential."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound, PermissionDenied

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("register_agent_mcp", _ROOT / "scripts/deploy/register_agent_mcp.py")
reg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reg)

HOST = "https://workspace.example"
APP_URL = "https://mcp-cql-databricks.example"
SCHEMA = "synthetic.agent"
CONNECTION = "mcp_cql_databricks_1234"
CLIENT = CONNECTION + "_u2m"
REDIRECT = HOST + "/login/oauth/http.html"


class Workspace:
    """The workspace APIs the registration calls, recording every write."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.connection = None
        self.service = False
        self.config = SimpleNamespace(host=HOST)
        self.apps = SimpleNamespace(get=lambda name: SimpleNamespace(url=APP_URL))
        self.connections = SimpleNamespace(get=self._get_connection, create=self._create_connection,
                                           update=self._update_connection)
        self.ai_gateway = SimpleNamespace(get_mcp_service=self._get_service, create_mcp_service=self._create_service)
        self.grants = SimpleNamespace(update=lambda **kw: self.calls.append(("grant", kw)))

    def get_workspace_id(self):
        return 1234

    def _get_connection(self, name):
        if self.connection is None:
            raise NotFound("no connection")
        return SimpleNamespace(options={k: v for k, v in self.connection.items() if k != "client_secret"})

    def _create_connection(self, *, name, connection_type, options, comment):
        self.calls.append(("connection-create", name))
        self.connection = dict(options)

    def _update_connection(self, *, name, options):
        self.calls.append(("connection-update", name))
        self.connection = dict(options)

    def _get_service(self, name):
        if not self.service:
            raise NotFound("no service")

    def _create_service(self, service, *, parent, mcp_service_id):
        self.calls.append(("service", parent, mcp_service_id, service.config.source_connection.name))
        self.service = True

    def writes(self):
        return [c for c in self.calls if c[0] != "grant"]


class Account:
    """The account OAuth client API, recording every write. Like the real one, a secret only comes back from create."""

    def __init__(self, admin=True):
        self.admin = admin
        self.calls: list[tuple] = []
        self.clients: dict[str, SimpleNamespace] = {}
        self.minted = 0
        self.custom_app_integration = SimpleNamespace(list=self._list, create=self._create, update=self._update,
                                                      delete=self._delete)

    def _list(self):
        if not self.admin:
            raise PermissionDenied("This API is disabled for users without account admin status")
        return iter(list(self.clients.values()))

    def _create(self, *, name, confidential, redirect_urls, scopes):
        self.minted += 1
        self.calls.append(("client-create", name))
        client = SimpleNamespace(integration_id=f"int-{self.minted}", client_id=f"client-{self.minted}", name=name,
                                 confidential=confidential, redirect_urls=redirect_urls, scopes=scopes)
        self.clients[client.integration_id] = client
        return SimpleNamespace(integration_id=client.integration_id, client_id=client.client_id,
                               client_secret=f"secret-{self.minted}")

    def _update(self, integration_id, *, redirect_urls, scopes):
        self.calls.append(("client-update", integration_id))
        self.clients[integration_id].redirect_urls, self.clients[integration_id].scopes = redirect_urls, scopes

    def _delete(self, integration_id):
        self.calls.append(("client-delete", integration_id))
        del self.clients[integration_id]


def register(w, a):
    return reg.register(w, a, app_name="mcp-cql-databricks", schema=SCHEMA, users_group="clinicians")


def test_a_first_deploy_creates_the_client_and_a_per_user_connection_and_a_service():
    w, a = Workspace(), Account()
    assert register(w, a) == CONNECTION
    (client,) = a.clients.values()
    assert client.name == CLIENT and client.confidential and client.redirect_urls == [REDIRECT]
    assert set(client.scopes) == {"all-apis", "offline_access"}
    assert w.connection["client_id"] == client.client_id and w.connection["client_secret"] == "secret-1"
    assert w.connection["host"] == APP_URL and w.connection["mcp_resource"] == APP_URL + "/mcp"
    assert w.connection["authorization_endpoint"] == HOST + "/oidc/v1/authorize"
    assert set(w.connection["oauth_scope"].split()) == {"all-apis", "offline_access"}
    assert ("service", f"schemas/{SCHEMA}", "cql_databricks", f"connections/{CONNECTION}") in w.calls


def test_the_users_group_gets_execute_and_never_use_connection():
    w = Workspace()
    register(w, Account())
    grants = [(c[1]["securable_type"], c[1]["full_name"], [p.value for p in c[1]["changes"][0].add])
              for c in w.calls if c[0] == "grant"]
    assert ("mcp_service", f"{SCHEMA}.cql_databricks", ["EXECUTE"]) in grants
    assert not any("USE_CONNECTION" in privileges for _, _, privileges in grants)
    assert all(c[1]["changes"][0].principal == "clinicians" for c in w.calls if c[0] == "grant")


def test_a_redeploy_that_finds_everything_in_place_changes_nothing():
    w, a = Workspace(), Account()
    register(w, a)
    workspace_before, account_before = len(w.writes()), len(a.calls)
    register(w, a)
    assert w.writes()[workspace_before:] == [] and a.calls[account_before:] == []


def test_a_redeploy_without_an_account_profile_keeps_the_connection_and_calls_no_account_api():
    """Only a new or repointed connection needs an account admin: a code-only redeploy needs none."""
    w = Workspace()
    register(w, Account())
    before = len(w.writes())
    assert register(w, None) == CONNECTION
    assert w.writes()[before:] == []


def test_no_account_profile_and_no_connection_leaves_genie_one_unconnected_and_writes_nothing():
    w = Workspace()
    assert register(w, None) is None
    assert w.calls == []


def test_no_account_profile_and_a_moved_app_leaves_the_connection_alone():
    w = Workspace()
    register(w, Account())
    kept = dict(w.connection)
    w.apps = SimpleNamespace(get=lambda name: SimpleNamespace(url="https://moved.example"))
    assert register(w, None) is None
    assert w.connection == kept


def test_a_connection_whose_client_was_deleted_gets_a_new_one():
    w, a = Workspace(), Account()
    register(w, a)
    a.clients.clear()
    register(w, a)
    assert ("connection-update", CONNECTION) in w.calls and w.connection["client_id"] == "client-2"


def test_a_deployer_who_is_not_an_account_admin_is_refused_before_any_write():
    w, a = Workspace(), Account(admin=False)
    with pytest.raises(reg.RegistrationError, match="account admin"):
        register(w, a)
    assert w.writes() == [] and a.calls == []


def test_a_moved_app_repoints_the_connection_through_a_new_client():
    w, a = Workspace(), Account()
    register(w, a)
    w.apps = SimpleNamespace(get=lambda name: SimpleNamespace(url="https://moved.example"))
    register(w, a)
    assert ("connection-update", CONNECTION) in w.calls and w.connection["host"] == "https://moved.example"
    assert ("client-delete", "int-1") in a.calls and list(a.clients) == ["int-2"]
    assert w.connection["client_id"] == "client-2" and w.connection["client_secret"] == "secret-2"


def test_a_connection_left_on_another_client_is_moved_to_a_new_one():
    """The connection exists but was made some other way (an M2M probe, say): it is repointed, never kept."""
    w, a = Workspace(), Account()
    w.connection = {"host": APP_URL, "client_id": "someone-elses", "client_secret": "x"}
    register(w, a)
    assert ("connection-update", CONNECTION) in w.calls and w.connection["client_id"] == "client-1"


def test_a_client_whose_redirect_was_changed_is_restored_and_kept():
    w, a = Workspace(), Account()
    register(w, a)
    a.clients["int-1"].redirect_urls = ["https://elsewhere.example/cb"]
    register(w, a)
    assert ("client-update", "int-1") in a.calls and a.clients["int-1"].redirect_urls == [REDIRECT]
    assert w.connection["client_id"] == "client-1"


def test_two_clients_with_the_agents_name_are_refused():
    w, a = Workspace(), Account()
    a._create(name=CLIENT, confidential=True, redirect_urls=[REDIRECT], scopes=["all-apis"])
    a._create(name=CLIENT, confidential=True, redirect_urls=[REDIRECT], scopes=["all-apis"])
    with pytest.raises(reg.RegistrationError, match="delete all but one"):
        register(w, a)
    assert w.writes() == []


@pytest.mark.parametrize("schema, group", [("noschema", "clinicians"), (SCHEMA, "")])
def test_bad_configuration_is_refused_before_any_write(schema, group):
    w, a = Workspace(), Account()
    with pytest.raises(reg.RegistrationError):
        reg.register(w, a, app_name="mcp-cql-databricks", schema=schema, users_group=group)
    assert w.calls == [] and a.calls == []
