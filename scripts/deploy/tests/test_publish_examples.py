"""SVC-EXAMPLE-KNOWLEDGE, SVC-EXAMPLE-NOTEBOOKS — what `scripts/deploy/publish-examples.sh` writes beside the notebooks.

The script runs as it is, against a stand-in `databricks` CLI that records every call and reports a `measures`
folder an earlier publish left files in, and a published root an earlier layout left notebooks and folders in. No live
workspace.
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
ROOT_FOLDER = "/Shared/cql_databricks/examples"
MEASURES = f"{ROOT_FOLDER}/measures"
#: What an earlier publish left in the folder: one library, and two files that are not libraries.
LEFT = ["ColorectalScreening.cql", "ColorectalScreening.measure.json", "ColorectalScreening.synth.json"]
#: What an earlier publish left at the top: a notebook and the masthead this one writes again, the `measures` folder, an
#: index and a notebook under names since retired, and the group folders of an earlier layout.
LEFT_AT_ROOT = [("01_getting_started", "NOTEBOOK"), ("start-here-hero.png", "FILE"), ("measures", "DIRECTORY"),
                ("start_here", "NOTEBOOK"), ("01_overview", "NOTEBOOK"),
                ("getting_started", "DIRECTORY"), ("scenarios", "DIRECTORY")]

# The stand-in CLI: each call is one JSON line in $CALLS; listing the measures folder answers with $LEFT.
STUB = f"""#!{sys.executable}
import json, os, sys
with open(os.environ["CALLS"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["workspace", "list"] and sys.argv[3] == {MEASURES!r}:
    print(json.dumps([{{"path": f"{MEASURES}/{{n}}", "object_type": "FILE"}} for n in json.loads(os.environ["LEFT"])]))
if sys.argv[1:3] == ["workspace", "list"] and sys.argv[3] == {ROOT_FOLDER!r}:
    print(json.dumps([{{"path": f"{ROOT_FOLDER}/{{n}}", "object_type": t}} for n, t in json.loads(os.environ["LEFT_AT_ROOT"])]))
"""


@pytest.fixture(scope="module")
def calls(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("publish")
    stub = tmp / "databricks"
    stub.write_text(STUB)
    stub.chmod(0o755)
    log = tmp / "calls.jsonl"
    env = {**os.environ, "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}", "CALLS": str(log), "LEFT": json.dumps(LEFT),
           "LEFT_AT_ROOT": json.dumps(LEFT_AT_ROOT)}
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


def _deleted(calls):
    return sorted((c[2], "--recursive" in c) for c in calls if c[:2] == ["workspace", "delete"])


def test_a_file_an_earlier_publish_left_that_is_not_a_library_is_removed(calls):
    deleted = [d for d in _deleted(calls) if d[0].startswith(f"{MEASURES}/")]
    assert deleted == [(f"{MEASURES}/ColorectalScreening.measure.json", False),
                       (f"{MEASURES}/ColorectalScreening.synth.json", False)]


def test_a_notebook_or_folder_an_earlier_layout_left_at_the_top_is_removed(calls):
    """SVC-EXAMPLE-NOTEBOOKS: the published root holds the index, the notebooks, the masthead and `measures`, and
    nothing an earlier publish left; a folder goes with everything in it."""
    deleted = [d for d in _deleted(calls) if not d[0].startswith(f"{MEASURES}/")]
    assert deleted == [(f"{ROOT_FOLDER}/01_overview", False), (f"{ROOT_FOLDER}/getting_started", True),
                       (f"{ROOT_FOLDER}/scenarios", True), (f"{ROOT_FOLDER}/start_here", False)]


def test_every_notebook_is_published_at_the_top_under_its_own_name(calls):
    """SVC-EXAMPLE-NOTEBOOKS, SVC-EXAMPLE-INDEX: the index and every example, at the top of the published folder."""
    examples = sorted(p.stem for p in EXAMPLES.glob("*.ipynb"))
    assert examples, "no example notebook at the top of examples/"
    published = _imported_under(calls, ROOT_FOLDER)
    assert set(examples) | {"00_start_here"} <= set(published)
    assert not [p for p in published if "/" in p and not p.startswith("measures/")], published
