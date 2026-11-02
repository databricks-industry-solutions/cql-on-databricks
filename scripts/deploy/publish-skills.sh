#!/usr/bin/env bash
# Publish every skill, from one definition, to Unity Catalog and the workspace skills folder (DEPLOY-AGENT-SKILLS).
#
# Reads this deployment's settings from the bundle and runs scripts/deploy/publish_skills.py, which says where each
# skill goes and why. Called by scripts/deploy/deploy.sh and by the bundle script `publish_skills`
# (`databricks bundle run publish_skills`): a bundle has no resource type for a skill or for a workspace folder.
# Runs after the bundle deploy.
#
# Usage: scripts/deploy/publish-skills.sh [-t <target>] [-p <profile>]
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
target="${DATABRICKS_BUNDLE_TARGET:-default}"; profile_args=()
while getopts "t:p:" o; do case "$o" in
  t) target="$OPTARG";;
  p) profile_args=(-p "$OPTARG");;
esac; done
. "$here/scripts/lib/databricks-profile.sh"
if [ ${#profile_args[@]} -eq 0 ]; then _p="$(config_profile -t "$target")"; [ -z "$_p" ] || profile_args=(-p "$_p"); fi
venv_py="$here/.venv/bin/python"
[ -x "$venv_py" ] || { echo "!!! venv absent — run: scripts/setup.sh"; exit 1; }

cfg="$(cd "$here" && databricks bundle validate -t "$target" ${profile_args[@]+"${profile_args[@]}"} -o json)"
var() { printf '%s' "$cfg" | python3 -c "import sys,json;v=json.load(sys.stdin).get('variables') or {};print((v.get(sys.argv[1]) or {}).get('value') or '')" "$1"; }
args=(--schema "$(var agent_skills_schema)" --users-group "$(var agent_users_group)"
      --workspace-users-group "$(var workspace_users_group)")

[ ${#profile_args[@]} -eq 0 ] || export DATABRICKS_CONFIG_PROFILE="${profile_args[1]}"
"$venv_py" "$here/scripts/deploy/publish_skills.py" "${args[@]}"
