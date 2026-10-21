"""SVC-COMPILER-UC — synthetic protocol/identity transport tests; no warehouse or HTTP."""
import base64
import gzip
import json
import random

import pytest

from cql_databricks.compiler.providers import CompileError, CompileRequest
from cql_databricks.compiler.uc import UcFunctionProvider


def test_request_is_lossless_compressed_and_bound_not_interpolated():
    """SVC-COMPILER-UC — large include closures fit without changing compiler inputs."""
    calls = []
    request = CompileRequest(cql="library Synthetic\ndefine X: 'é'", deps=(),
                             overrides={'synthetic': 'value' * 300_000})
    def run(sql, params):
        calls.append((sql, params))
        assert json.loads(gzip.decompress(base64.b64decode(params['request']))) == request.to_json()
        assert params['operation'] == 'compile-sql'
        assert len(params['request']) < 1_000_000
        return [{'response': json.dumps({'target': 'X', 'defines': ['X'], 'statements': []})}]
    result = UcFunctionProvider(run, ('synthetic', 'quoted`schema', 'compiler')).compile_sql(request)
    assert result.target == 'X'
    assert calls[0][0] == 'SELECT `synthetic`.`quoted``schema`.`compiler`(:operation, :request) AS response'
    assert 'library Synthetic' not in calls[0][0]


def test_pipeline_uses_shared_decoder_without_changing_durable_claims():
    """SVC-COMPILER-UC, PIPE-DURABLE-IDENTITY — metadata passes through the existing decoder."""
    body = {'compilerVersion': 'synthetic-build', 'datasets': [{
        'name': 'x', 'target': 'X', 'query': 'SELECT 1', 'library': 'Synthetic', 'define': 'X',
        'durable': {'format': 'cql-result-identity-v1', 'fingerprint': 'a' * 64,
                    'reusable': True, 'parameters': {'period': 'synthetic'}}}]}
    provider = UcFunctionProvider(lambda *args: [{'response': json.dumps(body)}], ('c', 's', 'f'))
    plan = provider.compile_pipeline(CompileRequest(cql='synthetic', durable=True))
    assert plan.compiler_version == 'synthetic-build'
    assert plan.datasets[0].durable.fingerprint == 'a' * 64
    assert plan.datasets[0].durable.parameters == {'period': 'synthetic'}


@pytest.mark.parametrize('function', [None, 'c.s.f', (), ('c', 'f'), ('c', '', 'f'), ('c', 's', 1)])
def test_invalid_function_fails_before_sql(function):
    """SVC-COMPILER-UC — explicit configured target only."""
    with pytest.raises(ValueError, match='UC compiler requires'):
        UcFunctionProvider(lambda *args: pytest.fail('SQL'), function)


@pytest.mark.parametrize('rows', [[], [{}, {}], [{}], [{'response': None}],
                                 [{'response': 'invalid'}], [{'response': '[]'}]])
def test_malformed_responses_fail_loudly(rows):
    """SVC-COMPILER-UC — transport failures are not empty successful compilations."""
    provider = UcFunctionProvider(lambda *args: rows, ('c', 's', 'f'))
    with pytest.raises(CompileError):
        provider.capabilities()


def test_permission_failure_never_tries_http(monkeypatch):
    """SVC-COMPILER-UC — no alternate identity or fallback execution path."""
    monkeypatch.setattr('requests.post', lambda *a, **kw: pytest.fail('HTTP fallback'))
    def denied(*args):
        raise PermissionError('synthetic denied EXECUTE')
    with pytest.raises(PermissionError, match='denied EXECUTE'):
        UcFunctionProvider(denied, ('c', 's', 'f')).translate('synthetic')


def test_oversized_compressed_request_fails_before_sql():
    """SVC-COMPILER-UC — bounded parameters fail locally, not after a warehouse request."""
    text = random.Random(0).randbytes(1_100_000).hex()
    with pytest.raises(CompileError, match='parameter limit'):
        UcFunctionProvider(lambda *args: pytest.fail('SQL'), ('c', 's', 'f')).translate(text)
