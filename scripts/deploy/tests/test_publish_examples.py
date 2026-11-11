"""SVC-EXAMPLE-KNOWLEDGE, SVC-EXAMPLE-NOTEBOOKS — what `scripts/deploy/publish-examples.sh` writes beside the notebooks.

The script runs as it is, against a stand-in `databricks` CLI that records every call and reports a `measures`
folder an earlier publish left files in. No live workspace.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/deploy/publish-examples.sh"
EXAMPLES = ROOT / "examples"
MEASURES = "/Shared/cql_databricks/examples/measures"
#: What an earlier publish left in the folder: one library, and two files that are not libraries.
LEFT = ["ColorectalScreening.cql", "ColorectalScreening.measure.json", "ColorectalScreening.synth.json"]

# The stand-in CLI: each call is one JSON line in $CALLS; listing the measures folder answers with $LEFT.
STUB = f"""#!{sys.executable}
import json, os, sys
with open(os.environ["CALLS"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["workspace", "list"] and sys.argv[3] == {MEASURES!r}:
    print(json.dumps([{{"path": f"{MEASURES}/{{n}}", "object_type": "FILE"}} for n in json.loads(os.environ["LEFT"])]))
"""


@pytest.fixture(scope="module")
def calls(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("publish")
    stub = tmp / "databricks"
    stub.write_text(STUB)
    stub.chmod(0o755)
    log = tmp / "calls.jsonl"
    env = {**os.environ, "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}", "CALLS": str(log), "LEFT": json.dumps(LEFT)}
    subprocess.run(["bash", str(SCRIPT), "-p", "synthetic-profile"], check=True, env=env, capture_output=True, text=True)
    return [json.loads(line) for line in log.read_text().splitlines()]


def _imported_under(calls, folder):
    return sorted(c[2][len(folder) + 1:] for c in calls if c[:2] == ["workspace", "import"] and c[2].startswith(f"{folder}/"))


def test_beside_the_notebooks_are_the_libraries_the_knowledge_volume_holds(calls):
    """SVC-EXAMPLE-KNOWLEDGE: the same `.cql` files as the default environment's knowledge volume, `libraries/`."""
    from shipped_sources.examples import volume_files

    in_volume = sorted(p[len("libraries/"):] for p in volume_files(EXAMPLES)["knowledge"] if p.startswith("libraries/"))
    assert in_volume, "the knowledge volume holds no libraries"
    assert _imported_under(calls, MEASURES) == in_volume
    assert not any(c[:2] == ["workspace", "import-dir"] for c in calls), "a whole directory would bring its other files"


def test_a_file_an_earlier_publish_left_that_is_not_a_library_is_removed(calls):
    deleted = sorted(c[2] for c in calls if c[:2] == ["workspace", "delete"])
    assert deleted == [f"{MEASURES}/ColorectalScreening.measure.json", f"{MEASURES}/ColorectalScreening.synth.json"]


def test_every_notebook_is_published_at_its_path_in_its_group(calls):
    """SVC-EXAMPLE-NOTEBOOKS, SVC-EXAMPLE-INDEX: the index, and each example under its group's folder."""
    examples = sorted(p.relative_to(EXAMPLES).with_suffix("").as_posix() for p in EXAMPLES.glob("*/*.ipynb"))
    published = _imported_under(calls, "/Shared/cql_databricks/examples")
    assert set(examples) | {"start_here"} <= set(published)
