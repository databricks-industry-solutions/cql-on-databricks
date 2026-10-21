"""SVC-WHEEL-VERSION — the version the deploy builds the cql-databricks client wheel under.

`<pyproject version>+src<content hash>`: the release number from compiler/client/pyproject.toml, plus a PEP 440
local label hashed from every file that goes into the wheel. A Databricks App keeps its venv across
deployments, and pip skips a wheel whose version is already installed, so a wheel whose contents changed
under the same version is never installed. The hash makes "contents changed" and "version changed" the same
thing, for committed and uncommitted changes alike, with no manual bump.

Stdlib only, so plain python3 runs it without a venv. Usage: python3 client_wheel_version.py <client dir>
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

#: Build and cache output under the client directory. None of it is wheel content.
_SKIP_DIRS = {"__pycache__", "build", "dist", ".pytest_cache"}


def _release(client: Path) -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', (client / "pyproject.toml").read_text(), re.MULTILINE)
    if not match:
        raise SystemExit(f"!!! no version in {client / 'pyproject.toml'} (SVC-WHEEL-VERSION)")
    return match.group(1)


def _content_files(client: Path) -> list[Path]:
    files = [client / "pyproject.toml", client / "README.md"]
    for path in sorted((client / "src").rglob("*")):
        rel = path.relative_to(client).parts
        if not path.is_file() or path.suffix == ".pyc":
            continue
        if any(part in _SKIP_DIRS or part.endswith(".egg-info") for part in rel):
            continue
        files.append(path)
    return files


def content_hash(client: Path) -> str:
    """12 hex digits over each wheel input's relative path and bytes, in a fixed order."""
    digest = hashlib.sha256()
    for path in _content_files(client):
        digest.update(path.relative_to(client).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def wheel_version(client: Path) -> str:
    # `src` keeps the label alphanumeric: PEP 440 strips leading zeros from an all-digit label, and the
    # normalized wheel filename would then differ from the version requirements.txt names.
    return f"{_release(client)}+src{content_hash(client)}"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: client_wheel_version.py <client dir>")
    print(wheel_version(Path(sys.argv[1])))
