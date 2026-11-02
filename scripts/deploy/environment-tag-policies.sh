#!/usr/bin/env bash
# Govern the environment tags, so only the project's admins can tag a catalog an environment or the default
# (DEPLOY-ENVIRONMENT-TAG-POLICIES). Runs before the deploy tags any catalog. A bundle has no tag-policy resource, so
# this is a bundle script: `databricks bundle run environment_tag_policies` runs it in the bundle's auth and target, and
# scripts/deploy/deploy.sh calls it directly.
#
# Usage: scripts/deploy/environment-tag-policies.sh [-t <target>] [-p <profile>]
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

cfg="$(cd "$here" && databricks bundle validate -t "$target" --profile "$profile" -o json)"
"$here/.venv/bin/python" "$here/scripts/deploy/environment_tag_policies.py" "$cfg" "$profile"
