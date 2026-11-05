#!/usr/bin/env bash
# Publish the workspace base environment that carries the client wheel, and make it the workspace default when
# default_base_environment is true (DEPLOY-BASE-ENVIRONMENT). Runs after the wheels are in the runtime Volume. A bundle
# has no base-environment resource, so this is a bundle script: `databricks bundle run base_environment` runs it in
# the bundle's auth and target, and scripts/deploy/deploy.sh calls it directly.
#
# Usage: scripts/deploy/base-environment.sh [-t <target>] [-p <profile>]
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
target="${DATABRICKS_BUNDLE_TARGET:-default}"; profile=""
while getopts "t:p:" o; do case "$o" in
  t) target="$OPTARG";;
  p) profile="$OPTARG";;
  *) exit 2;;
esac; done
. "$here/scripts/lib/databricks-profile.sh"
if [ -z "$profile" ]; then profile="$(config_profile -t "$target")"; fi
[ -n "$profile" ] || { echo "Select a profile in databricks.local.yml or pass -p <profile>." >&2; exit 1; }
# SVC-WHEEL-VERSION — the version the deploy built and uploaded: deploy.sh exports it, and run on its own it is
# computed from the same sources.
. "$here/scripts/lib/client-wheel-version.sh"

cfg="$(cd "$here" && databricks bundle validate -t "$target" --profile "$profile" -o json)"
"$here/.venv/bin/python" "$here/scripts/deploy/base_environment.py" "$cfg" "$profile" "$(client_wheel_version)"
