"""SVC-EXAMPLE-NOTEBOOKS, DEPLOY-BASE-ENVIRONMENT — every example notebook gets the client from the base environment.

Each example is a `.ipynb` whose serverless environment names the base environment the deploy publishes,
`workspace-base-environments/cql-on-databricks`, and nothing else: no install cell, no restart, no Volume path and no
placeholder, so the repository copy is the published copy. Only the Jupyter format stores a notebook's environment,
so the index page is the one source notebook, and it holds no code. Every example's first code cell is the same two
lines: `%reload_ext cql_databricks.compiler` and `from cql_databricks.compiler import cql`.
Offline: reads the repository copies only.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

EXAMPLES_DIR = pathlib.Path(__file__).resolve().parents[1]
INDEX = "00_start_here.py"
BASE_ENVIRONMENT = "workspace-base-environments/cql-on-databricks"
NOTEBOOKS = sorted(p.name for p in EXAMPLES_DIR.glob("*.ipynb"))
#: The one start every example has: the `%%cql` magic, and `cql()` (SVC-NOTEBOOK-CQL-MAGIC).
START = "%reload_ext cql_databricks.compiler\nfrom cql_databricks.compiler import cql"


def test_every_example_is_a_jupyter_notebook_and_the_index_is_the_only_source_notebook():
    assert [p.name for p in EXAMPLES_DIR.glob("*.py")] == [INDEX]
    index = (EXAMPLES_DIR / INDEX).read_text()
    assert "# COMMAND ----------" not in index, f"{INDEX} is one documentation cell (SVC-EXAMPLE-INDEX)"


def test_there_are_examples():
    assert len(NOTEBOOKS) >= 8


@pytest.mark.parametrize("nb", NOTEBOOKS)
def test_the_environment_is_the_base_environment_and_nothing_else(nb):
    notebook = json.loads((EXAMPLES_DIR / nb).read_text())
    env = notebook["metadata"]["application/vnd.databricks.v1+notebook"]["environmentMetadata"]
    assert env == {"base_environment": BASE_ENVIRONMENT}, f"{nb}: the environment must be the base environment alone"


@pytest.mark.parametrize("nb", NOTEBOOKS)
def test_the_first_code_cell_loads_the_magic_and_imports_cql(nb):
    cells = json.loads((EXAMPLES_DIR / nb).read_text())["cells"]
    first = next("".join(c["source"]) for c in cells if c["cell_type"] == "code")
    assert first == START, f"{nb}: the first code cell must be exactly {START!r}, got {first!r}"


@pytest.mark.parametrize("nb", NOTEBOOKS)
def test_no_cell_installs_restarts_or_names_a_deployment(nb):
    text = (EXAMPLES_DIR / nb).read_text()
    code = "".join("".join(c["source"]) for c in json.loads(text)["cells"] if c["cell_type"] == "code")
    assert "%pip install" not in code, f"{nb}: the client comes from the base environment, not a %pip cell"
    assert "restartPython" not in code, f"{nb}: nothing is installed, so nothing restarts"
    assert not re.search(r"/Volumes/\w", code), f"{nb}: a literal Volume path names one deployment's catalog"
    assert "__CQL_" not in text, f"{nb}: the repository copy is the published copy; no placeholder is filled in"
