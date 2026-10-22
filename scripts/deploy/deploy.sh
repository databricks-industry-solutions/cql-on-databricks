#!/usr/bin/env bash
# Deploy CQL on Databricks as ONE Databricks Asset Bundle (SVC-DEPLOY-BUNDLE) and START its Apps. The whole
# install/redeploy story in one command. Steps:
#   1. (Retired 2026-10-10: the Designer's Lakebase is removed, SPLIT-LAKEBASE SUPERSEDED.)
#   2. Stage each App's payload (SVC-PACKAGING) — with the deploy-resolved volume path baked in.
#   3. `databricks bundle deploy` — CREATES the runtime schema (SVC-CATALOG-LAYOUT) + volume, the Apps, perms.
#   4. Populate the runtime volume: the compiler App's JRE + 3 oversized jars (SVC-PACKAGING) and the
#      opaque-UDF catalog JAR.
#   5. Register the opaque `cql_*` catalog as UC Scala UDFs on serverless (SVC-RUNTIME-UDF-UC) via the SQL
#      Statement Execution API (needs `warehouse_id`; skipped with a notice if unset).
#   6.7 Publish the example notebooks to /Shared/CqlDatabricks/examples (SVC-EXAMPLE-NOTEBOOKS) — the shared,
#      every-user copy.
#   5.8 Create the Designer run log tables and grant them, as the deployer (APP-RUN-LOG).
#   7. `databricks bundle run` EVERY App the bundle declares (an App is not started by `deploy` alone).
#   8. Load the sample measures and patients from examples/ and bind the knowledge views, as the deployer
#      (CONTENT-SAMPLE-PRELOAD, DATA-BIND-AUTO).
# Before 2, the maintenance principal is created and granted (RUN-CLIENT-MAINTENANCE): it is the run-as of the
# cql-databricks-designer-maintenance Job the bundle deploys at 3.
# Re-run any time to redeploy + restart.
#
# Prereqs (one-time): `databricks auth login` / a CLI profile (U2M OAuth) — all auth is OAuth. Nothing
# else: the compiler, runtime, client and agent all build from THIS repository.
# Usage: scripts/deploy/deploy.sh [-t <target>] [-p <profile>]   (default target: default)
# Deploy config (workspace host, catalog, warehouse) lives in the gitignored databricks.local.yml
# (merged over databricks.yml via `include`). Auth resolves from ~/.databrickscfg via -p <profile>.
# .env is for LOCAL RUNTIME only — deploy scripts never source it.
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"

target="default"; profile_args=(); force_lock=""
while [[ $# -gt 0 ]]; do case "$1" in
  -t) target="$2"; shift 2;;
  -p) profile_args=(-p "$2"); shift 2;;
  --force-lock) force_lock="--force-lock"; shift;;
  # An ignored flag deploys somewhere the caller did not ask for (`--target prod` would deploy `default`).
  *) echo "!!! unknown argument: $1. Usage: scripts/deploy/deploy.sh [-t <target>] [-p <profile>] [--force-lock]" >&2; exit 2;;
esac; done
# No -p: the profile databricks.local.yml names (SVC-DEPLOY-ENV-LOCAL), so non-bundle CLI calls
# (databricks postgres, databricks current-user) authenticate against the bundle's workspace.
. "$here/scripts/lib/databricks-profile.sh"
if [ ${#profile_args[@]} -eq 0 ]; then _p="$(config_profile -t "$target")"; [ -z "$_p" ] || profile_args=(-p "$_p"); fi

# Ensure source_code_path dirs exist so `bundle validate` doesn't fail before staging runs. On a fresh clone
# they are empty, and every bundle call before [2/7] warns that their sync.include pattern matches no files;
# a placeholder keeps it quiet. Staging replaces each dir (rm -rf), so the placeholder is never deployed.
for d in "$here/compiler/server/build/app" "$here/designer/server/build/app" "$here/agent/build/app"; do
  mkdir -p "$d"
  [ -n "$(ls -A "$d")" ] || : > "$d/.unstaged"
done
venv_py="$here/.venv/bin/python"
[ -x "$venv_py" ] || { echo "!!! $venv_py is missing — run scripts/setup.sh (the provisioning steps use it)"; exit 1; }

# Validate ONCE and cache the resolved JSON, so app keys + variables come from the same authoritative view
# (stays correct as Apps/vars change). python3, not jq (no jq dep).
cfg="$(cd "$here" && databricks bundle validate -t "$target" ${profile_args[@]+"${profile_args[@]}"} -o json)"
jqpy() { printf '%s' "$cfg" | python3 -c "import sys,json;d=json.load(sys.stdin);$1"; }
keys="$(jqpy 'print("\n".join(((d.get("resources") or {}).get("apps") or {}).keys()))')"
# The resolved profile NAME on its own. Steps that authenticate through the Databricks SDK rather
# than the CLI (the sample provisioning step) need it as DATABRICKS_CONFIG_PROFILE — the SDK reads the
# environment, not our `-p` flag, and this script never sources .env.
profile_name="${profile_args[1]:-}"
# Resolve the volume path from the VARIABLES (uc_catalog/uc_schema/runtime_volume), which `bundle validate`
# fully substitutes. NOT from the volume resource: its schema_name is a chained resource reference
# (${resources.schemas.cql_databricks_runtime.name}, needed for create-ordering, SVC-CATALOG-LAYOUT) that
# validate leaves UNRESOLVED, which would bake a literal ${...} into the compiler app.yaml.
# SVC-CATALOG-LAYOUT — the product catalog has no default (a catalog name is workspace config), so an empty
# uc_catalog stops the deploy here, before it builds a volume path from it.
_uc_catalog="$(jqpy "v=d.get('variables') or {};print((v.get('uc_catalog') or {}).get('value') or '')")"
[ -n "$_uc_catalog" ] || {
  echo "!!! uc_catalog is empty. Set it in databricks.local.yml to the existing UC catalog the deploy creates"
  echo "!!! the runtime schema and volume in (SVC-CATALOG-LAYOUT)."; exit 1; }
volume="$(jqpy "v=d.get('variables') or {};f=lambda k:(v.get(k) or {}).get('value') or '';print('/Volumes/%s/%s/%s'%(f('uc_catalog'),f('uc_schema'),f('runtime_volume')))")"
echo "==> apps in bundle: $(echo $keys)"
echo "==> runtime volume=$volume"

# APP-PER-FEATURE — each feature App grants its users CAN_USE (databricks.yml `permissions`), because a user
# reached through another App is reached as themselves (APP-TO-APP-OBO). With no group the bundle drops the
# grant and only the deployer could use the Apps, so an empty group stops the deploy here.
users_group="$(jqpy "v=d.get('variables') or {};print((v.get('app_users_group') or {}).get('value') or '')")"
[ -n "$users_group" ] || {
  echo "!!! app_users_group is empty. Set it in databricks.local.yml to the account group whose members use"
  echo "!!! the Apps (they get CAN_USE on the Designer and agent Apps; APP-PER-FEATURE)."; exit 1; }
echo "==> app users group=$users_group"
# APP-RUN-LOG — the run log is read by an operator group, granted SELECT at step [5.8]; without one nobody but
# the deployer could read it, so an empty group stops the deploy here, like app_users_group.
readers_group="$(jqpy "v=d.get('variables') or {};print((v.get('run_log_readers_group') or {}).get('value') or '')")"
[ -n "$readers_group" ] || {
  echo "!!! run_log_readers_group is empty. Set it in databricks.local.yml to the account group that may read"
  echo "!!! the Designer run log (it gets SELECT on <designer_catalog>.designer.runs and run_steps; APP-RUN-LOG)."; exit 1; }
echo "==> run log readers group=$readers_group"
# CLIENT-DISCOVERY-SCOPE — the Apps and the knowledge-view bind discover only inside these scopes, and there is no
# default to fall back to, so an empty list stops the deploy here.
_catalog_scopes="$(jqpy "v=d.get('variables') or {};print((v.get('catalog_scopes') or {}).get('value') or '')")"
[ -n "$_catalog_scopes" ] || {
  echo "!!! catalog_scopes is empty. Set it in databricks.local.yml to the comma-separated catalogs or"
  echo "!!! catalog.schemas the Apps discover data sources, knowledge and terminology in (CLIENT-DISCOVERY-SCOPE)."; exit 1; }
echo "==> catalog scopes=$_catalog_scopes"

# RUN-CLIENT-MAINTENANCE — the private catalog is an administrator prerequisite; deployment only grants
# the maintenance principal its authority on it (RUN-CLIENT-PRIVATE-PROVISIONING). Its application ID is the
# cql-databricks-designer-maintenance Job's run-as, so it must exist before [3/7] deploys the Job.
_maintenance_principal="$(bash "$here/scripts/deploy/grant-maintenance-access.sh" -t "$target" ${profile_args[@]+"${profile_args[@]}"} | tail -1)"
[ -n "$_maintenance_principal" ] || { echo "!!! the maintenance principal setup printed no application ID"; exit 1; }
export BUNDLE_VAR_maintenance_principal="$_maintenance_principal"

# Export catalog + warehouse vars so stage-app.sh bakes them into each App's app.yaml
# (PIPE-DEPLOY-CATALOG-VARS). Values come from the resolved bundle variables (authoritative).
_designer_results_catalog="$(jqpy "v=d.get('variables') or {};print((v.get('designer_results_catalog') or {}).get('value') or '')")"
[ -n "$_designer_results_catalog" ] || {
  echo "!!! designer_results_catalog is empty. Set it in databricks.local.yml to the catalog holding the"
  echo "!!! Designer's per-user result schemas (RUN-CLIENT-PRIVATE-PROVISIONING). There is no default."; exit 1; }
_designer_catalog="$(jqpy "v=d.get('variables') or {};print((v.get('designer_catalog') or {}).get('value') or '')")"
_warehouse_id="$(jqpy "v=d.get('variables') or {};print((v.get('warehouse_id') or {}).get('value') or '')")"
# APP-ASSISTANT — the FM endpoint NAME. The App resolves it from the signed-in user (`SEC-OBO`), which
# provides the endpoint without needing an App `serving_endpoint` resource.
_assistant_endpoint="$(jqpy "v=d.get('variables') or {};print((v.get('assistant_endpoint') or {}).get('value') or '')")"
[ -n "$_assistant_endpoint" ] || {
  echo "!!! assistant_endpoint is empty. Set it in databricks.local.yml to the Model Serving chat endpoint"
  echo "!!! the assistant drawer calls (APP-ASSISTANT). There is no default."; exit 1; }
export BUNDLE_VAR_uc_catalog="${_uc_catalog}"
export BUNDLE_VAR_designer_results_catalog="${_designer_results_catalog}"
export BUNDLE_VAR_designer_catalog="${_designer_catalog}"
export BUNDLE_VAR_warehouse_id="${_warehouse_id}"
export BUNDLE_VAR_assistant_endpoint="${_assistant_endpoint}"
export BUNDLE_VAR_catalog_scopes="${_catalog_scopes}"

echo "==> [2/7] stage each App's payload (volume baked into app.yaml)"
# Export the resolved volume path so stage-compiler.sh substitutes it into the staged app.yaml.
export CQL_DATABRICKS_ARTIFACT_VOLUME="$volume"
# SVC-WHEEL-VERSION — computed ONCE, so the version stage-app.sh writes into requirements.txt is the version
# step [4/7] builds, even if the tree changes in between.
. "$here/scripts/lib/client-wheel-version.sh"
CQL_CLIENT_WHEEL_VERSION="$(client_wheel_version)"; export CQL_CLIENT_WHEEL_VERSION
# The cql-databricks-designer-maintenance Job installs this same wheel from the runtime volume.
export BUNDLE_VAR_client_wheel_version="$CQL_CLIENT_WHEEL_VERSION"
echo "==> client wheel version=$CQL_CLIENT_WHEEL_VERSION"
echo "$keys" | grep -qx "cql_databricks_compiler" && "$here/scripts/deploy/stage-compiler.sh"
# ONE staging call for the two Python Apps (Designer, agent; APP-PER-FEATURE), so the placeholder substitution
# runs once over every descriptor and cannot drift between them.
for k in cql_databricks_designer cql_databricks_agent; do
  echo "$keys" | grep -qx "$k" || { echo "!!! $k is not in the bundle; stage-app.sh stages both"; exit 1; }
done
"$here/scripts/deploy/stage-app.sh"
echo "==> [3/7] validate + deploy the bundle to target=$target (creates the schema + volume + apps)"
( cd "$here" && databricks bundle deploy -t "$target" ${profile_args[@]+"${profile_args[@]}"} ${force_lock} )

echo "==> [4/7] populate the runtime volume (JRE + jars)"
"$here/scripts/deploy/deploy-runtime-artifacts.sh" -t "$target" ${profile_args[@]+"${profile_args[@]}"}

echo "==> [5/7] register the cql_* catalog as UC Scala UDFs on serverless (SVC-RUNTIME-UDF-UC)"
"$here/scripts/deploy/register-uc-udfs.sh" -t "$target" ${profile_args[@]+"${profile_args[@]}"}

echo "==> [5.7/7] grant each App's service principal its access (DEPLOY-APP-GRANTS)"
# MUST run after [3/7] (the Apps — and so their service principals — must exist). Without this every
# deploy produced Apps that start and immediately crash: no UC USE_CATALOG.
"$here/scripts/deploy/grant-app-access.sh" -t "$target" ${profile_args[@]+"${profile_args[@]}"}

echo "==> [5.8] create the Designer run log tables and grant them, as the deployer (APP-RUN-LOG)"
# After [3/7], which creates the `designer` schema and the Designer App whose service principal gets MODIFY.
# FATAL on failure: a run log the App cannot write fails every write at ERROR, run after run.
[ -n "$_warehouse_id" ] || { echo "!!! warehouse_id is not set — the run log tables need it"; exit 1; }
_designer_app="$(jqpy "a=(d.get('resources') or {}).get('apps') or {};print((a.get('cql_databricks_designer') or {}).get('name') or '')")"
(
  if [ -n "$profile_name" ]; then export DATABRICKS_CONFIG_PROFILE="$profile_name"; fi
  "$venv_py" "$here/scripts/deploy/provision-run-log.py" --catalog "$_designer_catalog" --warehouse "$_warehouse_id" \
    --designer-app "$_designer_app" --readers-group "$readers_group"
)
# [5.9] is retired (SVC-COMPILER-M2M SUPERSEDED): a Job reaches the compiler App with its own identity
# through the token exchange (SVC-EXCHANGE-RETRY), so the deploy provisions no compiler caller.
# [5.5/7] is retired: the Designer's Lakebase is removed.
# [6/7] is retired (CARVE-CONTENT): there are no config rows to seed and no knowledge sync. The
# knowledge corpus is read from the tagged tables (KNOWLEDGE-LAKE-DIRECT); tag a schema by running
# the importer, or `python -m content_ingest tag` over a load that predates tagging.

echo "==> [6.7/7] publish the example notebooks to /Shared (SVC-EXAMPLE-NOTEBOOKS)"
# Runs after [3/7], which creates the runtime Volume the notebooks' %pip cells name. Overwrites in place, so
# each deploy restores the published copy to what the repo says it is.
"$here/scripts/deploy/publish-examples.sh" -t "$target" ${profile_args[@]+"${profile_args[@]}"}

echo "==> [7/7] run (create + start) every App the bundle declares"
echo "$keys" | while IFS= read -r app; do
  [ -n "$app" ] || continue
  echo "    -> databricks bundle run $app"
  ( cd "$here" && databricks bundle run "$app" -t "$target" ${profile_args[@]+"${profile_args[@]}"} )
done

echo "==> [8] load the sample measures and patients + bind the knowledge views, as the deployer (CONTENT-SAMPLE-PRELOAD)"
# After [7/7], like before; the bind reads the tagged knowledge this step loads. FATAL on failure, like every step
# that provisions what a run reads.
_runtime_schema="$(jqpy "v=d.get('variables') or {};print((v.get('uc_schema') or {}).get('value') or '')")"
[ -n "$_warehouse_id" ] || { echo "!!! warehouse_id is not set — the knowledge views need it"; exit 1; }
(
  if [ -n "$profile_name" ]; then export DATABRICKS_CONFIG_PROFILE="$profile_name"; fi
  export CQL_DATABRICKS_CATALOG_SCOPES="$_catalog_scopes"
  "$venv_py" "$here/scripts/deploy/provision-samples.py" --catalog "$_uc_catalog" --runtime-schema "$_runtime_schema" \
    --warehouse "$_warehouse_id"
)

echo "==> done. Apps deployed + started: $(echo $keys). Runtime volume populated at $volume."
