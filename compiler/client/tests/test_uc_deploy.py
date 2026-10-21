"""SVC-COMPILER-UC — deploy only our immutable artifacts and marked function, never grants/Apps."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

from databricks.sdk.errors import NotFound
import pytest

spec = importlib.util.spec_from_file_location(
    'register_uc', Path(__file__).parents[2] / 'server/deploy/register_uc.py')
deploy_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_module)


def test_ddl_quotes_identifiers_and_volume_paths():
    """SVC-COMPILER-UC — deployment values are data, never SQL syntax."""
    ddl = deploy_module.registration_statement(('synthetic', 'quote`schema', 'compiler'),
        ["/Volumes/synthetic/schema/artifacts/quote'jar.jar"])
    assert '`synthetic`.`quote``schema`.`compiler`' in ddl
    assert "quote''jar.jar" in ddl
    assert "environment_version = '4'" in ddl
    assert 'UcCompiler.invoke' in ddl


def test_refuses_unrelated_function_without_writing():
    """SVC-COMPILER-UC — a matching name is not permission to adopt another function."""
    client = SimpleNamespace(functions=SimpleNamespace(get=lambda name: SimpleNamespace(comment='unrelated')))
    with pytest.raises(ValueError, match='refusing to replace'):
        deploy_module.deploy(client, lambda *a: pytest.fail('SQL'), catalog='c', schema='s',
                             volume='v', libraries=Path('/synthetic/not-used'))


@pytest.mark.parametrize('corrupt', [False, True])
def test_upload_is_immutable_verified_and_requires_no_other_apis(tmp_path, corrupt):
    """SVC-COMPILER-UC — no credential/grant changes or replacement of existing artifacts."""
    jar = tmp_path / 'cql-databricks-compiler-server-synthetic.jar'
    jar.write_bytes(b'synthetic compiler artifact')
    stored, sql = {}, []
    def absent(*args):
        raise NotFound('synthetic missing')
    def upload(destination, source, **kwargs):
        assert not kwargs['overwrite']
        stored[destination] = b'corrupt' if corrupt else Path(source).read_bytes()
    client = SimpleNamespace(functions=SimpleNamespace(get=absent), files=SimpleNamespace(
        get_metadata=absent, create_directory=lambda *a: None, upload_from=upload,
        download=lambda name: SimpleNamespace(contents=io.BytesIO(stored[name]))))
    def run(statement, params=None):
        sql.append(statement)
        return [{'response': json.dumps({'compilerVersion': 'uc-sha256-' + 'a' * 64})}]
    if corrupt:
        with pytest.raises(ValueError, match='checksum mismatch'):
            deploy_module.deploy(client, run, catalog='synthetic', schema='runtime', volume='artifacts', libraries=tmp_path)
        assert not sql
    else:
        result = deploy_module.deploy(client, run, catalog='synthetic', schema='runtime', volume='artifacts', libraries=tmp_path)
        assert result['function'] == 'synthetic.runtime.cql_compile'
        assert result['jars'] == 1
        assert sql[0].startswith('CREATE OR REPLACE FUNCTION')
        assert sql[1].startswith('SELECT ')
