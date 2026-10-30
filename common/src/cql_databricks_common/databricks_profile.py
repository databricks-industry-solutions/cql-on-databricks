"""Which ~/.databrickscfg profile a LOCAL tool authenticates with (SVC-DEPLOY-ENV-LOCAL,
SVC-LOCAL-CONFIG-BUNDLE).

The profile is environment config, like the workspace host: it is never named in source. A local tool
resolves it, first match wins, from:

  1. an explicit `--profile` / `-p` the caller passed;
  2. `targets.<target>.workspace.profile` in the gitignored `databricks.local.yml` (the preferred home —
     the same value `bundle deploy` uses, so deploy and local runs cannot disagree);
  3. `DATABRICKS_CONFIG_PROFILE` in the environment.

Never `.env`: a workspace setting has one home, and local_env.py refuses a `.env` that sets one.

None of them set -> None, and the Databricks SDK falls back to its own ambient chain.

Offline — no CLI call, no token — so it can name the profile `scripts/auth-preflight.sh` logs in
with. STDLIB ONLY, and runnable as a script, so shell tooling resolves through this same code without
a venv:

    python3 common/src/cql_databricks_common/databricks_profile.py [-t <target>] [-p <profile>]

prints the profile (or nothing). The target defaults to `$DATABRICKS_BUNDLE_TARGET`, then `default`.
"""
from __future__ import annotations

import os
from pathlib import Path

_LOCAL_BUNDLE = "databricks.local.yml"
_ENV_VAR = "DATABRICKS_CONFIG_PROFILE"


def databricks_profile(explicit: str | None = None, *, target: str | None = None) -> str | None:
    """The profile a local tool should use, or None to leave it to the SDK's ambient chain."""
    if explicit:
        return explicit
    root = repo_root()
    target = target or os.environ.get("DATABRICKS_BUNDLE_TARGET") or "default"
    return ((root and _bundle_profile(root / _LOCAL_BUNDLE, target))
            or os.environ.get(_ENV_VAR)
            or None)


def repo_root() -> Path | None:
    """The bundle root: the nearest directory holding databricks.yml, searched up from the working
    directory (how the CLI finds a bundle) and then from this file (an editable install)."""
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for d in (start, *start.parents):
            if (d / "databricks.yml").is_file():
                return d
    return None


def _bundle_profile(path: Path, target: str) -> str | None:
    """`targets.<target>.workspace.profile` from a bundle YAML file. A minimal block-mapping reader —
    enough for the plain shape databricks.local.yml.example has, without a PyYAML dependency."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    want = ["targets", target, "workspace", "profile"]
    stack: list[tuple[int, str]] = []
    for line in lines:
        body = line.split(" #", 1)[0].rstrip()
        if not body.strip() or body.lstrip().startswith(("#", "-")):
            continue
        key, sep, value = body.strip().partition(":")
        if not sep:
            continue
        indent = len(body) - len(body.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key.strip().strip("'\"")))
        if [k for _, k in stack] == want:
            return value.strip().strip("'\"") or None
    return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-t", "--target")
    parser.add_argument("-p", "--profile")
    args = parser.parse_args()
    print(databricks_profile(args.profile, target=args.target) or "")
