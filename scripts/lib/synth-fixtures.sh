# Sourced by scripts/generate-*-data.sh — build a recipe's pinned measure fixtures (SYNTH-PDS-FIXTURE-PINS)
# with a local Compiler Service that lives only as long as the calling script.
#
#   build_recipe_fixtures <venv python> <recipe> [<measure>...]
#
# Builds the Compiler Service, starts it on a free local port, builds the fixtures of the named measures (default:
# every cohort in the recipe), and stops the service on the caller's exit. Offline: the pinned expansions of the
# sample measures are files in this repository.
_synth_fixtures_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_recipe_fixtures() {
  local venv_py="$1" recipe="$2"; shift 2
  local compiler_bin="$_synth_fixtures_root/compiler/server/build/install/compiler-app/bin/compiler-app"
  echo "==> building Compiler Service (Gradle installDist)…"
  "$_synth_fixtures_root/gradlew" -q -p "$_synth_fixtures_root" :compiler:server:installDist

  local port endpoint
  port="$("$venv_py" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
  endpoint="http://127.0.0.1:$port"
  echo "==> starting Compiler Service on :${port}…"
  PORT="$port" "$compiler_bin" >/dev/null 2>&1 &
  _synth_compiler_pid=$!
  trap 'kill "$_synth_compiler_pid" 2>/dev/null && wait "$_synth_compiler_pid" 2>/dev/null || true' EXIT INT TERM
  for _ in $(seq 1 120); do
    curl -sf -m 2 "$endpoint/health" >/dev/null 2>&1 && break
    kill -0 "$_synth_compiler_pid" 2>/dev/null || { echo "ERROR: Compiler Service exited before it was ready" >&2; return 1; }
    sleep 0.5
  done
  curl -sf -m 2 "$endpoint/health" >/dev/null || { echo "ERROR: Compiler Service not ready at $endpoint" >&2; return 1; }

  local measures=("$@") measure_args=() measure
  if [ ${#measures[@]} -eq 0 ]; then
    while IFS=' ' read -r measure _; do measures+=("$measure"); done < <(recipe_cohorts "$venv_py" "$recipe")
  fi
  for measure in "${measures[@]}"; do measure_args+=(--measure "$measure"); done
  echo "==> building the measure fixtures…"
  "$venv_py" -m cql_databricks_synth fixtures --endpoint "$endpoint" "${measure_args[@]}"
}

#   recipe_cohorts <venv python> <recipe>   # prints "<measure> <seed>" per cohort, after the recipe's own checks
recipe_cohorts() {
  "$1" -c 'import sys
from cql_databricks_synth.recipe import Recipe
for cohort in Recipe.from_file(sys.argv[1]).cohorts:
    print(cohort.measure, cohort.seed)' "$2"
}
