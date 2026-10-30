"""BUILD-TAXONOMY — the agent's service is host-agnostic: it imports the client and nothing that hosts it.

A service is logic that's host-agnostic (`docs/structure.md`). The server publishes these tools over MCP and runs
them in an App; the moment a tool imports either, it has a shape only that host can call, and a second caller —
a Job, a notebook, another transport — would have to bring the App along. Nothing else stops that import, so
this does, as ``./gradlew checkNeutrality`` does for the compiler's wall.
"""
from __future__ import annotations

import ast
from pathlib import Path

import cql_databricks.agent

_SERVICE = Path(__file__).resolve().parents[1] / "src" / "cql_databricks" / "agent" / "service"

#: What hosts the tools: the MCP SDK, the web stack, the shared App library, and the agent's own server.
_HOSTS = ("fastmcp", "mcp", "fastapi", "starlette", "uvicorn", "cql_databricks_common", "cql_databricks.agent.server")


def _imports(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_no_service_module_imports_what_hosts_it():
    found = [f"{path.relative_to(_SERVICE)}: {name}" for path in sorted(_SERVICE.rglob("*.py"))
             for name in _imports(path) if any(name == h or name.startswith(h + ".") for h in _HOSTS)]
    assert not found, f"the agent's service imports what hosts it: {found}. Inject it from the server instead."


def test_the_scan_sees_the_service_modules():
    # A wrong path would make the scan above pass over nothing.
    assert {"data.py", "definitions.py", "saved.py", "experiments.py"} <= {p.name for p in _SERVICE.glob("*.py")}


def test_cql_databricks_agent_stays_a_namespace():
    """BUILD-CLIENT-NAMESPACE — three distributions ship packages under it; an ``__init__.py`` would hide two."""
    assert getattr(cql_databricks.agent, "__file__", None) is None
