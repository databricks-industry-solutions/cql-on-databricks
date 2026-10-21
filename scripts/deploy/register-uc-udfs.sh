#!/usr/bin/env bash
# SVC-RUNTIME-UDF-UC + SVC-CATALOG-LAYOUT: register the engine's opaque `cql_*` catalog as PERSISTENT Unity
# Catalog Scala UDFs in <uc_catalog>.<uc_schema> (e.g. <uc_catalog>.runtime). Runs the
# `CREATE OR REPLACE FUNCTION … LANGUAGE SCALA` DDL — derived at deploy time from the engine manifest
# (EXEC-UC-MANIFEST) by the runtime jar's `printUcDdl` task, so it can never drift from the catalog — via the
# SQL Statement Execution API against a SERVERLESS SQL warehouse. Once created, the functions resolve on
# EVERY compute type incl. serverless, lifting the "serverless excluded" limit of the classic %scala path
# (SVC-RUNTIME-UDF). Idempotent (CREATE OR REPLACE).
#
# Called by scripts/deploy/deploy.sh AFTER deploy-runtime-artifacts.sh (the fat jar must already be in the volume —
# java_dependencies points at it) and after `bundle deploy` (the runtime schema must exist). Safe to run
# standalone to re-register. Needs `warehouse_id` set in the bundle (var or target); empty => skip.
# Usage: scripts/deploy/register-uc-udfs.sh [-t <target>] [-p <profile>]   (default target: default)
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
target="default"; profile_args=()
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
v() { jqpy "print((d.get('variables') or {}).get('$1',{}).get('value') or '')"; }
catalog="$(v uc_catalog)"; schema="$(v uc_schema)"; warehouse="$(v warehouse_id)"
[ -n "$catalog" ] || { echo "!!! uc_catalog is empty. Set it in databricks.local.yml (SVC-CATALOG-LAYOUT)."; exit 1; }
# Volume path from the VARIABLES (the volume resource's schema_name is a chained ref validate leaves as ${...}).
volume="/Volumes/$catalog/$schema/$(v runtime_volume)"

if [ -z "$warehouse" ]; then
  echo "==> warehouse_id unset for target=$target — SKIPPING UC UDF registration (SVC-RUNTIME-UDF-UC)."
  echo "    Set it (databricks.yml var or a target override) to register cql_* on serverless; the classic %scala path still works."
  exit 0
fi

# The uploaded fat jar's basename (deploy-runtime-artifacts.sh placed it at <volume>/udf/<base>).
jar="$(ls -t "$here"/compiler/runtime/udf/build/libs/cql-databricks-runtime-udf-*.jar 2>/dev/null | head -1)"
[ -n "$jar" ] && [ -f "$jar" ] || { echo "!!! runtime jar not found under $here/compiler/runtime/udf/build/libs — run scripts/deploy/deploy-runtime-artifacts.sh first"; exit 1; }
jarpath="$volume/udf/$(basename "$jar")"
echo "==> register cql_* UC Scala UDFs in $catalog.$schema (jar=$jarpath, warehouse=$warehouse)"

# Generate the DDL (JSON array of statement strings) from the engine manifest via the runtime jar.
ddl_json="$("$here/gradlew" -q -p "$here" :compiler:runtime:uc:printUcDdl --args="--catalog $catalog --schema $schema --jar $jarpath")"

# SVC-RUNTIME-UDF-IDENTITY (PIPE-BARRIER-HASH determinant 3): record WHICH build of the cql_* functions
# is registered, so CQL on Databricks can recompute materializations whose values came from a different one.
#
# WHY THIS IS NEEDED AT ALL. Re-registering these functions changes results with NO change to the emitted
# SQL (`cql_age_at(...)` reads identically before and after its semantics change) and no change to the
# compiler. Neither the barrier hash nor the compiler identity can see it — this is the only place the
# event is observable, so this is the only place that can record it.
#
# WHY THE JAR'S CONTENT HASH. The registered DDL is NOT a usable identity: the jar basename is constant
# across engine builds (runtime-0.0.0-phase0.jar), so the volume path and the CREATE FUNCTION text
# are byte-identical build to build. The jar's sha256 changes exactly when the functions could behave
# differently, which is the property we need.
udf_sha="$(shasum -a 256 "$jar" | cut -c1-12)"
engine_sha="$(git -C "$here" rev-parse --short HEAD 2>/dev/null || echo unknown)"
udf_identity="udf-${udf_sha}+engine-${engine_sha}"
echo "==> udf identity: $udf_identity"

# Appended to the SAME statement batch, so the marker cannot land without the functions landing. A
# table rather than a comment/tag because the BFF reads it with the warehouse it already has
# (`_execute_sql_warehouse`), and because it is governed alongside the functions it describes.
marker_ddl="$(python3 -c '
import json, sys
catalog, schema, identity, jarpath = sys.argv[1:5]
print(json.dumps([
  f"CREATE TABLE IF NOT EXISTS `{catalog}`.`{schema}`.udf_registration "
  f"(identity STRING, jar STRING, registered_at TIMESTAMP)",
  f"TRUNCATE TABLE `{catalog}`.`{schema}`.udf_registration",
  f"INSERT INTO `{catalog}`.`{schema}`.udf_registration VALUES "
  f"(\"{identity}\", \"{jarpath}\", current_timestamp())",
]))' "$catalog" "$schema" "$udf_identity" "$jarpath")"
ddl_json="$(python3 -c '
import json, sys
print(json.dumps(json.loads(sys.argv[1]) + json.loads(sys.argv[2])))' "$ddl_json" "$marker_ddl")"

# Submit each statement synchronously; fail loudly on any non-SUCCEEDED terminal state. The DDL is passed as
# argv (NOT stdin): `python3 -` already consumes stdin for the program (this heredoc), so the JSON must ride
# on an argument.
python3 - "$ddl_json" "$warehouse" ${profile_args[@]+"${profile_args[@]}"} <<'PY'
import json, subprocess, sys
stmts = json.loads(sys.argv[1])
warehouse, profile_args = sys.argv[2], sys.argv[3:]
for i, stmt in enumerate(stmts, 1):
    name = stmt.split("`")[5] if stmt.count("`") >= 6 else stmt[:40]
    body = json.dumps({"warehouse_id": warehouse, "statement": stmt, "wait_timeout": "50s", "on_wait_timeout": "CANCEL"})
    out = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements", *profile_args, "--json", body],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"    !! [{i}/{len(stmts)}] {name}: API call failed:\n{out.stderr}")
    state = (json.loads(out.stdout).get("status") or {}).get("state")
    if state != "SUCCEEDED":
        sys.exit(f"    !! [{i}/{len(stmts)}] {name}: state={state}\n{out.stdout}")
    print(f"    [{i}/{len(stmts)}] {name}: SUCCEEDED")
PY
echo "==> done. cql_* registered as persistent UC Scala UDFs in $catalog.$schema (resolve on all compute incl. serverless)."
