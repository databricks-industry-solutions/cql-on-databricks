"""SVC-NOTEBOOK-CQL-MAGIC — ``%load_ext cql_databricks.compiler`` imports the magic module.

IPython is a notebook-only surface and not a dependency, so nothing imported ``magic.py`` locally, and an
import of a name ``client.py`` no longer defines failed every example notebook at ``%load_ext``. A stub
IPython lets the module import here.
"""
import importlib
import sys
import types


def _stub_ipython(monkeypatch):
    def passthrough(*_args, **_kwargs):
        return lambda fn: fn

    modules = {
        "IPython": types.ModuleType("IPython"),
        "IPython.core": types.ModuleType("IPython.core"),
        "IPython.core.error": types.ModuleType("IPython.core.error"),
        "IPython.core.magic": types.ModuleType("IPython.core.magic"),
        "IPython.core.magic_arguments": types.ModuleType("IPython.core.magic_arguments"),
    }
    modules["IPython.core.error"].UsageError = type("UsageError", (Exception,), {})
    modules["IPython.core.magic"].Magics = object
    modules["IPython.core.magic"].cell_magic = lambda fn: fn
    modules["IPython.core.magic"].magics_class = lambda cls: cls
    modules["IPython.core.magic_arguments"].argument = passthrough
    modules["IPython.core.magic_arguments"].magic_arguments = passthrough
    modules["IPython.core.magic_arguments"].parse_argstring = lambda *_a: None
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "cql_databricks.compiler.magic", raising=False)


def test_magic_runs_the_zero_config_cql(monkeypatch):
    """SVC-NOTEBOOK-CQL-MAGIC — the magic is the same `SVC-ZERO-CONFIG-NOTEBOOK` path as `cql()`."""
    _stub_ipython(monkeypatch)
    magic = importlib.import_module("cql_databricks.compiler.magic")
    from cql_databricks.compiler import cql

    assert magic._run_cql is cql
