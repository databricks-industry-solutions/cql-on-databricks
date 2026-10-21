# Sourced by scripts/*.sh — the ~/.databrickscfg profile comes from CONFIG, never from source
# (SVC-DEPLOY-ENV-LOCAL). The resolution order (explicit -p, then databricks.local.yml, then
# DATABRICKS_CONFIG_PROFILE) lives in ONE place: cql_databricks_common/databricks_profile.py,
# stdlib-only so plain python3 runs it without a venv.
#
#   config_profile [-t <target>] [-p <profile>]   # prints the profile, or nothing
_config_profile_py="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/common/src/cql_databricks_common/databricks_profile.py"
config_profile() { python3 "$_config_profile_py" "$@"; }
