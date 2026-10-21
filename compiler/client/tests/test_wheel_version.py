"""SVC-WHEEL-VERSION — the deploy stamps the client wheel's version with a hash of its contents.

An App keeps its venv across deployments and pip skips a wheel whose version is already installed, so a
client that changed under the same version never reached the App (the agent App crashed on an import the
installed 0.1.1 lacked).
"""
import importlib.util
import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).parents[3]
spec = importlib.util.spec_from_file_location(
    'client_wheel_version', _ROOT / 'scripts/lib/client_wheel_version.py')
wheel_version_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wheel_version_module)

#: PEP 440 public version + local label, in the form pip writes into the wheel filename unchanged.
_STAMPED = re.compile(r'^\d+(\.\d+)*\+src[0-9a-f]{12}$')


def _client(tmp_path):
    client = tmp_path / 'client'
    (client / 'src/cql_databricks/compiler').mkdir(parents=True)
    (client / 'pyproject.toml').write_text('[project]\nname = "cql-databricks"\nversion = "0.1.1"\n')
    (client / 'README.md').write_text('synthetic\n')
    (client / 'src/cql_databricks/compiler/config.py').write_text('X = 1\n')
    return client


def test_version_is_the_release_plus_a_content_label(tmp_path):
    """SVC-WHEEL-VERSION — `src` keeps the label from being an all-digit one PEP 440 would renormalize."""
    version = wheel_version_module.wheel_version(_client(tmp_path))
    assert version.startswith('0.1.1+src')
    assert _STAMPED.match(version)


def test_changed_source_changes_the_version(tmp_path):
    """SVC-WHEEL-VERSION — the failure: new code under an unchanged version."""
    client = _client(tmp_path)
    before = wheel_version_module.wheel_version(client)
    (client / 'src/cql_databricks/compiler/config.py').write_text('X = 1\ndef resolve_catalog_scopes(): ...\n')
    assert wheel_version_module.wheel_version(client) != before


def test_added_file_changes_the_version(tmp_path):
    """SVC-WHEEL-VERSION — an uncommitted new module counts too."""
    client = _client(tmp_path)
    before = wheel_version_module.wheel_version(client)
    (client / 'src/cql_databricks/compiler/new_module.py').write_text('Y = 2\n')
    assert wheel_version_module.wheel_version(client) != before


def test_build_and_cache_output_do_not_change_the_version(tmp_path):
    """SVC-WHEEL-VERSION — running the tests or a local build must not force a reinstall."""
    client = _client(tmp_path)
    before = wheel_version_module.wheel_version(client)
    (client / 'src/cql_databricks/compiler/__pycache__').mkdir()
    (client / 'src/cql_databricks/compiler/__pycache__/config.cpython-312.pyc').write_bytes(b'\0')
    (client / 'src/cql_databricks.egg-info').mkdir()
    (client / 'src/cql_databricks.egg-info/PKG-INFO').write_text('synthetic\n')
    (client / 'build/lib').mkdir(parents=True)
    (client / 'build/lib/stale.py').write_text('Z = 3\n')
    assert wheel_version_module.wheel_version(client) == before


def test_the_real_client_stamps_and_the_shell_helper_agrees():
    """SVC-WHEEL-VERSION — staging and the wheel build both read the shell helper."""
    client = _ROOT / 'compiler/client'
    version = wheel_version_module.wheel_version(client)
    assert _STAMPED.match(version)
    shell = subprocess.run(
        ['bash', '-c', f'. "{_ROOT}/scripts/lib/client-wheel-version.sh"; unset CQL_CLIENT_WHEEL_VERSION; '
                       'client_wheel_version'], capture_output=True, text=True, check=True)
    assert shell.stdout.strip() == version


def test_staging_and_the_build_use_the_stamped_version():
    """SVC-WHEEL-VERSION — neither script may fall back to the plain pyproject release."""
    for script in ('stage-app.sh', 'deploy-runtime-artifacts.sh'):
        text = (_ROOT / 'scripts' / 'deploy' / script).read_text()
        assert 'client-wheel-version.sh' in text and 'client_ver="$(client_wheel_version)"' in text, script
    assert 'export CQL_CLIENT_WHEEL_VERSION' in (_ROOT / 'scripts/deploy/deploy.sh').read_text()
