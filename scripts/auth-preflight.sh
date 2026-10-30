#!/usr/bin/env bash
# Ensure a usable Databricks OAuth token before a local run (SEC-LOCAL-AUTH-PREFLIGHT).
#
# `databricks auth token` resolves the token WITHOUT any browser: it reads the cached token from
# the OS keyring and silently exchanges the refresh token when the hour-long access token has
# expired. So the common case is a no-op, and the interactive login only runs when that fails.
#
# An IP-access-list rejection is reported as such instead of launching a login, because a browser
# cannot fix it — the workspace is refusing this network, not this identity.
#
# Profile comes from config (SVC-DEPLOY-ENV-LOCAL): databricks.local.yml, then DATABRICKS_CONFIG_PROFILE.
# Resolved OFFLINE — reading the rest of the bundle needs the very token this script obtains.
# Usage: scripts/auth-preflight.sh
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"

. "$here/scripts/lib/databricks-profile.sh"
profile="$(config_profile)"
if [ -z "$profile" ]; then
  echo "ERROR: no Databricks profile configured. Set targets.default.workspace.profile in" >&2
  echo "       databricks.local.yml (SVC-LOCAL-CONFIG-BUNDLE)." >&2
  exit 1
fi

echo "==> checking cached Databricks token for profile '$profile'…"
if err="$(databricks auth token --profile "$profile" 2>&1 >/dev/null)"; then
  echo "==> token valid — no login needed."
  exit 0
fi

if printf '%s' "$err" | grep -qiE 'ip access|IpAccessDenied|403'; then
  echo "ERROR: the workspace refused this request from this network (IP access list)." >&2
  echo "       Reconnect the corporate VPN and re-run — re-authenticating will NOT help." >&2
  echo "       Detail: $err" >&2
  exit 1
fi

echo "==> cached token unusable, opening browser login…"
echo "    $err"
databricks auth login --profile "$profile"

# Fail loud if the login did not actually produce a usable token (CORE-NO-SILENT-MISLOWER).
databricks auth token --profile "$profile" >/dev/null
echo "==> auth OK for profile '$profile'."
