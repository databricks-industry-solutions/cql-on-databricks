#!/usr/bin/env bash
# Run CQL on Databricks locally — the Apps the Designer uses (SVC-APP-TOPOLOGY, APP-PER-FEATURE):
#   1. CQL Compiler Service  (JVM, :8080)    — translates CQL to ELM/SQL
#   2. agent MCP server      (Python, :3002) — the agent's tools at /mcp
#   3. Designer + SPA        (Python, :3000) — serves the UI + /api; runs calculations on the SQL warehouse
#   4. Vite dev server       (:5173)         — HMR + proxy to :3000 (dev mode only, --dev flag)
# The evaluator's point App is not started: nothing here calls it (`./gradlew :evaluator:service:run` runs it).
#
# Config: workspace settings from databricks.local.yml, local-run settings from the optional root .env
# (SVC-LOCAL-CONFIG-BUNDLE); auth from ~/.databrickscfg.
# Usage:
#   scripts/run-app.sh          # build SPA, start compiler + backend, open browser
#   scripts/run-app.sh --dev    # Vite HMR on :5173 instead of pre-built SPA
#   scripts/run-app.sh --skip-build  # skip Gradle + npm build (use existing artifacts)
#
# Ctrl-C stops all processes.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
venv_py="$here/.venv/bin/python"
designer="$here/designer"
compiler="$here/compiler/server"

dev=false; skip_build=false
for arg in "$@"; do case "$arg" in
  --dev) dev=true;;
  --skip-build) skip_build=true;;
esac; done

# Load the local environment once for every process started below (SVC-LOCAL-CONFIG-BUNDLE); the
# backend sees it is loaded and does not validate the bundle again.
. "$here/scripts/lib/local-env.sh"
load_local_env

# ── Cleanup: kill background children on exit ──────────────────────────────
pids=()
cleanup() {
  echo ""
  echo "==> shutting down…"
  for p in "${pids[@]}"; do
    kill "$p" 2>/dev/null && wait "$p" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

# ── 1. Build (unless --skip-build) ─────────────────────────────────────────
if ! $skip_build; then
  echo "==> building Compiler Service (Gradle installDist)…"
  ( cd "$compiler" && ./gradlew -q installDist )

  if ! $dev; then
    echo "==> building CQL on Databricks SPA (npm run ui:build)…"
    ( cd "$designer" && npm install --silent && npm run ui:build )
  fi
fi

# Verify artifacts exist.
compiler_bin="$compiler/build/install/compiler-app/bin/compiler-app"
if [ ! -x "$compiler_bin" ]; then
  echo "ERROR: Compiler not built. Run: ./gradlew :compiler:server:installDist" >&2
  exit 1
fi
if ! $dev && [ ! -d "$designer/dist" ]; then
  echo "ERROR: SPA not built. Run: cd designer && npm run ui:build" >&2
  exit 1
fi

# ── 2. Free ports from any stale run ───────────────────────────────────────
for port in 8080 3002 3000; do kill $(lsof -ti tcp:$port) 2>/dev/null || true; done
$dev && kill $(lsof -ti tcp:5173) 2>/dev/null || true

# ── 3. Start Compiler Service (:8080) ──────────────────────────────────────
echo "==> starting Compiler Service on :8080…"
PORT=8080 "$compiler_bin" &
pids+=($!)

# ── 4. Start the agent (:3002) and the Designer (:3000) ──────────────────────
echo "==> starting the agent's MCP server on :3002…"
PORT=3002 "$venv_py" -m cql_databricks.agent.server.app &
pids+=($!)

echo "==> starting the Designer on :3000…"
PORT=3000 "$venv_py" -m cql_databricks_designer &
pids+=($!)

# ── 5. Wait for the backends, then start Vite (--dev) or open browser ──────────
# The backends must be accepting connections BEFORE Vite starts, otherwise the SPA
# loads instantly and its /api calls hit ETIMEDOUT while uvicorn is still booting.
echo "==> waiting for the Designer…"
until curl -sf http://127.0.0.1:3000/api/health >/dev/null 2>&1; do sleep 0.5; done

if $dev; then
  echo "==> starting Vite dev server on :5173 (HMR)…"
  ( cd "$designer" && npx vite --host 127.0.0.1 ) &
  pids+=($!)
  url="http://localhost:5173"
else
  url="http://localhost:3000"
fi

echo "==> CQL on Databricks ready at $url (agent MCP at http://127.0.0.1:3002/mcp)"

# Keep the script alive until Ctrl-C.
wait
