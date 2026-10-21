"""SVC-EXAMPLE-NOTEBOOKS, SVC-PACKAGING — every example notebook installs the client from the deployment's Volume.

The runtime Volume is `/Volumes/<uc_catalog>/runtime/artifacts`, and `uc_catalog` is the deployment's choice. So a
notebook names it only as the `__CQL_DATABRICKS_ARTIFACT_VOLUME__` placeholder, which `scripts/deploy/publish-examples.sh`
fills in. A literal catalog in a `%pip` cell fails the first cell of every deployment that chose another catalog.
Offline: reads the repository copies only.
"""
from __future__ import annotations

import pathlib
import re

import pytest

EXAMPLES_DIR = pathlib.Path(__file__).resolve().parents[1]
PLACEHOLDER = "__CQL_DATABRICKS_ARTIFACT_VOLUME__"
NOTEBOOKS = sorted(p.name for p in EXAMPLES_DIR.glob("*.py") if p.name != "00_start_here.py")
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
