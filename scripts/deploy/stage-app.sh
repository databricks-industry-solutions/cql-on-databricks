#!/usr/bin/env bash
# Stage the two Python App payloads (SVC-APP-TOPOLOGY, APP-PER-FEATURE, SVC-PACKAGING) for `databricks bundle deploy`.
#
# One payload per feature App, each with its app.yaml at the ROOT (where a Databricks App expects it):
#   designer   designer/server/build/app  — cql_databricks_designer/ + the agent + common (COMMON-LIBRARY)
#              + the built SPA (dist/)
#   agent      agent/server/build/app     — cql_databricks.agent + common
# The client (`cql_databricks.compiler`, `.data_bindings`, `.resolvers`, `.result`) arrives in the client wheel each App
# pip-installs (BINDINGS-INTO-CLIENT, RESULT-INTO-CLIENT), so it is not staged as source.
# The agent and common are staged INTO the payload, not installed by the App from wheels: the agent's server
# declares cql-databricks-common, which is not on PyPI, so pip in the App cannot resolve it (found on the first
# deploy, 2026-10-03, for the distribution common came from then). Common is copied as source; the agent's
# three packages are pip-installed into the payload here (stage_agent), because the skills package is mapped
# from its own folder rather than from a `src/` tree, and its pyproject is the one place that says so.
# The compiler App's payload is scripts/deploy/stage-compiler.sh's.
#
# Each requirements.txt names non-PyPI wheels in the runtime UC Volume (SVC-PACKAGING) whose full /Volumes
# path this script substitutes. Re-run before each deploy (deploy.sh does).
#
# ONE script for both so the placeholder substitution below runs over every descriptor in one pass:
# separate copies of it would drift, and the failure mode is an App that starts with an unsubstituted value.
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
designer_src="$here/designer/server/src"
common_src="$here/common/src"
out_designer="$here/designer/server/build/app"
out_agent="$here/agent/server/build/app"
# BUILD-TAXONOMY — the agent's components, all three in every payload that serves the agent.
agent_components=("$here/agent/service" "$here/agent/skills" "$here/agent/server")
venv_py="$here/.venv/bin/python"
[ -x "$venv_py" ] || { echo "!!! $venv_py not found — run scripts/setup.sh; staging builds the agent's packages with it"; exit 1; }

echo "==> build the Designer SPA (vite) -> designer/dist/"
( cd "$here/designer" && npm install --no-audit --no-fund && npm run ui:build )

# stage <out> <deploy dir> <src root>... — app.yaml + requirements.txt from <deploy dir>, then EVERY
# top-level `cql_databricks_*` package and EVERY `cql_databricks.*` namespace package under each source
# root, enumerated by glob rather than by name (SPLIT-PACKAGES): a named list is how a payload silently
# loses the package added after the list was written, and an App missing one fails at IMPORT, after a
# green build and a green test run.
stage() {
  local out="$1" deploy="$2"; shift 2
  rm -rf "$out"; mkdir -p "$out"
  cp "$deploy/app.yaml" "$out/app.yaml"
  cp "$deploy/requirements.txt" "$out/requirements.txt"
  local root pkg
  for root in "$@"; do
    for pkg in "$root"/cql_databricks_*; do
      [ -d "$pkg" ] || continue
      case "$(basename "$pkg")" in *.egg-info) continue;; esac
      cp -R "$pkg" "$out/$(basename "$pkg")"
    done
    # BUILD-CLIENT-NAMESPACE — the `cql_databricks` PEP 420 namespace, staged with no `__init__.py`: the
    # client wheel the App pip-installs adds `cql_databricks.compiler` beside it.
    for pkg in "$root"/cql_databricks/*; do
      [ -d "$pkg" ] || continue
      mkdir -p "$out/cql_databricks"
      cp -R "$pkg" "$out/cql_databricks/$(basename "$pkg")"
    done
  done
  if [ -e "$out/cql_databricks/__init__.py" ]; then
    echo "!!! $out/cql_databricks/ holds an __init__.py; it must stay a namespace (BUILD-CLIENT-NAMESPACE)"; exit 1
  fi
  # Never ship tests or pycache to a deployed App.
  find "$out" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  find "$out" -type d -name tests -prune -exec rm -rf {} + 2>/dev/null || true
}

# stage_agent <out> — the agent's three packages, installed into a staged payload as their wheels would
# install them (`cql_databricks/agent/{service,server,skills}`, with the skill files). No dependencies: the App
# installs those from requirements.txt. No build isolation, so staging needs no network.
stage_agent() {
  local out="$1"
  "$venv_py" -m pip install -q --disable-pip-version-check --no-deps --no-build-isolation --no-compile \
    --target "$out" "${agent_components[@]}" \
    || { echo "!!! could not install the agent's packages into $out"; exit 1; }
  rm -rf "$out"/*.dist-info
  for f in "$out/cql_databricks/__init__.py" "$out/cql_databricks/agent/__init__.py"; do
    [ ! -e "$f" ] || { echo "!!! $f exists; it must stay a namespace (BUILD-CLIENT-NAMESPACE)"; exit 1; }
  done
}

echo "==> assemble the Designer App at $out_designer (+ dist/)"
stage "$out_designer" "$here/designer/server/deploy" "$designer_src" "$common_src"
stage_agent "$out_designer"
cp -R "$here/designer/dist" "$out_designer/dist"
echo "==> assemble the agent App at $out_agent"
stage "$out_agent" "$here/agent/server/deploy" "$common_src"
stage_agent "$out_agent"

# SVC-PY311-SYNTAX — every payload must PARSE under the Databricks Apps runtime, which is Python 3.11.
# The dev venv is 3.12 (databricks-connect 17.3 is `Requires-Python ==3.12.*`), so 3.12-only syntax runs
# green through the whole local suite and then raises `SyntaxError` at IMPORT in the App — a crashed
# deployment with a healthy build and a green test run behind it. Observed 2026-08-10: a PEP 701
# multi-line f-string expression in app.py.
#
# `compileall` needs no packages, only the interpreter, so this is a stock python3.11 and one second.
# A missing interpreter FAILS rather than skipping: a check that quietly passes when it did not run is
# the class of thing this exists to catch. `brew install python@3.11` on macOS.
echo "==> verify the staged payloads parse under Python 3.11 (SVC-PY311-SYNTAX)"
command -v python3.11 >/dev/null 2>&1 || {
  echo "!!! python3.11 not found — it is required to verify the staged payloads against the"
  echo "    Databricks Apps runtime (SVC-PY311-SYNTAX). Install it: brew install python@3.11"; exit 1; }
# The client wheel each App installs from the Volume is built from compiler/client/src, so it is checked too: it is
# imported at startup like the staged source (observed 2026-11-01: a PEP 701 f-string in resolve.py).
# The bytecode goes to a scratch directory, never the source tree.
PYTHONPYCACHEPREFIX="$(mktemp -d)" python3.11 -m compileall -q "$here/compiler/client/src/cql_databricks" || {
  echo "!!! the client wheel's source does not parse under Python 3.11, which is what the Apps run (SVC-PY311-SYNTAX)."
  exit 1; }
for o in "$out_designer" "$out_agent"; do
  python3.11 -m compileall -q "$o"/cql_databricks_* "$o"/cql_databricks || {
    echo "!!! the staged source in $o does not parse under Python 3.11, which is what the App runs."
    echo "    It would deploy cleanly and then crash at import (SVC-PY311-SYNTAX). Fix the syntax above."
    exit 1; }
  # compileall writes __pycache__ next to each module; drop it again so the payload ships source only.
  find "$o" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
done

# APP-RUN-LOG — the build the run log records as app_version: the source revision, marked when the
# working tree has uncommitted changes. Empty outside a git checkout, which drops the entry below.
CQL_APP_VERSION="$(git -C "$here" rev-parse --short=12 HEAD 2>/dev/null || true)"
if [ -n "$CQL_APP_VERSION" ] && [ -n "$(git -C "$here" status --porcelain 2>/dev/null)" ]; then
  CQL_APP_VERSION="$CQL_APP_VERSION-dirty"
fi
export CQL_APP_VERSION

# Substitute placeholder env vars into the STAGED app.yaml. deploy.sh exports
# BUNDLE_VAR_* before calling
# this script; a var it leaves unset has its env entry DROPPED from the staged descriptor
# (PIPE-DEPLOY-NO-PLACEHOLDER-ENV) so config.py's own fallback chain is what applies.
for y in "$out_designer/app.yaml" "$out_agent/app.yaml"; do
python3 -c '
import os, re, sys
p = sys.argv[1]
s = open(p).read()
# Catalog + warehouse — sourced from BUNDLE_VAR_* exports.
mapping = {
    "__UC_CATALOG__": os.environ.get("BUNDLE_VAR_uc_catalog", ""),
    "__CQL_RUNTIME_CATALOG__": os.environ.get("BUNDLE_VAR_uc_catalog", ""),
    # APP-RUN-LOG — the catalog of the shared `designer` schema, and the build the log records.
    "__CQL_DESIGNER_CATALOG__": os.environ.get("BUNDLE_VAR_designer_catalog", ""),
    "__CQL_APP_VERSION__": os.environ.get("CQL_APP_VERSION", ""),
    "__WAREHOUSE_ID__": os.environ.get("BUNDLE_VAR_warehouse_id", ""),
    # APP-ASSISTANT — a NAME, not a resource binding: the FM call runs as the signed-in user
    # (SEC-OBO), so there is no App-side grant for a `valueFrom` to keep in step with.
    "__CQL_ASSISTANT_ENDPOINT__": os.environ.get("BUNDLE_VAR_assistant_endpoint", ""),
    # DOC-OPEN-SAVE — the canonical URL base of libraries created in the Designer.
    "__CQL_CANONICAL_BASE__": os.environ.get("BUNDLE_VAR_canonical_base", ""),
}
for placeholder, val in mapping.items():
    if val and val != placeholder:
        s = s.replace(placeholder, val)
# PIPE-DEPLOY-NO-PLACEHOLDER-ENV — drop every env entry whose value is STILL a placeholder. An
# unsubstituted placeholder means the deploy had no value for that var, and the descriptor must then
# say nothing about it: shipping `value: "__CQL_RUNTIME_CATALOG__"` sets the var to that literal
# string, which is truthy, so config.py never reaches its fallback and the app queries a catalog
# named __cql_runtime_catalog__. Absent is the only encoding of "not configured" the app can read.
name_re = re.compile("^  - name: \"[A-Z_0-9]+\"$")
placeholder_re = re.compile("^    value: \"__[A-Z_0-9]+__\"$")
lines, kept, dropped, i = s.split("\n"), [], [], 0
while i < len(lines):
    if name_re.match(lines[i]) and i + 1 < len(lines) and placeholder_re.match(lines[i + 1]):
        dropped.append(lines[i].split("\"")[1])
        i += 2
        continue
    kept.append(lines[i])
    i += 1
# The remover above is deliberately STRICT (it matches the exact two-line shape the descriptors use)
# and this guard is deliberately LOOSE, because the dangerous outcome is a placeholder the remover
# did not recognize — different indentation, single quotes, a lowercase name — sailing through and
# being deployed as a literal env value, which is the very defect this rule exists to prevent. A
# silent non-match is not allowed to look like success, so an unrecognized survivor kills the stage.
# Only `value:` lines are examined: the descriptors DISCUSS placeholders in their comments.
value_line = re.compile("^\\s*value\\s*:")
token = re.compile("__[A-Za-z_0-9]+__")
leftover = [l for l in kept if value_line.match(l) and token.search(l)]
if leftover:
    sys.stderr.write("!!! " + p + " still holds unsubstituted placeholders after staging:\n")
    for l in leftover:
        sys.stderr.write("!!!   " + l.strip() + "\n")
    sys.stderr.write("!!! Deploying these would set the env vars to those literal strings, and\n")
    sys.stderr.write("!!! config.py would read them as configured values (PIPE-DEPLOY-NO-PLACEHOLDER-ENV).\n")
    sys.stderr.write("!!! Match the entry shape used by the other env vars in the deploy/app.yaml files.\n")
    sys.exit(1)
open(p, "w").write("\n".join(kept))
if dropped:
    print("==> " + p + ": dropped unconfigured env vars " + ", ".join(dropped) + " (config.py defaults apply)")
' "$y"
done
echo "==> app.yaml catalog vars: UC_CATALOG=${BUNDLE_VAR_uc_catalog:-<dropped>} WAREHOUSE_ID=${BUNDLE_VAR_warehouse_id:-<dropped>}"

# SVC-PACKAGING — bake the runtime Volume path into the staged requirements.txt, so pip installs the
# cql-databricks wheel from UC. A Databricks App cannot install it any other way: the package is not on
# PyPI, and requirements.txt supports neither relative paths nor env-var references — the full /Volumes
# path has to be literal. deploy.sh exports CQL_DATABRICKS_ARTIFACT_VOLUME (the same value stage-compiler.sh uses).
# FATAL when unset: unlike app.yaml's placeholders, which degrade to config.py defaults, an unsubstituted
# wheel path makes pip fail and the App never starts — so a payload that carries one must not be staged.
vol="${CQL_DATABRICKS_ARTIFACT_VOLUME:-}"
if [ -z "$vol" ] || [ "$vol" = "__CQL_DATABRICKS_ARTIFACT_VOLUME__" ]; then
  echo "!!! CQL_DATABRICKS_ARTIFACT_VOLUME is unset — requirements.txt would keep the __CQL_DATABRICKS_ARTIFACT_VOLUME__"
  echo "!!! placeholder and the App's pip install would fail. Run scripts/deploy/deploy.sh (which exports it)"
  echo "!!! or set CQL_DATABRICKS_ARTIFACT_VOLUME=/Volumes/<catalog>/<schema>/<volume> before staging."
  exit 1
fi
# SVC-WHEEL-VERSION — the version deploy-runtime-artifacts.sh builds the wheel under: the pyproject.toml
# release plus a hash of the wheel's contents. The App keeps its venv across deployments and pip skips a
# version already installed, so a rebuilt wheel under an unchanged version would never reach the App.
. "$here/scripts/lib/client-wheel-version.sh"
client_ver="$(client_wheel_version)"
[ -n "$client_ver" ] || { echo "!!! could not compute the client wheel version (SVC-WHEEL-VERSION)"; exit 1; }
# EVERY payload: each one installs the client wheel, and a placeholder left in any of them fails its pip.
for r in "$out_designer/requirements.txt" "$out_agent/requirements.txt"; do
  CQL_DATABRICKS_ARTIFACT_VOLUME="$vol" CQL_SPARK_CLIENT_VERSION="$client_ver" python3 -c 'import os,sys;p=sys.argv[1];s=open(p).read()
for k in ("CQL_DATABRICKS_ARTIFACT_VOLUME","CQL_SPARK_CLIENT_VERSION"): s=s.replace("__%s__"%k,os.environ[k])
open(p,"w").write(s)' "$r"
  # GUARD, same shape as the app.yaml one (PIPE-DEPLOY-NO-PLACEHOLDER-ENV): a placeholder that survives
  # substitution is not a degraded default here, it is an App that never starts. Check the requirement
  # LINES only — the comments above them name the placeholders on purpose.
  if grep -v '^[[:space:]]*#' "$r" | grep -q '__[A-Z_]*__'; then
    echo "!!! $r still holds an unsubstituted placeholder — pip would fail and the App would never bind"
    grep -v '^[[:space:]]*#' "$r" | grep '__[A-Z_]*__'
    exit 1
  fi
done
echo "==> requirements.txt CQL_DATABRICKS_ARTIFACT_VOLUME=$vol (cql_databricks-$client_ver wheel)"

for o in "$out_designer" "$out_agent"; do
  echo "==> staged OK ($(du -sh "$o" | cut -f1)): $o"
done
