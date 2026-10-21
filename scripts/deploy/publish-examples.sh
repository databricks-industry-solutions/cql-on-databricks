#!/usr/bin/env bash
# Publish the example notebooks to a SHARED workspace folder every user can reach (SVC-EXAMPLE-NOTEBOOKS).
#
# THE GAP THIS CLOSES. `bundle deploy` syncs examples/ into the DEPLOYER's home under
# `/Users/<deployer>/.bundle/cql-databricks/<target>/files/...`. That copy is invisible to everyone else: a
# teammate who opens the workspace has no path to the examples. Publishing a second copy to /Shared gives the
# examples one stable, discoverable home that is the same for every user of the workspace.
#
# WRITABLE BY EVERY USER — DELIBERATE. /Shared grants the `users` group CAN_MANAGE, and workspace object
# ACLs are a UNION, not an override: setting CAN_READ on a child folder ADDS an entry, it does not
# subtract the CAN_MANAGE inherited from /Shared. There is no deny and no way to break inheritance, so
# anything under /Shared is editable and deletable by anyone (verified against the reference workspace, 2026-08-07).
# A genuinely read-only home would have to be a TOP-LEVEL folder (e.g. /cql-on-databricks), which inherits only from
# / and so can carry `users: CAN_READ` alone. /Shared was chosen anyway for the cleaner structure; this
# script is therefore IDEMPOTENT AND OVERWRITING (`--overwrite`), so each deploy restores the published
# copy to what the repo says it is. Local edits are not preserved, by design.
#
# The published copy is a DERIVED artifact, never a source of truth: `examples/tests/
# test_example_notebooks.py` runs the REPO copy, so the notebooks are validated where they are authored.
#
# Idempotent: re-publishing overwrites in place. Runs AFTER the bundle deploy, which creates the runtime
# Volume the notebooks install from.
#
# Usage: scripts/deploy/publish-examples.sh [-t <target>] [-p <profile>]
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

# The published root. Mirrored by EXAMPLES_PATH in designer/src/measures/model.ts, which links to it.
shared_root="/Shared/CqlDatabricks/examples"
src="$here/examples"

# Everything in the directory, including `00_start_here` — the documentation-only index page that is the
# folder's front door (SVC-EXAMPLE-INDEX). It is a notebook like the others as far as this script cares:
# one `%md` cell, no code, numbered `00_` so the workspace listing sorts it first.
[ -d "$src" ] || { echo "!!! no examples directory at $src"; exit 1; }
echo "==> publishing $(ls "$src"/*.py | wc -l | tr -d ' ') notebooks (index + examples) + $(ls "$src"/*.png | wc -l | tr -d ' ') asset(s) -> $shared_root"

# SVC-EXAMPLE-NOTEBOOKS, SVC-PACKAGING — each notebook's `%pip` cell names the runtime Volume as the
# `__CQL_DATABRICKS_ARTIFACT_VOLUME__` placeholder, because the Volume's catalog is the deployment's
# `uc_catalog`. The published copy gets this deployment's path. Resolved from the bundle variables, as
# deploy-runtime-artifacts.sh resolves it, so the notebooks install from the Volume that step filled.
cfg="$(cd "$here" && databricks bundle validate -t "$target" ${profile_args[@]+"${profile_args[@]}"} -o json)"
volume="$(printf '%s' "$cfg" | python3 -c "import sys,json;v=json.load(sys.stdin).get('variables') or {};f=lambda k:(v.get(k) or {}).get('value') or '';print('/Volumes/%s/%s/%s'%(f('uc_catalog'),f('uc_schema'),f('runtime_volume')))")"
case "$volume" in *//*|*/) echo "!!! could not resolve the runtime Volume path from the bundle (got $volume)"; exit 1;; esac
echo "==> runtime volume=$volume"
staged="$(mktemp -d)"; trap 'rm -rf "$staged"' EXIT

databricks workspace mkdirs "$shared_root" ${profile_args[@]+"${profile_args[@]}"}

for f in "$src"/*.py; do
  name="$(basename "$f" .py)"
  echo "    -> $shared_root/$name"
  CQL_DATABRICKS_ARTIFACT_VOLUME="$volume" python3 -c 'import os,sys;s=open(sys.argv[1]).read()
open(sys.argv[2],"w").write(s.replace("__CQL_DATABRICKS_ARTIFACT_VOLUME__",os.environ["CQL_DATABRICKS_ARTIFACT_VOLUME"]))' \
    "$f" "$staged/$name.py"
  # PIPE-DEPLOY-NO-PLACEHOLDER-ENV — a placeholder that survives fails the notebook's first cell.
  if grep -q '__CQL_[A-Z_]*__' "$staged/$name.py"; then
    echo "!!! $name still holds an unsubstituted placeholder:"; grep -n '__CQL_[A-Z_]*__' "$staged/$name.py"; exit 1
  fi
  # SOURCE format + PYTHON language: the files carry the `# Databricks notebook source` header, so the
  # workspace renders them as notebooks rather than plain files. FATAL on failure (no `|| true`): a
  # half-published folder is worse than no folder, because a user would open a stale copy from an earlier
  # deploy beside a fresh one.
  databricks workspace import "$shared_root/$name" \
    --file "$staged/$name.py" --format SOURCE --language PYTHON --overwrite \
    ${profile_args[@]+"${profile_args[@]}"}
done

# The index page's masthead (SVC-EXAMPLE-INDEX). A `%md` cell strips every `style` attribute, so the one
# designed element on the page is this raster, referenced as `./start-here-hero.png` — a path the notebook
# renderer resolves against the notebook's OWN folder, which is why the image has to land here beside the
# notebooks rather than anywhere central. `--format AUTO` imports it as a workspace FILE (not a notebook).
# Same fail-loud rule as above: an index whose masthead 404s is a broken front door.
for f in "$src"/*.png; do
  name="$(basename "$f")"
  echo "    -> $shared_root/$name"
  databricks workspace import "$shared_root/$name" \
    --file "$f" --format AUTO --overwrite \
    ${profile_args[@]+"${profile_args[@]}"}
done

# The content directories (SVC-EXAMPLE-KNOWLEDGE): the CQL measures and FHIR data
# notebook 03 mounts, and notebook 01 points `library_path` at. They have to land BESIDE the notebooks,
# because both notebooks derive the path from their own notebook location rather than from a configured
# root — the same reason the masthead above cannot live anywhere central.
#
# `import-dir` walks the tree, so each directory's structure is preserved. A `.cql` / `.ndjson` / `.json`
# file has no notebook header, so it imports as a workspace FILE and stays byte-identical — which is what
# makes it readable with a plain open() from a notebook via /Workspace.
for d in measures data; do
  [ -d "$src/$d" ] || { echo "!!! missing content directory $src/$d — notebook 03 cannot run without it"; exit 1; }
  echo "==> publishing content -> $shared_root/$d"
  databricks workspace import-dir "$src/$d" "$shared_root/$d" --overwrite \
    ${profile_args[@]+"${profile_args[@]}"}
done

echo "==> done. Examples published at $shared_root (readable + runnable by every workspace user)."
