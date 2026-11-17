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
NOTEBOOKS = sorted(p.relative_to(EXAMPLES_DIR).as_posix() for p in EXAMPLES_DIR.glob("*.ipynb"))
#: The one start every example has: the `%%cql` magic, and `cql()` (SVC-NOTEBOOK-CQL-MAGIC).
START = "%reload_ext cql_databricks.compiler\nfrom cql_databricks.compiler import cql"


def test_every_example_is_a_jupyter_notebook_and_the_index_is_the_only_source_notebook():
    assert [p.name for p in EXAMPLES_DIR.glob("*.py")] == [INDEX]
    index = (EXAMPLES_DIR / INDEX).read_text()
    assert "# COMMAND ----------" not in index, f"{INDEX} is one documentation cell (SVC-EXAMPLE-INDEX)"


def test_there_are_examples():
    assert len(NOTEBOOKS) >= 6


def test_every_example_is_numbered_at_the_top_and_the_index_links_to_each_in_order():
    """SVC-EXAMPLE-NOTEBOOKS, SVC-EXAMPLE-INDEX: every example at the top of `examples/`, named `<NN>_<subtopic>` in
    snake_case and numbered 01 on in reading order, after the index, `00_start_here`; no notebook in a folder. The
    index links to every example, in the order of their numbers."""
    assert all(re.fullmatch(r"\d\d_[a-z][a-z0-9_]*\.ipynb", nb) for nb in NOTEBOOKS), NOTEBOOKS
    numbers = [nb[:2] for nb in NOTEBOOKS]
    assert numbers == [f"{n:02d}" for n in range(1, len(numbers) + 1)], f"the examples are numbered 01 on: {numbers}"
    assert not list(EXAMPLES_DIR.glob("*/**/*.ipynb")), "an example notebook is in a folder"
    index = (EXAMPLES_DIR / INDEX).read_text()
    links = re.findall(r'href="\$\./([^"]+)"', index)
    assert links == [nb[:-len(".ipynb")] for nb in NOTEBOOKS], f"the index links {links}"


@pytest.mark.parametrize("nb", NOTEBOOKS)
def test_the_title_and_the_exit_name_the_notebook(nb):
    """SVC-EXAMPLE-NOTEBOOKS: the title starts with the notebook's number, and the exit payload names the notebook."""
    notebook = json.loads((EXAMPLES_DIR / nb).read_text())
    cells = notebook["cells"]
    assert "".join(cells[0]["source"]).startswith(f"# {nb[:2]} "), f"{nb}: the title starts with its number"
    assert f'"notebook": "{nb[:-len(".ipynb")]}"' in "".join(cells[-1]["source"]), f"{nb}: the exit names it"
    assert notebook["metadata"]["application/vnd.databricks.v1+notebook"]["notebookName"] == nb[:-len(".ipynb")]


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
