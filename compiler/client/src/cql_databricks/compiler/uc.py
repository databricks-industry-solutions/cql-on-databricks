"""SVC-COMPILER-UC — compile through the caller's SQL runner, without App authentication."""
from __future__ import annotations

import base64
import gzip
import json

from cql_databricks.data_bindings.runner import SqlRunner

from .providers import CompileError, CompileRequest, _JsonProvider


class UcFunctionProvider(_JsonProvider):
    """SVC-COMPILER-UC — one explicit UC compiler function; errors never fall back to HTTP.

    ``function`` is a (catalog, schema, function) tuple. The deployment owns its versioned JARs;
    the runner supplies caller identity and compute. Compiles once per request, not per data row.
    """

    def __init__(self, run: SqlRunner, function: tuple[str, str, str]):
        if (not callable(run) or not isinstance(function, tuple) or len(function) != 3
                or any(not isinstance(part, str) or not part.strip() or '\x00' in part for part in function)):
            raise ValueError("UC compiler requires a SQL runner and (catalog, schema, function)")
        self._run = run
        self._base = '.'.join('`' + part.replace('`', '``') + '`' for part in function)

    def _post(self, path: str, request: CompileRequest | dict) -> dict:
        body = request.to_json() if isinstance(request, CompileRequest) else request
        raw = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(raw) > 64 * 1024 * 1024:
            raise CompileError("UC compiler request exceeds 64 MiB decoded limit")
        payload = base64.b64encode(gzip.compress(raw, mtime=0)).decode('ascii')
        # SQL Statement Execution limits the combined parameters to 1 MiB. Leave room for operation.
        if len(payload) > 1_000_000:
            raise CompileError("UC compiler compressed request exceeds SQL parameter limit")
        rows = self._run(f'SELECT {self._base}(:operation, :request) AS response',
                         {'operation': path.removeprefix('/'), 'request': payload})
        if len(rows) != 1 or not isinstance(rows[0].get('response'), str):
            raise CompileError("UC compiler must return one JSON response")
        try:
            result = json.loads(rows[0]['response'])
        except ValueError as error:
            raise CompileError("UC compiler returned invalid JSON") from error
        if not isinstance(result, dict):
            raise CompileError("UC compiler must return a JSON object")
        return result

    def capabilities(self) -> dict:
        """SVC-COMPILER-UC — the same artifact identity as compile/describe, across sandboxes."""
        return self._post('/capabilities', {})
