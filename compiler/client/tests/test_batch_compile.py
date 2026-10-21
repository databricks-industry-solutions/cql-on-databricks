"""SVC-COMPILE-BATCH — the complete input travels once; older services fail before execution."""
from unittest.mock import patch

import pytest

from cql_databricks.compiler.libraries import LibrarySource
from cql_databricks.compiler.providers import CompileError, CompileRequest, RemoteProvider


def test_batch_request_carries_sources_parameters_and_metadata_in_one_call():
    """SVC-COMPILE-BATCH — root plus CQL closure, no translate/schema requests."""
    request = CompileRequest(cql="library Synthetic", deps=(LibrarySource(name="Helper", cql="library Helper"),),
                             measurement_period=("2026-01-01", "2026-12-31"),
                             data_source={"catalog": "synthetic", "schema": "source", "routines": ["retrieve_patient"]},
                             durable=True)
    body = request.to_json()
    assert body["measurementPeriod"] == ["2026-01-01", "2026-12-31"]
    assert body["dataSource"]["routines"] == ["retrieve_patient"]
    assert body["deps"][0]["cql"] == "library Helper"
    provider = RemoteProvider("http://localhost:8080")
    with patch.object(provider, "_post", return_value={"batchProtocol": "cql-batch-v1", "datasets": []}) as post:
        provider.compile_pipeline(request)
        post.assert_called_once_with("/compile-pipeline", request)


def test_old_compiler_cannot_silently_ignore_new_batch_inputs():
    """SVC-COMPILE-BATCH — no compatibility fallback with an unbound measurement period."""
    provider = RemoteProvider("http://localhost:8080")
    with patch.object(provider, "_post", return_value={"datasets": []}):
        with pytest.raises(CompileError, match="deploy the bundled compiler"):
            provider.compile_pipeline(CompileRequest(cql="library Synthetic", measurement_period=("2026", "2027")))
        assert provider.compile_pipeline(CompileRequest(cql="library Synthetic")).datasets == []
