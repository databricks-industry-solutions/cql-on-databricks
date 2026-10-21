#!/usr/bin/env bash
# Stage the CQL on Databricks Compiler Service App payload for the DAB (SVC-DEPLOY-BUNDLE, SVC-PACKAGING).
#
# Databricks Apps store source as Workspace files (10 MB/file). Our jars are all < 10 MB, so they ship
# as app source; only a JRE + 3 PUBLIC jars (icu4j, spark-connect-client-jvm, scala-compiler) exceed the
# limit, and bootstrap.py fetches THOSE from the runtime UC Volume at startup (public-URL fallback).
#
# This assembles build/app/ = bootstrap.py + requirements.txt + app.yaml + lib/ (installDist jars MINUS
# the 3 oversized ones). deploy.sh exports CQL_DATABRICKS_ARTIFACT_VOLUME so the staged app.yaml points at the
# volume; run standalone the placeholder stays. Build on any OS. Usage: scripts/deploy/stage-compiler.sh
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
svc="$here/compiler/server"
app="$svc/build/app"
# The oversized PUBLIC jars bootstrap.py fetches from Maven — keep in sync with bootstrap.py MAVEN_JARS.
OVERSIZED=(icu4j-77.1.jar spark-connect-client-jvm_2.13-4.1.2.jar scala-compiler-2.13.17.jar)

echo "==> installDist (through mavenRepoUrl when set, else Maven Central)"
( cd "$here" && ./gradlew :compiler:server:installDist )

echo "==> assemble app source at $app (lib/ minus the 3 Maven-hosted jars)"
rm -rf "$app"; mkdir -p "$app/lib"
cp "$svc"/build/install/compiler-app/lib/*.jar "$app/lib/"
for j in "${OVERSIZED[@]}"; do rm -f "$app/lib/$j"; done
cp "$svc/deploy/bootstrap.py"     "$app/bootstrap.py"
cp "$svc/deploy/requirements.txt" "$app/requirements.txt"   # databricks-sdk for the volume read (SVC-PACKAGING)
cp "$svc/deploy/app.yaml"         "$app/app.yaml"

# Substitute the runtime-artifact volume path into the STAGED app.yaml only (never the source). deploy.sh
# exports the deploy-resolved path as CQL_DATABRICKS_ARTIFACT_VOLUME; run standalone the placeholder stays and
# bootstrap.py falls back to the public URLs. python (not sed) so the path's slashes need no escaping.
vol="${CQL_DATABRICKS_ARTIFACT_VOLUME:-}"
if [ -n "$vol" ] && [ "$vol" != "__CQL_DATABRICKS_ARTIFACT_VOLUME__" ]; then
  CQL_DATABRICKS_ARTIFACT_VOLUME="$vol" python3 -c 'import os,sys;p=sys.argv[1];s=open(p).read();open(p,"w").write(s.replace("__CQL_DATABRICKS_ARTIFACT_VOLUME__",os.environ["CQL_DATABRICKS_ARTIFACT_VOLUME"]))' "$app/app.yaml"
  echo "==> app.yaml CQL_DATABRICKS_ARTIFACT_VOLUME=$vol"
else
  echo "==> app.yaml CQL_DATABRICKS_ARTIFACT_VOLUME left as placeholder (bootstrap will use public URLs)"
fi

# SVC-COMPILER-IDENTITY / PIPE-BARRIER-HASH: stamp the compiler identity CQL on Databricks keys its materialization
# cache on. The git sha of BOTH repos, because the emitted SQL is a product of both: the engine
# (the compiler area) does the lowering, this service wraps it. Dirty working trees get a
# `-dirty` marker with a timestamp, so an uncommitted build never masquerades as the commit it sits on.
#
# Stable for the life of a deployment: an App restart must not invalidate result tables, a new deploy must.
# ONE repository now, so the compiler and this host always ship from the same commit -- there is no
# longer a second SHA that can silently disagree with it.
engine_sha=$(git -C "$here" rev-parse --short HEAD 2>/dev/null || echo "unknown")
svc_sha=$(git -C "$here" rev-parse --short HEAD 2>/dev/null || echo "unknown")
dirty=""
if ! git -C "$here" diff --quiet HEAD -- compiler/server 2>/dev/null; then
  dirty="-dirty$(date +%s)"
fi
compiler_version="engine-${engine_sha}+svc-${svc_sha}${dirty}"
CQL_COMPILER_VERSION="$compiler_version" python3 -c 'import os,sys;p=sys.argv[1];s=open(p).read();open(p,"w").write(s.replace("__CQL_COMPILER_VERSION__",os.environ["CQL_COMPILER_VERSION"]))' "$app/app.yaml"
echo "==> app.yaml CQL_COMPILER_VERSION=$compiler_version"

# Guard: fail if any app-source file is >= 10 MB (would be rejected at app deploy).
big=$(find "$app" -type f -size +10485760c) || true
if [ -n "$big" ]; then echo "!!! oversized app-source file(s):"; echo "$big"; exit 1; fi

echo "==> staged OK ($(ls "$app/lib" | wc -l | tr -d ' ') jars in app source; JRE + 3 jars fetched from the volume at startup)"
du -sh "$app"