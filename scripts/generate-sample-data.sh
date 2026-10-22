#!/usr/bin/env bash
# Regenerate the synthetic population small in examples/data from its recipe (DATA-SYNTH-SMALL).
#
# Run by hand, only to update the committed sample data: after a change to a sample measure, its
# <Measure>.synth.json, the recipe or the synthesizer. No build, test or deploy step runs it. The synthesizer
# test test_recipe.py fails when the committed files differ from what the recipe builds.
#
# Steps: build the Compiler Service, start it on a free local port, build the recipe's pinned measure fixtures
# (SYNTH-PDS-FIXTURE-PINS), write the population into examples/data, stop the Compiler Service. Offline: the
# sample measures' expansions are files in this repository, so no workspace is needed.
#
# Usage: scripts/generate-sample-data.sh
# Then review the diff in examples/data and commit it.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
venv_py="$here/.venv/bin/python"
recipe="$here/loader/synthesizer/recipes/synthetic-small.json"
out="$here/examples/data"
compiler_bin="$here/compiler/server/build/install/compiler-app/bin/compiler-app"

[ -x "$venv_py" ] || { echo "ERROR: venv absent — run: scripts/setup.sh" >&2; exit 1; }

echo "==> building Compiler Service (Gradle installDist)…"
"$here/gradlew" -q -p "$here" :compiler:server:installDist

port="$("$venv_py" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
endpoint="http://127.0.0.1:$port"
echo "==> starting Compiler Service on :${port}…"
PORT="$port" "$compiler_bin" >/dev/null 2>&1 &
compiler_pid=$!
trap 'kill "$compiler_pid" 2>/dev/null && wait "$compiler_pid" 2>/dev/null || true' EXIT INT TERM
for _ in $(seq 1 120); do
  curl -sf -m 2 "$endpoint/health" >/dev/null 2>&1 && break
  kill -0 "$compiler_pid" 2>/dev/null || { echo "ERROR: Compiler Service exited before it was ready" >&2; exit 1; }
  sleep 0.5
done
curl -sf -m 2 "$endpoint/health" >/dev/null || { echo "ERROR: Compiler Service not ready at $endpoint" >&2; exit 1; }

measure_args=()
while IFS= read -r measure; do measure_args+=(--measure "$measure"); done < <(
  "$venv_py" -c 'import json, sys; [print(c["measure"]) for c in json.load(open(sys.argv[1]))["cohorts"]]' "$recipe")

echo "==> building the recipe's measure fixtures…"
"$venv_py" -m cql_databricks_synth fixtures --endpoint "$endpoint" "${measure_args[@]}"

echo "==> writing the population into examples/data…"
"$venv_py" -m cql_databricks_synth population --recipe "$recipe" --out "$out"

echo "==> done. Review the diff in examples/data, then commit it."
