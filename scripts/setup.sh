#!/usr/bin/env bash
# Bootstrap the local dev environment — one-time setup for a fresh workstation (SVC-APP-TOPOLOGY).
# Creates the CQL on Databricks app Python venv (databricks-connect + FastAPI + dev deps) and installs the SPA's
# npm dependencies. Re-run any time to rebuild from scratch (e.g. after a repo rename or Python upgrade).
#
#   scripts/setup.sh          # create venv + npm install
#
# After this, `scripts/run-app.sh --dev` and `scripts/test.sh` both work.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
venv="$here/.venv"
venv_py="$venv/bin/python"

# ONE venv at the repository root (CI uses the same path), holding every Python component editable.
echo "==> venv ($venv) over python3.12 + editable client, common, designer server[dev], agent (service, skills, server), shipped sources, synthesizer"
python3.12 -m venv "$venv"
"$venv_py" -m pip install -q --upgrade pip
# databricks-connect: the Spark sessions of the examples smoke and the loaders (SVC-RUN-DBCONNECT).
"$venv_py" -m pip install -e "$here/compiler/client" -e "$here/common[test]" -e "$here/designer/server[dev]" \
  -e "$here/agent/service[test]" -e "$here/agent/skills" -e "$here/agent/server[test]" \
  -e "$here/scripts/deploy/shipped_sources[dev]" \
  -e "$here/loader/synthesizer[dev]" "databricks-connect~=17.3"

# Synthesizer measure fixtures are GENERATED, never committed (SYNTH-PDS-FIXTURE-PINS).
# Best-effort build: needs the local Compiler Service (ELM) and databricks.local.yml (the catalog of the UC
# expansions); when either is missing the synth tests SKIP with this exact fix.
if [ ! -f "$here/databricks.local.yml" ]; then
  echo "  databricks.local.yml not found — synth fixtures skipped (after configuring: scripts/with-local-env.sh .venv/bin/python -m cql_databricks_synth fixtures)"
elif curl -sf -m 2 "${CQL_COMPILER_ENDPOINT:-http://127.0.0.1:8080}/health" >/dev/null 2>&1; then
  "$here/scripts/with-local-env.sh" "$venv_py" -m cql_databricks_synth fixtures \
    || echo "  synth fixtures incomplete — re-run: scripts/with-local-env.sh .venv/bin/python -m cql_databricks_synth fixtures"
else
  echo "  Compiler Service not running — synth fixtures skipped (scripts/with-local-env.sh .venv/bin/python -m cql_databricks_synth fixtures)"
fi

echo "==> npm install (CQL on Databricks SPA)"
( cd "$here/designer" && npm install --no-audit --no-fund )

echo "==> setup done — now: scripts/run-app.sh --dev   (or scripts/test.sh)"
