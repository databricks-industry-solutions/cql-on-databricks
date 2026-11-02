"""SVC-PACKAGING — the Compiler Service launcher reads its oversized artifacts from the runtime volume when one is
configured, and fails the boot when that read fails. The public URLs are used only when no volume is configured.
The public host is a real local HTTP server; the Databricks SDK, which is external, is a fake whose download
raises the SDK's own `PermissionDenied`."""
from __future__ import annotations

import importlib.util
import io
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import databricks.sdk
import pytest
from databricks.sdk.errors import PermissionDenied

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("bootstrap", _ROOT / "compiler/server/deploy/bootstrap.py")
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)

RELPATH = "maven/icu4j-77.1.jar"
PAYLOAD = b"jar bytes from the public host"
VOLUME = "/Volumes/synthetic_catalog/runtime/artifacts"


class _PublicHost:
    """A real local HTTP server serving one artifact; it records the path of every request it gets."""

    def __init__(self, root: Path):
        self.requests: list[str] = []
        host = self

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(root), **kwargs)

            def log_message(self, *args):
                host.requests.append(self.path)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/{RELPATH}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def public_host(tmp_path):
    (tmp_path / "maven").mkdir()
    (tmp_path / RELPATH).write_bytes(PAYLOAD)
    host = _PublicHost(tmp_path)
    yield host
    host.close()


class _Files:
    def __init__(self, behaviour):
        self._behaviour = behaviour

    def download(self, path):
        return self._behaviour(path)


class _FakeWorkspace:
    """The SDK surface `bootstrap._fetch` uses: `WorkspaceClient().files.download(path).contents`."""
    constructed = 0
    behaviour = None

    def __init__(self):
        type(self).constructed += 1
        self.files = _Files(type(self).behaviour)


@pytest.fixture
def sdk(monkeypatch):
    _FakeWorkspace.constructed = 0
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", _FakeWorkspace)
    return _FakeWorkspace


def test_a_failed_volume_read_stops_the_boot_and_names_the_path(monkeypatch, tmp_path, public_host, sdk):
    """SVC-PACKAGING — with a volume configured, a failed read raises naming the volume path and the cause. The
    public host is reachable and holds the same artifact, yet is never tried."""
    def denied(path):
        raise PermissionDenied("no READ_VOLUME")
    sdk.behaviour = denied
    monkeypatch.setattr(bootstrap, "ARTIFACT_VOLUME", VOLUME)
    dest = tmp_path / "icu4j.jar"

    with pytest.raises(bootstrap.ArtifactReadError) as caught:
        bootstrap._fetch(str(dest), RELPATH, public_host.url)

    message = str(caught.value)
    assert f"{VOLUME}/{RELPATH}" in message
    assert "no READ_VOLUME" in message
    assert "READ_VOLUME" in message and "uc_securable" in message
    assert public_host.requests == []
    assert not dest.exists()


def test_a_configured_volume_is_the_only_source(monkeypatch, tmp_path, public_host, sdk):
    """SVC-PACKAGING — a volume read that succeeds writes the artifact and never touches the public host."""
    sdk.behaviour = lambda path: type("Resp", (), {"contents": io.BytesIO(b"volume bytes")})()
    monkeypatch.setattr(bootstrap, "ARTIFACT_VOLUME", VOLUME)
    dest = tmp_path / "icu4j.jar"

    bootstrap._fetch(str(dest), RELPATH, public_host.url)

    assert dest.read_bytes() == b"volume bytes"
    assert public_host.requests == []


def test_no_volume_configured_reads_the_public_url(monkeypatch, tmp_path, public_host, sdk):
    """SVC-PACKAGING — with no volume configured (a local run), the artifact comes from its public URL and the
    SDK is never constructed."""
    monkeypatch.setattr(bootstrap, "ARTIFACT_VOLUME", "")
    dest = tmp_path / "icu4j.jar"

    bootstrap._fetch(str(dest), RELPATH, public_host.url)

    assert dest.read_bytes() == PAYLOAD
    assert len(public_host.requests) == 1
    assert sdk.constructed == 0
