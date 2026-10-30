#!/usr/bin/env bash
# Regenerate the synthetic population small in examples/data from its recipe (DATA-SYNTH-SMALL).
#
# Run by hand, only to update the committed sample data: after a change to an example measure, its
# <Measure>.synth.json, the recipe or the synthesizer. No build, test or deploy step runs it. The synthesizer
# test test_recipe.py fails when the committed files differ from what the recipe builds.
#
# Steps: build the recipe's pinned measure fixtures with a local Compiler Service (scripts/lib/synth-fixtures.sh),
# then write the population into examples/data. Offline: no workspace is needed.
# The large population's counterpart is scripts/generate-large-data.sh (DATA-SYNTH-LARGE).
#
# Usage: scripts/generate-example-data.sh
# Then review the diff in examples/data and commit it.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
venv_py="$here/.venv/bin/python"
recipe="$here/loader/synthesizer/recipes/synthetic-small.json"
out="$here/examples/data"
. "$here/scripts/lib/synth-fixtures.sh"

[ -x "$venv_py" ] || { echo "ERROR: venv absent — run: scripts/setup.sh" >&2; exit 1; }

build_recipe_fixtures "$venv_py" "$recipe"

echo "==> writing the population into examples/data…"
"$venv_py" -m cql_databricks_synth population --recipe "$recipe" --out "$out"

echo "==> done. Review the diff in examples/data, then commit it."
