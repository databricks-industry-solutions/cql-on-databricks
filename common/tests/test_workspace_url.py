"""APP-TOUR — the workspace URL the SPA links workspace objects against.

Its consumer is the Product Tour's link to the published example notebooks
(`SVC-EXAMPLE-NOTEBOOKS`). The App is served from a different origin than the workspace, so a wrong
answer here is a link into someone else's workspace — which is why "no host" raises rather than
returning something plausible.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cql_databricks_common.workspace import workspace_url


def _client(host: str | None) -> MagicMock:
    client = MagicMock()
    client.config.host = host
    return client


def test_host_comes_from_the_resolved_sdk_config():
    with patch("databricks.sdk.WorkspaceClient", return_value=_client("https://acme.cloud.databricks.com")):
        assert workspace_url() == "https://acme.cloud.databricks.com"


def test_trailing_slash_is_dropped_so_the_composed_path_has_one_separator():
    with patch("databricks.sdk.WorkspaceClient", return_value=_client("https://acme.cloud.databricks.com/")):
        assert workspace_url() == "https://acme.cloud.databricks.com"


@pytest.mark.parametrize("host", [None, ""])
def test_no_host_raises_rather_than_returning_a_guess(host):
    with patch("databricks.sdk.WorkspaceClient", return_value=_client(host)):
        with pytest.raises(RuntimeError, match="workspace host"):
            workspace_url()
