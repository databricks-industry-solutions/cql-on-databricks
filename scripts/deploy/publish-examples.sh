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
# Idempotent: re-publishing overwrites in place. Runs AFTER the base environment the notebooks name is built
# (DEPLOY-BASE-ENVIRONMENT).
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
shared_root="/Shared/cql_databricks/examples"
src="$here/examples"

# Everything in the directory: `start_here`, the documentation-only index page that is the folder's front door
# (SVC-EXAMPLE-INDEX), one `%md` cell and no code, and a folder per group of notebooks, `getting_started` and
# `scenarios`, published as the same folders.
[ -d "$src" ] || { echo "!!! no examples directory at $src"; exit 1; }
echo "==> publishing $(ls "$src"/*.py "$src"/*/*.ipynb | wc -l | tr -d ' ') notebooks (index + examples) + $(ls "$src"/*.png | wc -l | tr -d ' ') asset(s) -> $shared_root"

# SVC-EXAMPLE-NOTEBOOKS, DEPLOY-BASE-ENVIRONMENT — the notebooks hold nothing of this deployment: each example's
# environment names the base environment the deploy published, by its workspace-wide name, so the repository copy
# is the published copy and nothing is filled in.
databricks workspace mkdirs "$shared_root" ${profile_args[@]+"${profile_args[@]}"}

# FATAL on failure (no `|| true`): a half-published folder is worse than no folder, because a user would open a
# stale copy from an earlier deploy beside a fresh one.
_publish() {  # <file> <format> [<language>] — the file's path under examples/ is its path under $shared_root
  local name; name="${1#"$src"/}"; name="${name%.*}"
  # PIPE-DEPLOY-NO-PLACEHOLDER-ENV — a placeholder in a published notebook fails its first cell.
  if grep -q '__CQL_[A-Z_]*__' "$1"; then
    echo "!!! $name holds a placeholder, which nothing fills in:"; grep -n '__CQL_[A-Z_]*__' "$1"; exit 1
  fi
  echo "    -> $shared_root/$name"
  databricks workspace import "$shared_root/$name" --file "$1" --format "$2" ${3:+--language "$3"} --overwrite \
    ${profile_args[@]+"${profile_args[@]}"}
}
# The index page is the one source notebook: SOURCE format + PYTHON language, so the workspace renders it as a
# notebook rather than a plain file. The examples are Jupyter, the one format that stores a notebook's environment.
for f in "$src"/*.py; do _publish "$f" SOURCE PYTHON; done
for group in "$src"/*/; do
  ls "$group"*.ipynb >/dev/null 2>&1 || continue   # content and test folders hold no notebook
  databricks workspace mkdirs "$shared_root/$(basename "$group")" ${profile_args[@]+"${profile_args[@]}"}
  for f in "$group"*.ipynb; do _publish "$f" JUPYTER; done
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

# The example libraries (SVC-EXAMPLE-KNOWLEDGE): the `.cql` files in `examples/measures`, and nothing else from it —
# the same files the deploy writes to the default environment's knowledge volume, under `libraries/`
# (DEPLOY-DEFAULT-ENVIRONMENT). The notebooks read them as a workspace folder of libraries. They have to land BESIDE
# the notebooks, because a notebook derives the path from its own notebook location rather than from a configured
# root — the same reason the masthead above cannot live anywhere central. The `.measure.json` and `.synth.json`
# files beside them are for the deploy's tables and the synthesizer, which read them from the repository.
#
# A `.cql` file has no notebook header, so `--format AUTO` imports it as a workspace FILE, byte-identical — which is
# what makes it readable with a plain open() from a notebook via /Workspace.
measures="$shared_root/measures"
libraries=("$src"/measures/*.cql)
[ -e "${libraries[0]}" ] || { echo "!!! no CQL library in $src/measures — the notebooks cannot run without them"; exit 1; }
echo "==> publishing ${#libraries[@]} libraries -> $measures"
databricks workspace mkdirs "$measures" ${profile_args[@]+"${profile_args[@]}"}
for f in "${libraries[@]}"; do
  echo "    -> $measures/$(basename "$f")"
  databricks workspace import "$measures/$(basename "$f")" --file "$f" --format AUTO --overwrite \
    ${profile_args[@]+"${profile_args[@]}"}
done
# Then everything else in the folder is removed. `--overwrite` never deletes, so a file an earlier publish left there
# would stay beside the libraries; the folder holds exactly what this publish wrote.
keep="$(for f in "${libraries[@]}"; do basename "$f"; done)"
databricks workspace list "$measures" -o json ${profile_args[@]+"${profile_args[@]}"} \
  | KEEP="$keep" python3 -c '
import json, os, sys
keep = set(os.environ["KEEP"].split("\n"))
for entry in json.loads(sys.stdin.read() or "[]") or []:
    if entry["path"].rsplit("/", 1)[-1] not in keep:
        print(entry.get("object_type", ""), entry["path"])' \
  | while read -r kind path; do
      echo "    x  $path"
      if [ "$kind" = DIRECTORY ]; then
        databricks workspace delete "$path" --recursive ${profile_args[@]+"${profile_args[@]}"}
      else
        databricks workspace delete "$path" ${profile_args[@]+"${profile_args[@]}"}
      fi
    done

echo "==> done. Examples published at $shared_root (readable + runnable by every workspace user)."
