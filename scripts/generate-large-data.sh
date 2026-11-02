#!/usr/bin/env bash
# Generate the synthetic population large from its recipe and land it in the workspace (DATA-SYNTH-LARGE).
#
# Run by hand, only to update the workspace's large population: after a change to an example measure, its
# <Measure>.synth.json, the recipe or the synthesizer. No build, test or deploy step runs it. Deploy first
# (docs/deploy.md): landing needs the catalog and the profile from databricks.local.yml.
#
# Steps: build the pinned measure fixtures with a local Compiler Service (scripts/lib/synth-fixtures.sh), then for
# each cohort generate it with its expected results into a scratch directory, verify it (SYNTH-PDS-VERIFY), land it
# with loader/scripts/synthetic-population/load.py, and delete the scratch copy. Landing a cohort again replaces
# only that cohort. The small population's counterpart is scripts/generate-example-data.sh (DATA-SYNTH-SMALL).
#
# A 250,000-patient cohort is 560 to 900 MB of NDJSON, uploaded gzipped as 10 to 18 MB; one takes about 3.5 to 5
# minutes end to end.
#
# The population is an environment of its own, in its own catalog, never the default (DATA-SYNTH-LARGE): --catalog
# names it, and must not be uc_catalog. --users-group reads it and --designer-app's principal reads its knowledge.
#
# Usage: scripts/generate-large-data.sh --catalog <catalog> --users-group <group> --designer-app <app> \
#          [--measure <measure>]...   (default: every cohort in the recipe)
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
venv_py="$here/.venv/bin/python"
recipe="$here/loader/synthesizer/recipes/synthetic-large.json"
. "$here/scripts/lib/synth-fixtures.sh"

usage="usage: $0 --catalog <catalog> --users-group <group> --designer-app <app> [--measure <measure>]..."
only=(); catalog=""; users_group=""; designer_app=""
while [ $# -gt 0 ]; do case "$1" in
  --measure) [ $# -ge 2 ] || { echo "$usage" >&2; exit 2; }; only+=("$2"); shift 2;;
  --catalog) [ $# -ge 2 ] || { echo "$usage" >&2; exit 2; }; catalog="$2"; shift 2;;
  --users-group) [ $# -ge 2 ] || { echo "$usage" >&2; exit 2; }; users_group="$2"; shift 2;;
  --designer-app) [ $# -ge 2 ] || { echo "$usage" >&2; exit 2; }; designer_app="$2"; shift 2;;
  *) echo "$usage" >&2; exit 2;;
esac; done
[ -n "$catalog" ] && [ -n "$users_group" ] && [ -n "$designer_app" ] || { echo "$usage" >&2; exit 2; }

[ -x "$venv_py" ] || { echo "ERROR: venv absent — run: scripts/setup.sh" >&2; exit 1; }
[ -f "$here/databricks.local.yml" ] || { echo "ERROR: databricks.local.yml not found — see docs/deploy.md" >&2; exit 1; }

cohorts=()
while IFS= read -r line; do cohorts+=("$line"); done < <(recipe_cohorts "$venv_py" "$recipe")
selected=()
for cohort in "${cohorts[@]}"; do
  measure="${cohort% *}"
  if [ ${#only[@]} -eq 0 ] || printf '%s\n' "${only[@]}" | grep -qx "$measure"; then selected+=("$cohort"); fi
done
for measure in ${only[@]+"${only[@]}"}; do
  printf '%s\n' "${cohorts[@]}" | grep -q "^$measure " \
    || { echo "ERROR: $measure is not a cohort in $recipe" >&2; exit 2; }
done

measures=()
for cohort in "${selected[@]}"; do measures+=("${cohort% *}"); done
build_recipe_fixtures "$venv_py" "$recipe" "${measures[@]}"

read -r patients period < <("$venv_py" -c 'import sys
from cql_databricks_synth.recipe import Recipe
recipe = Recipe.from_file(sys.argv[1])
print(recipe.patients, recipe.measurement_period)' "$recipe")

for cohort in "${selected[@]}"; do
  measure="${cohort% *}"; seed="${cohort#* }"
  fixtures="$("$venv_py" -c 'import sys
from cql_databricks_synth.fixtures import fixture_dir
print(fixture_dir(sys.argv[1]))' "$measure")"
  config_args=()
  [ -f "$fixtures/measure-config.json" ] && config_args=(--config "$fixtures/measure-config.json")
  scratch="$(mktemp -d "${TMPDIR:-/tmp}/synth-$measure.XXXXXX")"

  echo "==> $measure: generating $patients patients (seed $seed) into ${scratch}…"
  "$venv_py" -m cql_databricks_synth generate --elm "$fixtures" --valuesets "$fixtures" --out "$scratch" \
    --patients "$patients" --seed "$seed" --measurement-period "$period" ${config_args[@]+"${config_args[@]}"}
  echo "==> $measure: verifying…"
  "$venv_py" -m cql_databricks_synth verify --elm "$fixtures" --valuesets "$fixtures" --out "$scratch"
  echo "==> $measure: landing…"
  "$here/scripts/with-local-env.sh" "$venv_py" "$here/loader/scripts/synthetic-population/load.py" \
    --ndjson-dir "$scratch" --cohort "$measure" --catalog "$catalog" --users-group "$users_group" \
    --designer-app "$designer_app"
  rm -rf "$scratch"
done

echo "==> done: landed ${measures[*]}."
