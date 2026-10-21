#!/usr/bin/env bash
# Create (or reuse) the maintenance service principal and grant it authority over the private catalog
# (RUN-CLIENT-MAINTENANCE).
#
# The private catalog is a PLATFORM PREREQUISITE (RUN-CLIENT-PRIVATE-PROVISIONING): an administrator creates
# it and grants Designer users USE CATALOG and CREATE SCHEMA. Deployment never creates it and grants users
# nothing on it. Its one identity and grant is the dedicated principal the daily cql-maintenance Job runs as,
# which drops expired results in every user's schema:
#   USE_CATALOG, USE_SCHEMA   reach every user's schema
#   MANAGE                    inherited by every schema and table; deletes objects it does not own
# plus CAN_USE on the configured warehouse, which the Job's SQL runs on. The principal has no secret.
#
# Idempotent: the principal is found by its hashed display name and grants are additive. Creating a service
# principal needs workspace admin; granting needs OWNERSHIP of the catalog (or MANAGE). A missing catalog
# fails the deploy.
#
# Prints the principal's application ID on stdout: deploy.sh passes it to the bundle as `maintenance_principal`,
# the run-as of the `cql-maintenance` Job.
#
# Usage: scripts/deploy/grant-maintenance-access.sh [-t <target>] [-p <profile>]
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
# Progress goes to stderr; stdout is the principal's application ID alone, which deploy.sh reads.
"$here/.venv/bin/python" "$here/scripts/deploy/maintenance_principal.py" "$cfg" "$profile"
