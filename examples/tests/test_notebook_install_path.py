"""SVC-EXAMPLE-NOTEBOOKS, SVC-PACKAGING — every example notebook installs the client from the deployment's Volume.

The runtime Volume is `/Volumes/<uc_catalog>/runtime/artifacts`, and `uc_catalog` is the deployment's choice. So a
notebook names it only as the `__CQL_DATABRICKS_ARTIFACT_VOLUME__` placeholder, which `scripts/deploy/publish-examples.sh`
fills in. A literal catalog in a `%pip` cell fails the first cell of every deployment that chose another catalog.
A `.ipynb` example carries the wheel in its serverless environment instead, as the `__CQL_DATABRICKS_CLIENT_WHEEL__`
placeholder, which the same script fills with the one wheel in the Volume.
Offline: reads the repository copies only.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

EXAMPLES_DIR = pathlib.Path(__file__).resolve().parents[1]
PLACEHOLDER = "__CQL_DATABRICKS_ARTIFACT_VOLUME__"
NOTEBOOKS = sorted(p.name for p in EXAMPLES_DIR.glob("*.py") if p.name != "00_start_here.py")
JUPYTER_NOTEBOOKS = sorted(p.name for p in EXAMPLES_DIR.glob("*.ipynb"))
WHEEL_PLACEHOLDER = "__CQL_DATABRICKS_CLIENT_WHEEL__"
PIP_LINE = re.compile(r"^# MAGIC %pip install (.+)$", re.MULTILINE)


@pytest.mark.parametrize("nb", NOTEBOOKS)
def test_pip_cell_installs_from_the_volume_placeholder(nb):
    installs = PIP_LINE.findall((EXAMPLES_DIR / nb).read_text())
    assert installs, f"{nb} has no %pip install cell"
    for line in installs:
        paths = line.split()
        assert all(p.startswith(PLACEHOLDER + "/") for p in paths), (
            f"{nb}: every %pip path must start with {PLACEHOLDER}/, got {line!r}")
        assert len(paths) == len(set(paths)), f"{nb}: a wheel is listed twice in {line!r}"


@pytest.mark.parametrize("nb", JUPYTER_NOTEBOOKS)
def test_jupyter_notebook_environment_installs_the_wheel_placeholder(nb):
    notebook = json.loads((EXAMPLES_DIR / nb).read_text())
    env = notebook["metadata"]["application/vnd.databricks.v1+notebook"]["environmentMetadata"]
    assert env["dependencies"] == [WHEEL_PLACEHOLDER], (
        f"{nb}: the environment must install exactly {WHEEL_PLACEHOLDER}, got {env['dependencies']!r}")
    code = "".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code")
    assert "%pip install" not in code, f"{nb}: the wheel comes from the environment, not a %pip cell"
    assert "/Volumes/" not in code, f"{nb}: a literal Volume path names one deployment's catalog"
