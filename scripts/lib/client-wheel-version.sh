# Sourced by scripts/*.sh — the version the client wheel is built and installed under (SVC-WHEEL-VERSION).
# deploy.sh exports CQL_CLIENT_WHEEL_VERSION once, so staging (which writes it into each App's
# requirements.txt) and the wheel build agree even if the tree changes between the two steps. Run on its own,
# a script computes it here. The hashing lives in client_wheel_version.py.
#
#   client_wheel_version   # prints e.g. 0.1.1+src3f2a9c0d41be
_client_wheel_version_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
client_wheel_version() {
  if [ -n "${CQL_CLIENT_WHEEL_VERSION:-}" ]; then echo "$CQL_CLIENT_WHEEL_VERSION"; return; fi
  python3 "$_client_wheel_version_root/scripts/lib/client_wheel_version.py" "$_client_wheel_version_root/compiler/client"
}
