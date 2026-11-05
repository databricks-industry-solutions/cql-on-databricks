#!/usr/bin/env bash
# Deploy CQL on Databricks as ONE Databricks Asset Bundle (SVC-DEPLOY-BUNDLE) and START its Apps. The whole
# install/redeploy story in one command. Steps:
#   2. Stage each App's payload (SVC-PACKAGING) — with the deploy-resolved volume path baked in.
#   3. `databricks bundle deploy` — CREATES the runtime schema (SVC-CATALOG-LAYOUT) + volume, the Apps, perms.
#   4. Populate the runtime volume: the compiler App's JRE + 3 oversized jars (SVC-PACKAGING) and the
#      opaque-UDF catalog JAR.
#   4.5 Publish the "CQL on Databricks" base environment over the client wheel just uploaded, and make it the
#      workspace default when default_base_environment is true (DEPLOY-BASE-ENVIRONMENT).
#   5. Register the opaque `cql_*` catalog as UC Scala UDFs on serverless (SVC-RUNTIME-UDF-UC) in one SQL
#      session (needs `warehouse_id`; skipped with a notice if unset).
#   6.7 Publish the example notebooks to /Shared/CqlDatabricks/examples (SVC-EXAMPLE-NOTEBOOKS) — the shared,
#      every-user copy.
#   5.8 Create the Designer run log tables and grant them, as the deployer (APP-RUN-LOG).
#   7. `databricks bundle run` EVERY App the bundle declares (an App is not started by `deploy` alone).
#   7.5 Publish every skill, from one definition, as a Unity Catalog skill and to the workspace skills folder
#      (DEPLOY-AGENT-SKILLS).
#   7.6 Register the agent App under Unity Gateway, per user, so Genie One can call it (DEPLOY-AGENT-REGISTRATION).
#   8. Build the default environment in uc_catalog, as the deployer: its schemas, the example measures and patients
#      from examples/ in its stores, its views, its tags and its grants (DEPLOY-DEFAULT-ENVIRONMENT).
# Before 2, the workspace settings set to true in databricks.local.yml are turned on (DEPLOY-WORKSPACE-SETTINGS), and
# the maintenance principal is created and granted (RUN-CLIENT-MAINTENANCE): it is the run-as of the
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
for d in "$here/compiler/server/build/app" "$here/designer/server/build/app" "$here/agent/server/build/app"; do
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
# than the CLI (the example provisioning step) need it as DATABRICKS_CONFIG_PROFILE — the SDK reads the
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
# DEPLOY-AGENT-SKILLS — the workspace group that reads the workspace skills folder, a workspace object: Unity Catalog's
# account groups and workspace groups are not the same, so app_users_group (an account group) cannot serve.
workspace_users_group="$(jqpy "v=d.get('variables') or {};print((v.get('workspace_users_group') or {}).get('value') or '')")"
[ -n "$workspace_users_group" ] || {
  echo "!!! workspace_users_group is empty. Set it in databricks.local.yml to the workspace group that may read the"
  echo "!!! skills folder Genie Code loads (DEPLOY-AGENT-SKILLS)."; exit 1; }
echo "==> workspace users group=$workspace_users_group"
# APP-RUN-LOG — the run log is read by an operator group, granted SELECT at step [5.8]; without one nobody but
# the deployer could read it, so an empty group stops the deploy here, like app_users_group.
readers_group="$(jqpy "v=d.get('variables') or {};print((v.get('run_log_readers_group') or {}).get('value') or '')")"
[ -n "$readers_group" ] || {
  echo "!!! run_log_readers_group is empty. Set it in databricks.local.yml to the account group that may read"
  echo "!!! the Designer run log (it gets SELECT on the <designer_catalog>.designer schema; APP-RUN-LOG)."; exit 1; }
echo "==> run log readers group=$readers_group"
# DEPLOY-AGENT-SKILLS — the schema the agent's skills are published in as UC skills. No default, so an empty one
# stops the deploy here.
_agent_skills_schema="$(jqpy "v=d.get('variables') or {};print((v.get('agent_skills_schema') or {}).get('value') or '')")"
[ -n "$_agent_skills_schema" ] || {
  echo "!!! agent_skills_schema is empty. Set it in databricks.local.yml to the catalog.schema the agent's skills"
  echo "!!! are published in as UC skills (DEPLOY-AGENT-SKILLS)."; exit 1; }
echo "==> agent skills schema=$_agent_skills_schema"
# DEPLOY-AGENT-SKILLS, DEPLOY-AGENT-REGISTRATION — the ACCOUNT group granted the agent's skills and EXECUTE on its MCP
# Service. Unity Catalog grants take account principals only, so app_users_group (an App permission) cannot serve.
_agent_users_group="$(jqpy "v=d.get('variables') or {};print((v.get('agent_users_group') or {}).get('value') or '')")"
[ -n "$_agent_users_group" ] || {
  echo "!!! agent_users_group is empty. Set it in databricks.local.yml to the account group that may use the agent"
  echo "!!! from Genie One (DEPLOY-AGENT-SKILLS, DEPLOY-AGENT-REGISTRATION)."; exit 1; }
echo "==> agent users group=$_agent_users_group"
# DEPLOY-AGENT-REGISTRATION — an account admin's account-level CLI profile, OPTIONAL. Step [7.6] needs it only to
# create or repoint the agent's connection, which needs a new account OAuth client. Empty: [7.6] leaves a missing
# or stale connection alone and says Genie One is not connected. Set: checked here, with one read, so a profile
# that is not an account admin's stops the deploy now rather than after the Apps are built.
_agent_account_profile="$(jqpy "v=d.get('variables') or {};print((v.get('agent_account_profile') or {}).get('value') or '')")"
if [ -n "$_agent_account_profile" ]; then
  databricks account custom-app-integration list --limit 1 -p "$_agent_account_profile" >/dev/null || {
    echo "!!! the account profile $_agent_account_profile cannot list the account's OAuth clients. It must be an"
    echo "!!! account-level profile signed in by an account admin (docs/deploy.md, The agent in Genie One)."; exit 1; }
  echo "==> agent account profile=$_agent_account_profile"
else
  echo "==> no agent_account_profile: the deploy connects Genie One only if its connection is already in place"
fi
# RUN-CLIENT-MAINTENANCE — the maintenance principal is the cql-databricks-designer-maintenance Job's run-as, so it
# must exist before [3/7] deploys the Job. Its MANAGE on the default environment's draft_results is granted at [8],
# after [3/7] makes the schema (DEPLOY-DEFAULT-ENVIRONMENT).
_maintenance_principal="$(bash "$here/scripts/deploy/grant-maintenance-access.sh" -t "$target" ${profile_args[@]+"${profile_args[@]}"} | tail -1)"
[ -n "$_maintenance_principal" ] || { echo "!!! the maintenance principal setup printed no application ID"; exit 1; }
export BUNDLE_VAR_maintenance_principal="$_maintenance_principal"

# Export catalog + warehouse vars so stage-app.sh bakes them into each App's app.yaml.
# Values come from the resolved bundle variables (authoritative).
_designer_catalog="$(jqpy "v=d.get('variables') or {};print((v.get('designer_catalog') or {}).get('value') or '')")"
_warehouse_id="$(jqpy "v=d.get('variables') or {};print((v.get('warehouse_id') or {}).get('value') or '')")"
# APP-ASSISTANT — the FM endpoint NAME. The App resolves it from the signed-in user (`SEC-OBO`), which
# provides the endpoint without needing an App `serving_endpoint` resource.
_assistant_endpoint="$(jqpy "v=d.get('variables') or {};print((v.get('assistant_endpoint') or {}).get('value') or '')")"
[ -n "$_assistant_endpoint" ] || {
  echo "!!! assistant_endpoint is empty. Set it in databricks.local.yml to the Model Serving chat endpoint"
  echo "!!! the assistant drawer calls (APP-ASSISTANT). There is no default."; exit 1; }
export BUNDLE_VAR_uc_catalog="${_uc_catalog}"
export BUNDLE_VAR_designer_catalog="${_designer_catalog}"
export BUNDLE_VAR_warehouse_id="${_warehouse_id}"
export BUNDLE_VAR_assistant_endpoint="${_assistant_endpoint}"
# DOC-OPEN-SAVE — the canonical URL base of libraries created in the Designer. databricks.yml has a default, so
# empty here means it was set empty on purpose.
_canonical_base="$(jqpy "v=d.get('variables') or {};print((v.get('canonical_base') or {}).get('value') or '')")"
[ -n "$_canonical_base" ] || {
  echo "!!! canonical_base is empty. Set it in databricks.local.yml to the base URL of libraries created in"
  echo "!!! the Designer (DOC-OPEN-SAVE), or remove it to use the default."; exit 1; }
export BUNDLE_VAR_canonical_base="${_canonical_base}"

echo "==> [1.5] workspace settings (DEPLOY-WORKSPACE-SETTINGS)"
# Each is workspace-wide, so it is the implementer's to choose: a setting is turned on only when its variable is
# true, as a workspace admin, and false leaves it as it is, never turning it off. Before [2/7], because the Apps
# must start with user authorization on, and [7.6] needs Unity Gateway. FATAL on failure.
_workspace_setting() {  # <variable> <setting> <what it is>
  local value
  value="$(jqpy "v=d.get('variables') or {};print(str((v.get('$1') or {}).get('value') or 'false').lower())")"
  case "$value" in
    true)
      databricks workspace-settings-v2 patch-public-workspace-setting "$2" \
        --json "{\"name\":\"$2\",\"boolean_val\":{\"value\":true}}" ${profile_args[@]+"${profile_args[@]}"} >/dev/null
      echo "--> $3 ($2): on";;
    false) echo "--> $1 is false: $3 ($2) left as it is";;
    *) echo "!!! $1 must be true or false; got $value"; exit 1;;
  esac
}
_workspace_setting enable_databricks_apps custom_apps_preview "Databricks Apps"
_workspace_setting enable_apps_user_authorization enable_obo_user_apps "user authorization for Apps (SEC-OBO)"
_workspace_setting enable_unity_gateway ai_gateway_ga_ws "Unity Gateway (DEPLOY-AGENT-REGISTRATION)"
_workspace_setting enable_genie_uc_skills genie_uc_skills "Unity Gateway skills in Genie (DEPLOY-AGENT-SKILLS)"

echo "==> [1.6] the project's admins are workspace admins (DEPLOY-PROJECT-ADMINS)"
# Every deploy, as the deployer (a workspace admin): each of admins is added to the workspace and its admins group.
(
  if [ -n "$profile_name" ]; then export DATABRICKS_CONFIG_PROFILE="$profile_name"; fi
  "$venv_py" "$here/scripts/deploy/project_admins.py" \
    --admins "$(jqpy "import json;v=d.get('variables') or {};print(json.dumps((v.get('admins') or {}).get('value') or []))")"
)

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

echo "==> [4.5] publish the base environment that carries the client wheel (DEPLOY-BASE-ENVIRONMENT)"
# After [4/7], which uploads the wheel its YAML names. FATAL on failure: a stale base environment installs the client
# from before this deploy, with nothing failing to say so.
bash "$here/scripts/deploy/base-environment.sh" -t "$target" ${profile_args[@]+"${profile_args[@]}"}

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

echo "==> [6.7/7] publish the example notebooks to /Shared (SVC-EXAMPLE-NOTEBOOKS)"
# Runs after [4.5], which builds the base environment the notebooks name. Overwrites in place, so
# each deploy restores the published copy to what the repo says it is.
"$here/scripts/deploy/publish-examples.sh" -t "$target" ${profile_args[@]+"${profile_args[@]}"}

echo "==> [7/7] run (create + start) every App the bundle declares"
echo "$keys" | while IFS= read -r app; do
  [ -n "$app" ] || continue
  echo "    -> databricks bundle run $app"
  ( cd "$here" && databricks bundle run "$app" -t "$target" ${profile_args[@]+"${profile_args[@]}"} )
done

echo "==> [7.5] publish every skill to Unity Catalog and the workspace skills folder (DEPLOY-AGENT-SKILLS)"
# FATAL on failure: Genie One would call the agent's tools without the workflows that govern them, and Genie Code
# would have no import skill, with nothing failing to say so. After [4/7], which uploads the wheels the import skill
# installs. Genie One reaches the agent App itself, as the user (SEC-OBO); this step registers no connection and
# stores no credential.
"$here/scripts/deploy/publish-skills.sh" -t "$target" ${profile_args[@]+"${profile_args[@]}"}

echo "==> [7.6] register the agent under Unity Gateway, per user (DEPLOY-AGENT-REGISTRATION)"
# After [7/7] and [7.5]: the connection points at the agent App's URL, and the MCP Service sits in the skills
# schema. FATAL on failure: an unregistered agent is invisible to Genie One with nothing failing to say so.
_agent_app="$(jqpy "a=(d.get('resources') or {}).get('apps') or {};print((a.get('cql_databricks_agent') or {}).get('name') or '')")"
(
  if [ -n "$profile_name" ]; then export DATABRICKS_CONFIG_PROFILE="$profile_name"; fi
  "$venv_py" "$here/scripts/deploy/register_agent_mcp.py" --app "$_agent_app" --schema "$_agent_skills_schema" \
    --users-group "$_agent_users_group" --account-profile "$_agent_account_profile"
)

echo "==> [7.9] govern the environment tags (DEPLOY-ENVIRONMENT-TAG-POLICIES)"
# Before [8], which tags the default environment: once a key is governed, only its assigners can apply it.
_govern="$(jqpy "v=d.get('variables') or {};print(str((v.get('govern_environment_tags') or {}).get('value', True)).lower())")"
case "$_govern" in
  true) bash "$here/scripts/deploy/environment-tag-policies.sh" -t "$target" ${profile_args[@]+"${profile_args[@]}"};;
  false)
    echo "!!! WARNING: govern_environment_tags is false, so the environment tags are NOT governed: anyone who can tag a"
    echo "!!! catalog can make it an environment or the default environment (DEPLOY-ENVIRONMENT-TAG-POLICIES).";;
  *) echo "!!! govern_environment_tags must be true or false, not $_govern"; exit 1;;
esac

echo "==> [8] fill the default environment in $_uc_catalog, as the deployer (DEPLOY-DEFAULT-ENVIRONMENT)"
# After [3/7], whose bundle deploy made the environment's schemas and their grants. FATAL on failure, like every step
# that provisions what a run reads.
(
  if [ -n "$profile_name" ]; then export DATABRICKS_CONFIG_PROFILE="$profile_name"; fi
  "$venv_py" "$here/scripts/deploy/provision-examples.py" --catalog "$_uc_catalog" --users-group "$users_group" \
    --maintenance-principal "$_maintenance_principal"
)

echo "==> done. Apps deployed + started: $(echo $keys). Runtime volume populated at $volume."
