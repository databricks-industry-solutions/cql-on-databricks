# Sourced by scripts/*.sh — load the environment a LOCAL run starts with (SVC-LOCAL-CONFIG-BUNDLE):
# workspace settings from databricks.local.yml (via `databricks bundle validate`), then local-run
# settings and secrets from the optional root .env. A .env that sets a workspace setting is refused.
# The rules live in ONE place: cql_databricks_common/local_env.py (stdlib-only, plain python3).
#
#   load_local_env [-t <target>]
_local_env_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
load_local_env() {
  local exports
  exports="$(python3 "$_local_env_root/common/src/cql_databricks_common/local_env.py" --shell "$@")" \
    || return 1
  eval "$exports"
  # The .env uses ${VAR} references (some to the bundle values just exported), harmless but they trip
  # `set -u`; restore whatever nounset state the caller had.
  if [ -f "$_local_env_root/.env" ]; then
    local nounset=0; case $- in *u*) nounset=1;; esac
    set +u; set -a; . "$_local_env_root/.env"; set +a
    [ "$nounset" = 0 ] || set -u
  fi
}
