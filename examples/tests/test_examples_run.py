"""Guarded LIVE test that RUNS each example notebook end-to-end so they cannot silently rot.

This test imports each RUNNABLE notebook in `examples/` into a temp workspace folder,
submits it as a one-time job run on SERVERLESS compute, waits, and asserts result_state SUCCESS with the
`{"ok": true, ...}` payload every module exits on. Running the REPO copy (not a deployed copy) is
deliberate: it validates the notebooks as they live in source, which is what keeps them up to date
(BUILD-CLIENT, SVC-RUN-EVAL).

SCOPE — this is the SMOKE, not the oracle (SVC-EXAMPLE-NOTEBOOKS, 2026-08-09). The notebooks are
narrative-led: each cell's expected result is stated in the markdown above it, not asserted in code. So a
green run proves every compile and every query in every module succeeded against a fresh deployment — it
does NOT prove the values were right. Value-level correctness is owned by the engine's conformance corpus
and its differential against the Java reference, which is where a CQL semantics regression belongs.

SERVERLESS, not a cluster (`SVC-NOTEBOOK-RUNTIME-BIND`): the examples need no cluster library and no
`%scala` cell, because the `cql_*` catalog is registered as UC Scala functions (`SVC-RUNTIME-UDF-UC`) and
the notebook client qualifies every emitted call against it. So the only live prerequisites are a deployed
workspace and auth — there is no cluster id to supply.

SKIPS automatically unless the live prerequisites are present, so it is safe in an otherwise offline run:
  - env CQL_DATABRICKS_LIVE=1 — opt in (these submit real jobs and cost real compute).
  - Databricks auth resolvable by the SDK (DATABRICKS_CONFIG_PROFILE or the ambient config).
  - databricks-sdk importable.
  - env CQL_DATABRICKS_ARTIFACT_VOLUME=/Volumes/<uc_catalog>/runtime/artifacts — the deployed runtime Volume.
    Each notebook's `%pip` cell names it as a placeholder, which this test fills in as
    `scripts/deploy/publish-examples.sh` does (SVC-EXAMPLE-NOTEBOOKS). Required when live: the test fails without it.
The workspace must be DEPLOYED (scripts/deploy/deploy.sh): the client wheels in the runtime Volume, the cql_*
UC functions registered, and the Compiler Service App running (SVC-RUNTIME-UDF-UC, SVC-PACKAGING).
Run:  CQL_DATABRICKS_LIVE=1 DATABRICKS_CONFIG_PROFILE=<profile> \
        CQL_DATABRICKS_ARTIFACT_VOLUME=/Volumes/<uc_catalog>/runtime/artifacts python -m pytest \
        examples/tests/test_examples_run.py -v
"""
import base64
import datetime
import json
import os
import pathlib

import pytest

# This test lives INSIDE the examples it runs (examples/tests/), so the notebooks are its parent.
# It used to sit in a top-level examples/tests/ and reach sideways into the client package -- a path
# that broke silently in the merge and pointed at a directory that did not exist.
EXAMPLES_DIR = pathlib.Path(__file__).resolve().parents[1]
# `00_start_here` is the index page (SVC-EXAMPLE-INDEX): one markdown cell, no code, so there is nothing
# to submit and no `ok` payload to assert. It is excluded by name rather than by "has no code cells" —
# an example that stopped exiting with a payload should FAIL this test, not quietly opt out of it.
INDEX_NOTEBOOK = "00_start_here.py"
NOTEBOOKS = sorted(p.name for p in EXAMPLES_DIR.glob("*.py") if p.name != INDEX_NOTEBOOK)

# The content directories the notebooks mount (SVC-EXAMPLE-KNOWLEDGE). Notebooks 01 and 03 derive their
# paths from their OWN notebook location, so they must be staged BESIDE them in the temp folder — exactly
# as `scripts/deploy/publish-examples.sh` lands them beside the notebooks in /Shared. Staging the .py files alone
# would fail 03 at its first cell, which is the correct failure but a useless test.
CONTENT_DIRS = ("measures", "data")

# Serverless notebook environment version. "4" = DBR 17.3, matching the repo's pin (see databricks.yml).
SERVERLESS_CLIENT = "4"

pytestmark = pytest.mark.skipif(
    os.environ.get("CQL_DATABRICKS_LIVE", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="set CQL_DATABRICKS_LIVE=1 (+ Databricks auth) to run the example notebooks against a deployed "
           "workspace on serverless")


@pytest.fixture(scope="module")
def ws():
    sdk = pytest.importorskip("databricks.sdk")
    return sdk.WorkspaceClient()


@pytest.fixture(scope="module")
def dest_dir(ws):
    from databricks.sdk.service import workspace

    me = ws.current_user.me().user_name
    path = f"/Users/{me}/_cql_databricks_nb_test"
    ws.workspace.mkdirs(path)

    # Stage the content directories beside where the notebooks will land, preserving each tree. AUTO
    # format keeps every file byte-identical as a workspace FILE, which is what a notebook's plain
    # open() reads.
    for d in CONTENT_DIRS:
        root = EXAMPLES_DIR / d
        for f in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = f.relative_to(root)
            sub = rel.parent.as_posix()
            ws.workspace.mkdirs(f"{path}/{d}/{sub}" if sub != "." else f"{path}/{d}")
            ws.workspace.import_(
                path=f"{path}/{d}/{rel.as_posix()}",
                content=base64.b64encode(f.read_bytes()).decode(),
                format=workspace.ImportFormat.AUTO,
                overwrite=True)

    yield path
    try:
        ws.workspace.delete(path, recursive=True)
    except Exception:
        pass


@pytest.mark.parametrize("nb", NOTEBOOKS)
def test_example_notebook_runs(ws, dest_dir, nb):
    from databricks.sdk.service import compute, jobs, workspace

    volume = os.environ.get("CQL_DATABRICKS_ARTIFACT_VOLUME", "").strip()
    assert volume, "set CQL_DATABRICKS_ARTIFACT_VOLUME=/Volumes/<uc_catalog>/runtime/artifacts (the deployed Volume)"
    src = (EXAMPLES_DIR / nb).read_text().replace("__CQL_DATABRICKS_ARTIFACT_VOLUME__", volume)
    wpath = f"{dest_dir}/{nb[:-3]}"  # drop .py
    ws.workspace.import_(
        path=wpath,
        content=base64.b64encode(src.encode()).decode(),
        format=workspace.ImportFormat.SOURCE,
        language=workspace.Language.PYTHON,
        overwrite=True)

    waiter = ws.jobs.submit(
        run_name=f"cql-databricks-nb-test-{nb}",
        tasks=[jobs.SubmitTask(
            task_key="run",
            notebook_task=jobs.NotebookTask(notebook_path=wpath),
            environment_key="default")],
        environments=[jobs.JobEnvironment(
            environment_key="default",
            spec=compute.Environment(client=SERVERLESS_CLIENT))])
    run_id = waiter.run_id
    try:
        run = waiter.result(timeout=datetime.timedelta(minutes=40))
    except Exception:
        # A failed run raises out of the waiter; re-read it so the assertion below reports the real
        # notebook error rather than the waiter's generic "workload failed" (CORE-NO-SILENT-MISLOWER).
        run = ws.jobs.get_run(run_id)

    task_run_id = run.tasks[0].run_id
    out = ws.jobs.get_run_output(task_run_id)
    assert run.state.result_state == jobs.RunResultState.SUCCESS, (
        f"{nb} did not succeed: {run.state.result_state} — {out.error}\n{out.error_trace or ''}")

    payload = json.loads(out.notebook_output.result)
    assert payload.get("ok") is True, f"{nb} reported not-ok: {payload}"
