#!/usr/bin/env bash
# Populate the runtime UC Volume (SVC-RUNTIME-UDF, SVC-PACKAGING): the JVM Apps' JRE + oversized jars, the
# runtime function jars and the client wheels. Called by scripts/deploy/deploy.sh AFTER `bundle deploy` (the volume
# exists by then) and BEFORE the Apps are started, so the JVM Apps find their JRE + jars in the volume on
# first boot. Safe to run standalone to refresh the artifacts. The volume comes from the VALIDATED bundle config.
#
# It does NOT register the `cql_*` functions: scripts/deploy/register-uc-udfs.sh does, next (SVC-RUNTIME-UDF-UC).
#
# Prereqs: `databricks auth login` / a profile. Everything else builds from this repository.
# Usage: scripts/deploy/deploy-runtime-artifacts.sh [-t <target>] [-p <profile>]   (default target: default)
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
svc="$here/compiler/server"
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
# Resolve the volume path from the VARIABLES (uc_catalog/uc_schema/runtime_volume), which `bundle validate`
# fully substitutes. NOT from the volume resource: its schema_name is a chained resource reference
# (needed for create-ordering, SVC-CATALOG-LAYOUT) that validate leaves as a literal ${...}.
volume="$(jqpy "v=d.get('variables') or {};f=lambda k:(v.get(k) or {}).get('value') or '';print('/Volumes/%s/%s/%s'%(f('uc_catalog'),f('uc_schema'),f('runtime_volume')))")"
case "$volume" in *//*|*/) echo "!!! could not resolve the runtime Volume path from the bundle (got $volume)"; exit 1;; esac
echo "==> volume=$volume"

cache="$svc/build/runtime-artifacts"   # under build/ (gitignored); reused across deploys to avoid re-download
mkdir -p "$cache"

# up <local-file> <volume-relpath> -> 0 on success. NOT called in a `||`/`if` chain (that would disable
# `set -e` inside the whole function); callers check the return value explicitly instead.
up() {
  local rel="$2"; local dir; dir="$(dirname "$rel")"
  if [ "$dir" != "." ]; then databricks fs mkdir "dbfs:$volume/$dir" ${profile_args[@]+"${profile_args[@]}"} || return 1; fi
  databricks fs cp "$1" "dbfs:$volume/$rel" --overwrite ${profile_args[@]+"${profile_args[@]}"} || return 1
  echo "    uploaded $rel"
}

echo "==> [a] JVM App runtime artifacts (JRE + oversized jars) -> volume (SVC-PACKAGING)"
# The manifest (url<TAB>volume-relpath) comes from bootstrap.py, so the upload matches what it reads. The
# Maven jars are copied from the installDist output (SVC-STAGE-LOCAL-JARS), which Gradle resolved through
# `mavenRepoUrl` when one is set, so the deploy host needs no Maven access of its own. The JRE is fetched
# (curl honours HTTPS_PROXY; CQL_DATABRICKS_JRE_URL repoints it). Any failure stops the deploy: an App
# started without its artifacts in the volume fails at boot wherever public egress is closed.
lib="$svc/build/install/compiler-app/lib"
while IFS=$'\t' read -r url rel; do
  [ -n "$url" ] || continue
  case "$rel" in
    maven/*)
      local_f="$lib/$(basename "$rel")"
      [ -f "$local_f" ] || { echo "!!! $(basename "$rel") is not in $lib. Run scripts/deploy/stage-compiler.sh first; if it is still missing, bootstrap.py MAVEN_JARS and the Gradle dependencies disagree on a version"; exit 1; } ;;
    *)
      local_f="$cache/$(basename "$rel")"
      if [ ! -f "$local_f" ]; then
        echo "    fetching $(basename "$rel")"
        curl -fSL --retry 3 -o "$local_f" "$url" || { rm -f "$local_f"; echo "!!! fetch failed: $url. Behind a proxy, set HTTPS_PROXY; to use a mirror, set CQL_DATABRICKS_JRE_URL (docs/deploy.md)"; exit 1; }
      fi ;;
  esac
  up "$local_f" "$rel" || { echo "!!! upload failed: $rel -> $volume"; exit 1; }
done < <(python3 "$svc/deploy/bootstrap.py" --manifest)

echo "==> [b] build the JARs: engine opaque-UDF catalog + the CQL on Databricks session entry point"
# TWO jars land on the runtime cluster (SVC-RUNTIME-UDF):
#   1. runtime (engine) — the opaque `cql_*` UDF classes the emitted SQL calls by name.
#   2. compiler/runtime/uc (this repo) — the STABLE `com.databricks.cql.platform.CqlDatabricks.init(spark)` entry point a
#      notebook `%scala` cell calls to register the catalog on shared clusters; it delegates into #1.
"$here/gradlew" -p "$here" :compiler:runtime:udf:jar
jar="$(ls -t "$here"/compiler/runtime/udf/build/libs/cql-databricks-runtime-udf-*.jar 2>/dev/null | head -1)"
[ -n "$jar" ] && [ -f "$jar" ] || { echo "!!! built UDF JAR not found under compiler/runtime/udf/build/libs"; exit 1; }
base="$(basename "$jar")"
echo "    -> $jar"

"$here/gradlew" -p "$here" :compiler:runtime:uc:jar
rtjar="$(ls -t "$here"/compiler/runtime/uc/build/libs/cql-databricks-runtime-uc-*.jar 2>/dev/null | head -1)"
[ -n "$rtjar" ] && [ -f "$rtjar" ] || { echo "!!! built compiler/runtime/uc JAR not found under compiler/runtime/uc/build/libs"; exit 1; }
rtbase="$(basename "$rtjar")"
echo "    -> $rtjar"

echo "==> [c] upload both JARs to the volume (java_dependencies target for the UC UDFs)"
up "$jar" "udf/$base"
up "$rtjar" "udf/$rtbase"

echo "==> [d] build + upload the client wheels -> volume wheels/ (SVC-PACKAGING, SVC-EXAMPLE-NOTEBOOKS)"
# TWO consumers, both blocking: the base environment names the client wheel (DEPLOY-BASE-ENVIRONMENT), and the CQL on Databricks
# App's requirements.txt installs cql_databricks-<version>-py3-none-any.whl from here (the package is not
# on PyPI, SVC-PACKAGING). The skill that imports measures installs the client wheel too (CONTENT-IMPORT-SKILL).
# Build with a Python that HAS setuptools (--no-build-isolation avoids PyPI, blocked
# on locked-down hosts); --no-deps because both consumers already have requests/pyspark.
# FATAL, not best-effort: this step is now on the App's startup path — a missing wheel means the App's pip
# install fails and it never binds its port, so a silent skip would just move the failure somewhere less
# legible (the earlier non-fatal skip predates the App consumer).
whldir="$svc/build/wheels"; rm -rf "$whldir"; mkdir -p "$whldir"
# The repo's own venv (scripts/setup.sh) is the FIRST candidate, not an afterthought: a stock macOS
# `python3` has no setuptools, which is exactly why this step had been silently skipping — the volume's
# wheels/ folder did not exist on the reference workspace at all.
pybuild=""; for c in "$here/.venv/bin/python" python3.11 python3.12 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import setuptools' >/dev/null 2>&1; then pybuild="$c"; break; fi
done
[ -n "$pybuild" ] || { echo "!!! no Python with setuptools found — cannot build the client wheels, which the CQL on Databricks App installs from the volume (SVC-PACKAGING). Run scripts/setup.sh to create .venv."; exit 1; }
echo "    building with $pybuild"
# SVC-WHEEL-VERSION — the client wheel is built under its content-stamped version (client-wheel-version.sh),
# the one stage-app.sh wrote into each App's requirements.txt. An App keeps its venv across deployments and
# pip skips a version already installed, so changed contents must carry a new version. The stamp goes into
# a COPY of the client, so the checked-in pyproject.toml keeps the plain release.
. "$here/scripts/lib/client-wheel-version.sh"
client_ver="$(client_wheel_version)"
[ -n "$client_ver" ] || { echo "!!! could not compute the client wheel version (SVC-WHEEL-VERSION)"; exit 1; }
client_copy="$svc/build/client-src"; rm -rf "$client_copy"; mkdir -p "$client_copy"
cp -R "$here/compiler/client/pyproject.toml" "$here/compiler/client/README.md" "$here/compiler/client/src" "$client_copy/"
find "$client_copy" \( -name '__pycache__' -o -name '*.egg-info' \) -type d -prune -exec rm -rf {} +
CQL_CLIENT_WHEEL_VERSION="$client_ver" python3 -c 'import os,re,sys;p=sys.argv[1];s=open(p).read()
s,n=re.subn(r"^version\s*=\s*\"[^\"]+\"","version = \"%s\""%os.environ["CQL_CLIENT_WHEEL_VERSION"],s,count=1,flags=re.M)
sys.exit("!!! no version line in "+p) if n!=1 else open(p,"w").write(s)' "$client_copy/pyproject.toml"
echo "    client wheel version $client_ver"
# DEPLOY-SHIPPED-SOURCES — the shipped sources are the deploy's own and are not built here: the import skill
# imports measures with the client wheel alone (CONTENT-IMPORT-MEASURES).
# BUILD-TAXONOMY — the agent's three components each build a wheel.
agent_components=("$here/agent/service" "$here/agent/skills" "$here/agent/server")
"$pybuild" -m pip wheel --no-build-isolation --no-deps -w "$whldir" \
  "$client_copy" "${agent_components[@]}" >/dev/null \
  || { echo "!!! client wheel build failed (pip wheel over $client_copy + ${agent_components[*]})"; exit 1; }
# The expected filenames come from the pyprojects that BUILD the wheels, never from a version literal in
# this script; a hard-coded 0.0.0 here failed the deploy the day the engine bumped.
for pkg_dir in "$client_copy" "${agent_components[@]}"; do
  # Read the DISTRIBUTION NAME from pyproject.toml, not from the directory basename. They differ on purpose —
  # client/ builds the cql_databricks wheel and agent/server/ the cql_databricks_agent_server one — so a
  # basename-derived filename would look for a wheel that pip never produced.
  pkg="$(sed -n 's/^name *= *"\(.*\)"/\1/p' "$pkg_dir/pyproject.toml" | head -1 | tr - _)"
  [ -n "$pkg" ] || { echo "!!! no name in $pkg_dir/pyproject.toml"; exit 1; }
  ver="$(sed -n 's/^version *= *"\(.*\)"/\1/p' "$pkg_dir/pyproject.toml" | head -1)"
  [ -n "$ver" ] || { echo "!!! no version in $pkg_dir/pyproject.toml (SVC-WHEEL-VERSION)"; exit 1; }
  [ -f "$whldir/${pkg}-${ver}-py3-none-any.whl" ] \
    || { echo "!!! ${pkg}-${ver}-py3-none-any.whl not produced — pip wheel built $(ls "$whldir" | tr '\n' ' ')"; exit 1; }
done
# PRUNE BEFORE UPLOAD (SVC-WHEEL-VERSION, SVC-EXAMPLE-NOTEBOOKS). The upload is `cp --overwrite`, which
# replaces a wheel of the SAME name and leaves every other one behind — so the directory accumulates one
# file per version ever deployed. That is not merely untidy: the documented `%pip` install uses a GLOB
# (`cql_databricks-*.whl`), so it never carries a version a reader would have to keep in step. The moment a
# second version appears the glob expands to two files and pip fails with "Double requirement given" — which
# is exactly what the 0.0.0 -> 0.1.0 bump did.
#
# So the directory must hold EXACTLY the current build. Clearing it is safe because it is derived: the
# pyprojects are the source of truth, and the App's requirements.txt names the version stage-app.sh read
# from the same file in the same deploy. Not fatal on failure — an empty listing is a fresh volume.
echo "    pruning stale wheels (the notebooks install by glob, so one build per package)"
for old in $(databricks fs ls "dbfs:$volume/wheels" ${profile_args[@]+"${profile_args[@]}"} 2>/dev/null | grep '\.whl$' || true); do
  if [ ! -f "$whldir/$old" ]; then
    echo "    removing wheels/$old"
    databricks fs rm "dbfs:$volume/wheels/$old" ${profile_args[@]+"${profile_args[@]}"} || true
  fi
done
for w in "$whldir"/*.whl; do [ -f "$w" ] && up "$w" "wheels/$(basename "$w")"; done

echo "==> done. Volume populated; $base + $rtbase uploaded to $volume/udf/."
