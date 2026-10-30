"""The environment a LOCAL run starts with (SVC-LOCAL-CONFIG-BUNDLE).

Every setting has exactly one home:

- **Workspace settings** — which workspace, profile, catalogs, warehouse — live ONLY in the
  gitignored `databricks.local.yml`, the same file `bundle deploy` reads, so a local run and a deploy
  cannot disagree about where they point. They are read from `databricks bundle validate -o json`
  (the CLI's own merge of databricks.yml defaults and the local overrides) and exported as the env
  vars in `BUNDLE_OWNED`.
- **Local-run settings and secrets** — `CQL_MATERIALIZE_RETRIEVES`, `SKIP_*`, the
  local Compiler Service endpoint — live in the optional, gitignored root `.env`.

A `.env` that sets a `BUNDLE_OWNED` variable is REFUSED, not reconciled: two homes for one value is
the drift this exists to prevent. No `databricks.local.yml` means the offline mode (local Spark, no
workspace) and exports nothing.

STDLIB ONLY, and runnable as a script for shell tooling (scripts/lib/local-env.sh):

    python3 common/src/cql_databricks_common/local_env.py --shell [-t <target>]

prints `export` lines for the bundle values (after checking `.env`); the shell then sources `.env`.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

try:
    from .databricks_profile import repo_root
except ImportError:  # run as a script: the sibling module is on sys.path[0]
    from databricks_profile import repo_root

# Env var -> where its value lives in the resolved bundle (`bundle validate -o json`).
BUNDLE_OWNED: dict[str, tuple[str, ...]] = {
    "DATABRICKS_HOST": ("workspace", "host"),
    "DATABRICKS_CONFIG_PROFILE": ("workspace", "profile"),
    "UC_CATALOG": ("variables", "uc_catalog", "value"),
    "CONTENT_CATALOG": ("variables", "uc_catalog", "value"),
    "CQL_RUNTIME_CATALOG": ("variables", "uc_catalog", "value"),
    "CQL_DESIGNER_RESULTS_CATALOG": ("variables", "designer_results_catalog", "value"),
    "CQL_DESIGNER_CATALOG": ("variables", "designer_catalog", "value"),
    "CQL_DATABRICKS_CATALOG_SCOPES": ("variables", "catalog_scopes", "value"),
    "WAREHOUSE_ID": ("variables", "warehouse_id", "value"),
    "CQL_ASSISTANT_ENDPOINT": ("variables", "assistant_endpoint", "value"),
    "CQL_CANONICAL_BASE": ("variables", "canonical_base", "value"),
}

# Set once the environment is loaded, so a child process (run-app.sh -> the backend) does not
# validate the bundle a second time.
LOADED_MARKER = "CQL_LOCAL_ENV_TARGET"

_LOCAL_BUNDLE = "databricks.local.yml"


class LocalEnvError(RuntimeError):
    """The local configuration is inconsistent or unreadable. Fix the config; nothing is guessed."""


def bundle_values(target: str | None = None, *, root: Path | None = None,
                  run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict[str, str]:
    """The `BUNDLE_OWNED` env vars the bundle sets for `target`. Empty values are omitted, so the
    consumer's own default applies. No databricks.local.yml -> {} (offline mode)."""
    root = root or repo_root()
    if root is None or not (root / _LOCAL_BUNDLE).is_file():
        return {}
    target = _target(target)
    proc = run(["databricks", "bundle", "validate", "-t", target, "-o", "json"],
               cwd=root, capture_output=True, text=True)
    # validate prints the resolved config even when it also reports an error (e.g. an unstaged App
    # payload directory), and only the config is needed here. No JSON at all is a real failure.
    try:
        resolved = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise LocalEnvError(f"`databricks bundle validate -t {target}` produced no config:\n"
                            f"{proc.stderr.strip()}") from None
    out: dict[str, str] = {}
    for name, path in BUNDLE_OWNED.items():
        value = resolved
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value not in (None, ""):
            out[name] = str(value)
    return out


def check_dotenv(root: Path | None = None) -> None:
    """Refuse a root `.env` that sets a `BUNDLE_OWNED` variable."""
    root = root or repo_root()
    if root is None:
        return
    try:
        lines = (root / ".env").read_text().splitlines()
    except OSError:
        return
    owned = set()
    for line in lines:
        name, sep, _ = line.strip().removeprefix("export ").partition("=")
        if sep and name.strip() in BUNDLE_OWNED:
            owned.add(name.strip())
    if owned:
        raise LocalEnvError(
            f".env sets {', '.join(sorted(owned))} — workspace settings live only in {_LOCAL_BUNDLE} "
            f"(SVC-LOCAL-CONFIG-BUNDLE). Remove them from .env; set them in {_LOCAL_BUNDLE}.")


def load_local_env(target: str | None = None) -> None:
    """Load the local environment into os.environ, once per process tree: bundle values first, then
    `.env` (python-dotenv, which never overrides a variable already set). Call it once, from a
    `__main__`."""
    if os.environ.get(LOADED_MARKER):
        return
    root = repo_root()
    check_dotenv(root)
    _apply(bundle_values(target, root=root))
    os.environ[LOADED_MARKER] = _target(target)
    if root is not None and (root / ".env").is_file():
        try:
            from dotenv import load_dotenv
        except ImportError as e:
            raise LocalEnvError("a .env exists but python-dotenv is not installed") from e
        load_dotenv(root / ".env", override=False)


def _apply(values: dict[str, str]) -> None:
    """The bundle wins over an inherited value — and says so, rather than silently replacing it."""
    for name, value in values.items():
        _notice_replaced(name, value)
        os.environ[name] = value


def _notice_replaced(name: str, value: str) -> None:
    prior = os.environ.get(name)
    if prior is not None and prior != value:
        print(f"local-env: {name}={prior!r} from the shell replaced by {value!r} from "
              f"{_LOCAL_BUNDLE}", file=sys.stderr)


def _target(target: str | None) -> str:
    return target or os.environ.get("DATABRICKS_BUNDLE_TARGET") or "default"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-t", "--target")
    parser.add_argument("--shell", action="store_true", required=True,
                        help="print `export` lines for the bundle values")
    args = parser.parse_args()
    try:
        check_dotenv()
        values = bundle_values(args.target)
    except LocalEnvError as e:
        sys.exit(f"ERROR: {e}")
    for name, value in values.items():
        _notice_replaced(name, value)
        print(f"export {name}={shlex.quote(value)}")
    print(f"export {LOADED_MARKER}={shlex.quote(_target(args.target))}")
