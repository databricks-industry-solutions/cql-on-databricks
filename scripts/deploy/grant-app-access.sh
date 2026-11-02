#!/usr/bin/env bash
# Grant each deployed App's SERVICE PRINCIPAL the access it needs to run (DEPLOY-APP-GRANTS).
#
# THE GAP THIS CLOSES. `bundle deploy` creates the Apps, and each App gets its own service principal —
# an identity that did not exist when the catalog or the schema were provisioned.
# Nothing then granted that identity anything, so every deploy produced Apps that start and immediately
# die. It is invisible in local dev because a developer runs as themselves, and they own everything.
#
# SCOPE: Unity Catalog only. The Designer's Lakebase is removed. An App `uc_securable` resource
# covers only CONNECTION/FUNCTION/TABLE/VOLUME, and what the readers need is CATALOG-level. Without
# USE_CATALOG an App dies with `PERMISSION_DENIED: User does not have USE CATALOG on Catalog '<catalog>'`.
#
# Idempotent: re-granting is a no-op. Runs AFTER the bundle deploy (the Apps and their service
# principals must exist).
#
# Usage: scripts/deploy/grant-app-access.sh [-t <target>] [-p <profile>]
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
# DATABRICKS_BUNDLE_TARGET is exported by `databricks bundle run`, which is how this runs as part of the
# bundle (see `scripts:` in databricks.yml); -t overrides it for a direct call from deploy.sh.
target="${DATABRICKS_BUNDLE_TARGET:-default}"; profile_args=()
while getopts "t:p:" o; do case "$o" in
  t) target="$OPTARG";;
  p) profile_args=(-p "$OPTARG");;
esac; done
# No -p: the profile databricks.local.yml names (SVC-DEPLOY-ENV-LOCAL), so the plain CLI calls below
# reach the same workspace the bundle does.
. "$here/scripts/lib/databricks-profile.sh"
if [ ${#profile_args[@]} -eq 0 ]; then _p="$(config_profile -t "$target")"; [ -z "$_p" ] || profile_args=(-p "$_p"); fi

cfg="$(cd "$here" && databricks bundle validate -t "$target" ${profile_args[@]+"${profile_args[@]}"} -o json)"
jqpy() { printf '%s' "$cfg" | python3 -c "import sys,json;d=json.load(sys.stdin);$1"; }

catalog="$(jqpy "v=d.get('variables') or {};print((v.get('uc_catalog') or {}).get('value') or '')")"
# APP-PER-FEATURE — two Python Apps, one privilege set. The compiler App is a JVM host that touches no
# data (SVC-STATELESS, EXEC-SQL-BACKEND), so it gets no catalog privileges. An earlier cut of
# this script looped over every app in the bundle and handed the compiler the full set; that is
# over-permissioning a non-human identity for no capability it uses.
app_name() { jqpy "a=(d.get('resources') or {}).get('apps') or {};x=a.get('$1') or {};print(x.get('name') or '')"; }
designer_app="$(app_name cql_databricks_designer)"
agent_app="$(app_name cql_databricks_agent)"
[ -n "$catalog" ] || { echo "!!! could not resolve uc_catalog from the bundle"; exit 1; }
for v in designer_app agent_app; do
  [ -n "${!v}" ] || { echo "!!! could not resolve the ${v%_app} App from the bundle"; exit 1; }
done
echo "==> catalog=$catalog apps=$designer_app $agent_app"

# DEPLOY-APP-GRANTS, RUN-CLIENT-DRAFT-RESULTS — the App service principals get USE_CATALOG on the catalog and nothing
# that reaches data: the catalog is the default environment, and a catalog-wide SELECT would be inherited by every
# user's table in draft_results. Every result read is the signed-in user's (SEC-OBO). The Designer's principal reads
# the environment's knowledge and terminology as itself (KNOWLEDGE-LAKE-DIRECT); those schema grants are bundle
# resources (databricks.yml). USE_CATALOG is here because a bundle grants on a catalog only when it holds the catalog,
# and this one is the deployer's. An earlier deploy granted USE_SCHEMA and SELECT catalog-wide, so they are revoked.
catalog_privs='["USE_CATALOG"]'
revoked_privs='["USE_SCHEMA","SELECT"]'

# grant_on <securable type> <full name> <principal> <privileges JSON> — additive. FATAL on failure (no
# `|| true`): a silently skipped grant produces an App that deploys "successfully" and then crashes on
# startup, which is precisely the failure mode this script exists to end.
grant_on() {
  if ! databricks grants update "$1" "$2" ${profile_args[@]+"${profile_args[@]}"} \
         --json "{\"changes\":[{\"principal\":\"$3\",\"add\":$4}]}" >/dev/null; then
    echo "!!! could not grant on $1 '$2'. Granting requires OWNERSHIP of the catalog (or MANAGE)"
    echo "!!! — check with: databricks catalogs get ${2%%.*} -o json | grep owner"
    echo "!!! The equivalent SQL, for someone who has it:"
    echo "!!!   GRANT $(echo "$4" | tr -d '[]"' | tr '_,' ' ,') ON $1 $2 TO \`$3\`;"
    exit 1
  fi
  echo "    UC: granted $4 on $1 $2"
}

for app in "$designer_app" "$agent_app"; do
  sp="$(databricks apps get "$app" ${profile_args[@]+"${profile_args[@]}"} -o json \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('service_principal_client_id') or '')")"
  if [ -z "$sp" ]; then echo "    !! $app: no service principal yet (deploy it first) — skipping"; continue; fi
  echo "==> $app -> service principal $sp"
  grant_on CATALOG "$catalog" "$sp" "$catalog_privs"
  if ! databricks grants update CATALOG "$catalog" ${profile_args[@]+"${profile_args[@]}"} \
         --json "{\"changes\":[{\"principal\":\"$sp\",\"remove\":$revoked_privs}]}" >/dev/null; then
    echo "!!! could not revoke the catalog-wide $revoked_privs from $sp on '$catalog'"; exit 1
  fi
  echo "    UC: revoked catalog-wide $revoked_privs on CATALOG $catalog"
done

echo "==> done. App service principals granted."
