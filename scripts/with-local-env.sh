#!/usr/bin/env bash
# Run a command in the local environment (SVC-LOCAL-CONFIG-BUNDLE): workspace settings from
# databricks.local.yml, local-run settings and secrets from the optional root .env.
# Usage: scripts/with-local-env.sh [-t <target>] <command> [args...]
#   e.g. scripts/with-local-env.sh .venv/bin/python -m cql_databricks_synth fixtures
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
. "$here/scripts/lib/local-env.sh"
target_args=()
if [ "${1:-}" = "-t" ]; then target_args=(-t "$2"); shift 2; fi
[ $# -gt 0 ] || { echo "usage: scripts/with-local-env.sh [-t <target>] <command> [args...]" >&2; exit 2; }
load_local_env ${target_args[@]+"${target_args[@]}"}
exec "$@"
