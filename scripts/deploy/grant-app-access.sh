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

# The READERS: the Designer and the agent read as their own service principal only what the content
# resolver and the data-source registry read (`app_sql_runner`) — the tags and the tagged knowledge and
# data-source schemas. Every result read is the signed-in user's (SEC-OBO). Not checked on a workspace.
reader_privs='["USE_CATALOG","USE_SCHEMA","SELECT"]'

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

# RUN-CLIENT-PRIVATE-PROVISIONING — the catalog that also holds Designer users' private schemas (a
# single-catalog deployment). Catalog grants are inherited by every schema, so App identities get only
# the catalog privilege that reaches no data (USE_CATALOG). Everything else is granted on each schema that is not private, and the catalog-wide copy
# of it is revoked. A schema created later by someone else (a new data load or published result) needs
# the next deploy before an App can read it.
private_catalog="$(jqpy "v=d.get('variables') or {};print((v.get('designer_results_catalog') or {}).get('value') or '')")"
private_schema_format="cql-private-schema-v1"   # cql_databricks.result.private.SCHEMA_FORMAT
grant_beside_private() {  # <principal> <catalog> <privileges JSON>
  local sp="$1" cat="$2" privs="$3" split catalog_privs schema_privs schemas schema
  split="$(printf '%s' "$privs" | python3 -c "
import sys,json
privs=json.load(sys.stdin); top=[p for p in privs if p in ('USE_CATALOG','CREATE_SCHEMA')]
print(json.dumps(top)); print(json.dumps([p for p in privs if p not in top]))")"
  catalog_privs="$(printf '%s\n' "$split" | sed -n 1p)"; schema_privs="$(printf '%s\n' "$split" | sed -n 2p)"
  echo "    UC: catalog $cat also holds private results; schema-level grants, private schemas skipped"
  grant_on CATALOG "$cat" "$sp" "$catalog_privs"
  if ! databricks grants update CATALOG "$cat" ${profile_args[@]+"${profile_args[@]}"} \
         --json "{\"changes\":[{\"principal\":\"$sp\",\"remove\":$schema_privs}]}" >/dev/null; then
    echo "!!! could not revoke the catalog-wide $schema_privs from $sp on '$cat'"; exit 1
  fi
  echo "    UC: revoked catalog-wide $schema_privs on CATALOG $cat"
  schemas="$(databricks schemas list "$cat" ${profile_args[@]+"${profile_args[@]}"} -o json | python3 -c "
import sys,json
for s in json.load(sys.stdin):
    name=s['name']
    if name == 'information_schema' or name.startswith('cql_private_') or s.get('comment') == '$private_schema_format':
        continue
    print(name)")" || { echo "!!! could not list schemas in '$cat'"; exit 1; }
  for schema in $schemas; do grant_on SCHEMA "$cat.$schema" "$sp" "$schema_privs"; done
}

for app in "$designer_app" "$agent_app"; do
  privs="$reader_privs"
  sp="$(databricks apps get "$app" ${profile_args[@]+"${profile_args[@]}"} -o json \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('service_principal_client_id') or '')")"
  if [ -z "$sp" ]; then echo "    !! $app: no service principal yet (deploy it first) — skipping"; continue; fi
  echo "==> $app -> service principal $sp"

  # The readers read nothing in the results catalog as themselves (SEC-OBO), so they get the product one only.
  for cat in $catalog; do
    if [ "$cat" = "$private_catalog" ]; then
      grant_beside_private "$sp" "$cat" "$privs"
      continue
    fi
    grant_on CATALOG "$cat" "$sp" "$privs"
  done
done

echo "==> done. App service principals granted."
