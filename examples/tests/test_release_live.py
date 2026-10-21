"""Guarded LIVE release test (CLIENT-RELEASE-TESTS): each sample measure from the built wheels, as a one-time
serverless run.

For every sample measure in ``examples/`` (one ``data/expected-results/<Library>.json`` each), submits
``release/measure_release.py`` (the repository copy) with the deployed Volume and the measure's name, version and
data model filled in, and its expected results passed as a parameter, as ``test_examples_run.py`` does. The notebook
discovers the measure and the sample patients by tag in the deployment's catalog scopes (CONTENT-SAMPLE-PRELOAD),
computes the measure durably, compares each population with its expected result per patient, then checks a second
run reuses every define (CLIENT-INCLUDED-REUSE). It exits ``{"ok": true}`` only when all of that holds.

SKIPS unless the live prerequisites are present, so it is safe in an otherwise offline run. ``scripts/test.sh
integration`` sets them all, and fails the run if this skips:
  - env CQL_DATABRICKS_LIVE=1, with Databricks auth (DATABRICKS_CONFIG_PROFILE);
  - env CQL_DATABRICKS_ARTIFACT_VOLUME: the deployed runtime Volume, holding the wheels;
  - env CQL_DATABRICKS_PUBLISH_CATALOG: the bundle's ``uc_catalog``, which holds the sample measures and patients;
  - env CQL_DATABRICKS_CATALOG_SCOPES: the bundle's ``catalog_scopes`` (CLIENT-DISCOVERY-SCOPE);
  - env CQL_DATABRICKS_RESULTS_CATALOG: the bundle's ``designer_results_catalog``, where the run's private
    results schema goes (RUN-CLIENT-PRIVATE-PROVISIONING).
"""
import base64
import datetime
import json
import os
import pathlib

import pytest

NOTEBOOK = pathlib.Path(__file__).resolve().parent / "release" / "measure_release.py"
EXAMPLES = pathlib.Path(__file__).resolve().parents[1]
#: One expected-results file per sample measure, so a new sample measure is tested with no edit here.
EXPECTED_RESULTS = sorted((EXAMPLES / "data" / "expected-results").glob("*.json"))
VOLUME = os.environ.get("CQL_DATABRICKS_ARTIFACT_VOLUME", "").strip()
CATALOG = os.environ.get("CQL_DATABRICKS_PUBLISH_CATALOG", "").strip()
SCOPES = os.environ.get("CQL_DATABRICKS_CATALOG_SCOPES", "").strip()
RESULTS_CATALOG = os.environ.get("CQL_DATABRICKS_RESULTS_CATALOG", "").strip()
# Serverless notebook environment version, as test_examples_run.py pins it.
SERVERLESS_CLIENT = "4"

pytestmark = pytest.mark.skipif(
    os.environ.get("CQL_DATABRICKS_LIVE", "").strip().lower() not in {"1", "true", "yes", "on"}
    or not (VOLUME and CATALOG and SCOPES and RESULTS_CATALOG),
    reason="set CQL_DATABRICKS_LIVE=1, CQL_DATABRICKS_ARTIFACT_VOLUME, CQL_DATABRICKS_PUBLISH_CATALOG, "
           "CQL_DATABRICKS_CATALOG_SCOPES and CQL_DATABRICKS_RESULTS_CATALOG (scripts/test.sh integration sets "
           "them) to run the release test")


def _filled_in(expected: dict) -> str:
    """The notebook with the Volume and the measure filled in: name, version, data model (its `using` line) and the
    population its `%%cql` library reads."""
    measure = expected["measure"]
    cql = (EXAMPLES / "measures" / f"{measure}.cql").read_text()
    model = next(line.strip() for line in cql.splitlines() if line.strip().startswith("using "))
    return (NOTEBOOK.read_text().replace("__CQL_DATABRICKS_ARTIFACT_VOLUME__", VOLUME)
            .replace("__MEASURE__", measure).replace("__VERSION__", expected["version"])
            .replace("__MODEL__", model).replace("__POPULATION__", next(iter(expected["populations"]))))


def test_there_is_a_sample_measure_to_release():
    assert EXPECTED_RESULTS, f"no expected results under {EXAMPLES / 'data' / 'expected-results'}"


@pytest.mark.parametrize("expected_file", EXPECTED_RESULTS, ids=[p.stem for p in EXPECTED_RESULTS])
def test_measure_release_run_matches_its_expected_results_and_reuses(expected_file):
    """CLIENT-RELEASE-TESTS, CLIENT-KNOWLEDGE-INCLUDES, RUN-CLIENT-RESUME, CLIENT-INCLUDED-REUSE — the notebook's
    checks, live."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service import compute, jobs, workspace

    ws = WorkspaceClient()
    folder = f"/Users/{ws.current_user.me().user_name}/_cql_databricks_release_test"
    ws.workspace.mkdirs(folder)
    path = f"{folder}/measure_release"
    expected = json.loads(expected_file.read_text())
    source = _filled_in(expected)
    ws.workspace.import_(path=path, content=base64.b64encode(source.encode()).decode(),
                         format=workspace.ImportFormat.SOURCE, language=workspace.Language.PYTHON, overwrite=True)
    try:
        waiter = ws.jobs.submit(
            run_name="cql-databricks-release-test",
            tasks=[jobs.SubmitTask(
                task_key="run",
                notebook_task=jobs.NotebookTask(notebook_path=path, base_parameters={
                    "catalog_scopes": SCOPES, "results_catalog": RESULTS_CATALOG,
                    "expected_results": json.dumps(expected)}),
                environment_key="default")],
            environments=[jobs.JobEnvironment(environment_key="default",
                                              spec=compute.Environment(client=SERVERLESS_CLIENT))])
        run_id = waiter.run_id
        try:
            run = waiter.result(timeout=datetime.timedelta(minutes=40))
        except Exception:
            # Re-read the run so the assertion reports the notebook's error, not the waiter's.
            run = ws.jobs.get_run(run_id)
        out = ws.jobs.get_run_output(run.tasks[0].run_id)
        assert run.state.result_state == jobs.RunResultState.SUCCESS, (
            f"release run {run_id} did not succeed: {run.state.result_state} — {out.error}\n{out.error_trace or ''}")
        payload = json.loads(out.notebook_output.result)
        assert payload.get("ok") is True, f"release run reported not-ok: {payload}"
    finally:
        ws.workspace.delete(folder, recursive=True)
