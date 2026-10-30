#!/usr/bin/env bash
# Cross-component test runner (SVC-APP-TOPOLOGY). ONE entry point for every test suite in the repo, so
# "run the tests" is the same command whichever component you touched.
#
#   scripts/test.sh [unit]        # OFFLINE suites: client, servers, libraries, loader, deploy, frontend
#   scripts/test.sh integration   # LIVE suites against the deployed workspace (examples/tests)
#   scripts/test.sh all           # unit, then integration
#
# The JVM suite is not here: `./gradlew checkAll` runs it, with the conformance gate.
#
# unit: a missing prerequisite (venv / node_modules) is reported as SKIPPED with the exact fix — never a
# silent pass. integration: a missing prerequisite, or any test that skips, is a FAIL, because the caller
# asked for the live run. Exits non-zero iff a suite that RAN failed.
set -o pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
designer="$here/designer"
venv_py="$here/.venv/bin/python"

mode="${1:-unit}"
case "$mode" in
  unit|integration|all) ;;
  *) echo "usage: scripts/test.sh [unit|integration|all]"; exit 2;;
esac

PASS=""; FAIL=""; SKIP=""
run() {  # run <name> <cwd> <cmd...>
  local name="$1" cwd="$2"; shift 2
  echo; echo "== [$name] =="
  if ( cd "$cwd" && "$@" ); then PASS="$PASS|$name"; else FAIL="$FAIL|$name"; fi
}
skip() { echo "  SKIP: $2"; SKIP="$SKIP|$1"; }

if [ "$mode" = "unit" ] || [ "$mode" = "all" ]; then
  # ---------------------------------------------------------------------------- 1. plugin (offline)
  # Prefer the venv (client already installed); else bare python3 + PYTHONPATH (import needs no deps).
  if [ -x "$venv_py" ]; then
    run "client unittest" "$here/compiler/client" "$venv_py" -m unittest discover -s tests
  else
    echo; echo "== [client unittest] =="
    if ( cd "$here/compiler/client" && PYTHONPATH="$here/compiler/client/src" python3 -m unittest discover -s tests ); then
      PASS="$PASS|client unittest"; else FAIL="$FAIL|client unittest"; fi
  fi

  # ---------------------------------------------------------------------------- 2. servers and libraries (pytest)
  # The shared server library (COMMON-LIBRARY), the Designer's server and the agent.
  if [ -x "$venv_py" ]; then
    run "common pytest" "$here/common" "$venv_py" -m pytest -q
    run "designer server pytest" "$designer/server" "$venv_py" -m pytest -q
    run "agent pytest" "$here/agent" "$venv_py" -m pytest -q
  else
    skip "common pytest" "venv absent — run: scripts/setup.sh"
    skip "designer server pytest" "venv absent — run: scripts/setup.sh"
    skip "agent pytest" "venv absent — run: scripts/setup.sh"
  fi

  # ---------------------------------------------------------------------------- 2a. client pytest, loader, deploy
  # data-bindings and the results, in the client (BINDINGS-INTO-CLIENT, RESULT-INTO-CLIENT; their tests are pytest,
  # the rest of the client's are unittest; data-bindings includes the measure import, CONTENT-IMPORT-MEASURES) and
  # the deploy's own Python: its shipped sources (DEPLOY-SHIPPED-SOURCES) and scripts/deploy/tests. The loader's
  # synthesizer follows.
  if [ -x "$venv_py" ]; then
    run "data-bindings pytest" "$here/compiler/client" "$venv_py" -m pytest -q tests/data_bindings
    run "result pytest" "$here/compiler/client" "$venv_py" -m pytest -q tests/result
    run "shipped sources pytest" "$here/scripts/deploy/shipped_sources" "$venv_py" -m pytest -q
    run "deploy pytest" "$here" "$venv_py" -m pytest -q scripts/deploy/tests
  else
    skip "data-bindings pytest" "venv absent — run: scripts/setup.sh"
    skip "result pytest" "venv absent — run: scripts/setup.sh"
    skip "shipped sources pytest" "venv absent — run: scripts/setup.sh"
    skip "deploy pytest" "venv absent — run: scripts/setup.sh"
  fi

  # ---------------------------------------------------------------------------- 2b. synthesizer (pytest)
  if [ -x "$venv_py" ]; then
    run "loader synthesizer pytest" "$here/loader/synthesizer" "$venv_py" -m pytest -q
  else
    skip "loader synthesizer pytest" "venv absent — run: scripts/setup.sh"
  fi

  # ---------------------------------------------------------------------------- 3. app frontend (tsc + vitest)
  if [ -d "$designer/node_modules" ]; then
    run "designer frontend" "$designer" npm run typecheck
    run "designer frontend vitest" "$designer" npm run test
  else
    skip "designer frontend" "node_modules absent — run: scripts/setup.sh"
  fi
fi

if [ "$mode" = "integration" ] || [ "$mode" = "all" ]; then
  # -------------------------------------------------------------------------- 4. live suites (examples/tests)
  # SVC-EXAMPLE-NOTEBOOKS — the deployed-compiler smoke and every example notebook as a one-time serverless
  # run, against the workspace databricks.local.yml names. Every setting the tests read is resolved here,
  # never typed: the profile (SVC-DEPLOY-ENV-LOCAL), the runtime Volume from the bundle variables, and the
  # compiler App's URL and a token from the CLI. The smoke runs its SQL on serverless through Databricks
  # Connect (DATABRICKS_SERVERLESS): the root venv carries databricks-connect, which refuses a local session.
  echo; echo "== [integration] =="
  integration() {
    [ -x "$venv_py" ] || { echo "  venv absent — run: scripts/setup.sh"; return 1; }
    . "$here/scripts/lib/databricks-profile.sh"
    local target="${DATABRICKS_BUNDLE_TARGET:-default}" profile cfg volume catalog app url token junit skipped
    profile="$(config_profile -t "$target")"
    [ -n "$profile" ] || { echo "  no profile — set targets.$target.workspace.profile in databricks.local.yml"; return 1; }
    cfg="$(cd "$here" && databricks bundle validate -t "$target" -p "$profile" -o json)" || return 1
    jqpy() { printf '%s' "$cfg" | python3 -c "import sys,json;d=json.load(sys.stdin);$1"; }
    volume="$(jqpy "v=d.get('variables') or {};f=lambda k:(v.get(k) or {}).get('value') or '';print('/Volumes/%s/%s/%s'%(f('uc_catalog'),f('uc_schema'),f('runtime_volume')))")"
    case "$volume" in *//*|*/) echo "  could not resolve the runtime Volume path from the bundle (got $volume)"; return 1;; esac
    # BIND-PUBLISH: the live publish writes a scratch schema here, and drops it.
    catalog="$(jqpy "print(((d.get('variables') or {}).get('uc_catalog') or {}).get('value') or '')")"
    [ -n "$catalog" ] || { echo "  could not resolve uc_catalog from the bundle"; return 1; }
    app="$(jqpy "print(d['resources']['apps']['cql_databricks_compiler']['name'])")" || return 1
    url="$(databricks apps get "$app" -p "$profile" -o json | python3 -c "import sys,json;print(json.load(sys.stdin).get('url') or '')")"
    [ -n "$url" ] || { echo "  App $app has no URL — deploy it: scripts/deploy/deploy.sh"; return 1; }
    token="$(databricks auth token -p "$profile" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")" || return 1
    echo "  profile=$profile volume=$volume app=$app"
    junit="$(mktemp)"
    ( cd "$here" && DATABRICKS_CONFIG_PROFILE="$profile" CQL_DATABRICKS_LIVE=1 DATABRICKS_SERVERLESS=1 \
        CQL_DATABRICKS_ARTIFACT_VOLUME="$volume" CQL_DATABRICKS_PUBLISH_CATALOG="$catalog" \
        CQL_DATABRICKS_ENDPOINT="$url" CQL_DATABRICKS_TOKEN="$token" \
        "$venv_py" -m pytest -q -rs --junitxml="$junit" examples/tests ) || { rm -f "$junit"; return 1; }
    # A skipped live test proved nothing, so it fails the run.
    skipped="$(python3 -c "import sys,xml.etree.ElementTree as E;r=E.parse(sys.argv[1]).getroot();print(sum(int(s.get('skipped',0)) for s in r.iter('testsuite')))" "$junit")"
    rm -f "$junit"
    [ "$skipped" = "0" ] || { echo "  $skipped live test(s) skipped — see the reasons above"; return 1; }
  }
  if integration; then PASS="$PASS|integration"; else FAIL="$FAIL|integration"; fi
fi

# ---------------------------------------------------------------------------- summary
echo; echo "==================== summary ($mode) ===================="
IFS='|'; for s in $PASS; do [ -n "$s" ] && echo "  PASS  $s"; done
for s in $SKIP; do [ -n "$s" ] && echo "  SKIP  $s"; done
for s in $FAIL; do [ -n "$s" ] && echo "  FAIL  $s"; done
unset IFS
if [ -n "$FAIL" ]; then echo; echo "RESULT: FAIL"; exit 1; fi
echo; echo "RESULT: OK"; exit 0
