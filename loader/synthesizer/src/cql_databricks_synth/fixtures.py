"""Pinned, generated-on-machine measure fixtures (SYNTH-PDS-FIXTURE-PINS).

Measure fixtures — per-library ELM and value-set expansions — are DERIVED artifacts:
ELM is the Compiler Service's translation of pinned CQL, expansions are a snapshot of
the runtime's own terminology table. They are deliberately NOT committed:

- ELM bytes churn with every translator release (unreviewable diffs at corpus scale);
- published value-set expansions are licensed content — the repo references value sets by identity and
  never embeds licensed expansions at scale (`SHARE-LICENSING`);
- at 80+ measures the corpus is hundreds of megabytes of generated data.

What IS committed is ``pins.json`` beside this module: for every measure, the identity
and sha256 of each CQL source, the sha256 of every generated ELM, the sha256 of each
expansion snapshot, and the sha256 of the measure's realism config when it has one
(``measure-config.json`` in the fixture dir, SYNTH-PDS-MEASURE-CONFIG). ``build()`` regenerates the bytes into a local cache and verifies
them against the pins — so ground truth stays a function of (measure, config, seed)
(SYNTH-PDS-DETERMINISM), and a translator upgrade or upstream CQL change surfaces as a
loud hash mismatch that is re-pinned deliberately (``--repin``), reviewed like any other
diff, never drifted past.

Cache layout: ``~/.synth/fixtures/<measure>/`` (override: $CQL_DATABRICKS_SYNTH_CACHE),
each with a ``.pins.json`` stamp of the pins entry it was built from; ``fixture_dir()``
returns a cache dir only when its stamp matches the CURRENT pins.

Building requires a running Compiler Service (ELM) and, for UC-sourced expansions, a
Databricks profile and catalog — the same inputs the artifacts are derived from. The pins name
the terminology table without a catalog; the catalog is workspace config
(SVC-LOCAL-CONFIG-BUNDLE), never a tracked file. Tests SKIP with the
exact fix when a fixture set is absent (the `scripts/test.sh` convention).
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import SynthesizerError

PINS_PATH = Path(__file__).parent / "pins.json"
CACHE_ENV = "CQL_DATABRICKS_SYNTH_CACHE"
#: Where a measure's pinned realism config lands in its fixture dir (SYNTH-PDS-MEASURE-CONFIG).
CONFIG_NAME = "measure-config.json"


def cache_root() -> Path:
    return Path(os.environ.get(CACHE_ENV, "~/.synth/fixtures")).expanduser()


def repo_root() -> Path:
    """The monorepo root, found by its marker rather than a parent count: the package has sat at several depths
    (`CARVE-CONTENT`, `SPLIT-CONTENT`), and a fixed count is easy to get one level short."""
    for root in Path(__file__).resolve().parents:
        if (root / "settings.gradle").is_file():
            return root
    raise SynthesizerError(
        "fixture building requires the repo checkout (pinned CQL sources are repo-relative); "
        f"no settings.gradle above {Path(__file__).resolve()}")


def load_pins() -> dict:
    if not PINS_PATH.exists():
        raise SynthesizerError(f"{PINS_PATH} missing")
    return json.loads(PINS_PATH.read_text())


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stamp(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True)


def fixture_dir(measure: str) -> Path | None:
    """The measure's cache dir, or None when absent/stale relative to current pins."""
    entry = load_pins().get(measure)
    if entry is None:
        raise SynthesizerError(f"measure {measure!r} not in {PINS_PATH}")
    directory = cache_root() / measure
    stamp = directory / ".pins.json"
    if not stamp.exists() or stamp.read_text() != _stamp(entry):
        return None
    return directory


def dir_or_skip(measure: str) -> Path:
    """Test-side resolution: the cache dir, or a module-level pytest skip naming the fix."""
    import pytest

    directory = fixture_dir(measure)
    if directory is None:
        pytest.skip(
            f"{measure} fixtures not built (or stale) — run: "
            f"python -m cql_databricks_synth fixtures",
            allow_module_level=True)
    return directory


@dataclass
class BuildResult:
    measure: str
    directory: Path
    libraries: int
    valuesets: int


def build(measure: str, *, endpoint: str, profile: str | None, catalog: str | None = None,
          repin: bool = False, pins: dict | None = None) -> BuildResult:
    """Generate one measure's fixtures into the cache and verify against the pins.

    With ``repin=True`` the observed hashes are recorded into ``pins`` (the caller
    writes pins.json afterwards) instead of being enforced.
    """
    pins = pins if pins is not None else load_pins()
    entry = pins.get(measure)
    if entry is None:
        raise SynthesizerError(f"measure {measure!r} not in pins")
    root = repo_root()
    directory = cache_root() / measure
    directory.mkdir(parents=True, exist_ok=True)
    # The directory is the measure's fixture set and nothing else: a library or value set an earlier pin
    # named must not linger beside the new ones, where it would load as part of the closure.
    for stale in [*directory.glob("*.elm.json"), *directory.glob("valueset-*.ndjson"),
                  directory / CONFIG_NAME, directory / ".pins.json"]:
        stale.unlink(missing_ok=True)

    # -- 1. gather pinned CQL sources -------------------------------------------------
    sources: list[tuple[dict, str]] = []
    for ref in entry["cql"]:
        path = (root / ref["path"]).resolve()
        if not path.exists():
            raise SynthesizerError(f"pinned CQL missing: {ref['path']} (resolved {path})")
        text = path.read_text()
        digest = _sha256(text.encode())
        if repin:
            ref["sha256"] = digest
        elif ref.get("sha256") != digest:
            raise SynthesizerError(
                f"pinned CQL drifted: {ref['path']} is {digest}, pins say {ref.get('sha256')} "
                f"— if the upstream change is intended, re-pin with `fixtures --repin`")
        sources.append((ref, text))

    # -- 2. translate each emitting library against the rest of the closure -----------
    elm_hashes: dict[str, str] = {}
    urls: set[str] = set()
    written = 0
    for ref, text in sources:
        if not ref.get("emitElm", True):
            continue
        includes = [other for other_ref, other in sources if other_ref is not ref]
        elm_text = _translate(endpoint, text, includes, ref["path"])
        library = json.loads(elm_text)["library"]
        ident = library["identifier"]["id"]
        (directory / f"{ident}.elm.json").write_text(elm_text)
        digest = _sha256(elm_text.encode())
        elm_hashes[ident] = digest
        pinned = entry.get("elm", {}).get(ident)
        if not repin and pinned != digest:
            raise SynthesizerError(
                f"ELM for {ident} is {digest}, pins say {pinned} — translator output "
                f"changed; review and re-pin with `fixtures --repin`")
        for vs in library.get("valueSets", {}).get("def", []):
            urls.add(vs["id"])
        written += 1
    if repin:
        entry["elm"] = dict(sorted(elm_hashes.items()))

    # -- 3. expansions ------------------------------------------------------------------
    vs_spec = entry["valuesets"]
    if vs_spec["source"] == "repo":
        vs_count = _copy_repo_valuesets(root, vs_spec, directory, repin)
    elif vs_spec["source"] == "uc":
        vs_count = _snapshot_uc_valuesets(vs_spec, sorted(urls), directory, profile, catalog,
                                          repin)
    else:
        raise SynthesizerError(f"unknown valuesets source {vs_spec['source']!r}")

    # -- 4. the measure's realism config, when it has one (SYNTH-PDS-MEASURE-CONFIG) ---------
    if "config" in entry:
        _copy_config(root, entry["config"], directory, repin)

    (directory / ".pins.json").write_text(_stamp(entry))
    return BuildResult(measure=measure, directory=directory,
                       libraries=written, valuesets=vs_count)


def _translate(endpoint: str, cql: str, includes: list[str], label: str) -> str:
    body = json.dumps({"cql": cql, "includes": includes}).encode()
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/translate", data=body,
        headers={"Content-Type": "application/json"})
    try:
        response = json.load(urllib.request.urlopen(request, timeout=120))
    except OSError as e:
        raise SynthesizerError(
            f"Compiler Service unreachable at {endpoint} ({e}) — fixture ELM is "
            f"generated by /translate; start the local stack or set CQL_COMPILER_ENDPOINT"
        ) from e
    if not response.get("ok"):
        raise SynthesizerError(
            f"translate failed for {label}: {(response.get('diagnostics') or ['?'])[0]}")
    return response["elm"]


def _copy_config(root: Path, ref: dict, directory: Path, repin: bool) -> None:
    source = root / ref["path"]
    if not source.exists():
        raise SynthesizerError(f"pinned measure config missing: {ref['path']}")
    data = source.read_bytes()
    digest = _sha256(data)
    if repin:
        ref["sha256"] = digest
    elif ref.get("sha256") != digest:
        raise SynthesizerError(
            f"pinned measure config drifted: {ref['path']} is {digest}, pins say {ref.get('sha256')} "
            f"— re-pin with `fixtures --repin`")
    (directory / CONFIG_NAME).write_bytes(data)


def _copy_repo_valuesets(root: Path, spec: dict, directory: Path, repin: bool) -> int:
    for ref in spec["files"]:
        source = root / ref["path"]
        if not source.exists():
            raise SynthesizerError(f"pinned expansion missing: {ref['path']}")
        data = source.read_bytes()
        digest = _sha256(data)
        if repin:
            ref["sha256"] = digest
        elif ref.get("sha256") != digest:
            raise SynthesizerError(
                f"pinned expansion drifted: {ref['path']} — re-pin with `fixtures --repin`")
        (directory / source.name).write_bytes(data)
    return len(spec["files"])


def _snapshot_uc_valuesets(spec: dict, urls: list[str], directory: Path,
                           profile: str | None, catalog: str | None, repin: bool) -> int:
    """Snapshot the referenced expansions from the runtime's terminology table
    (SYNTH-PDS-TERMINOLOGY: one expansion source for generator and engine). ``spec['table']``
    is ``schema.table``; ``catalog`` comes from workspace config (SVC-LOCAL-CONFIG-BUNDLE)."""
    if not catalog:
        raise SynthesizerError(
            "UC-sourced expansions need a catalog: pass --catalog or run under "
            "scripts/with-local-env.sh, which sets CONTENT_CATALOG from databricks.local.yml")
    from databricks.connect import DatabricksSession  # only needed to (re)build

    table = f"{catalog}.{spec['table']}"

    builder = DatabricksSession.builder.profile(profile) if profile else DatabricksSession.builder
    spark = builder.serverless(True).getOrCreate()
    in_list = ", ".join(f"'{u}'" for u in urls)
    rows = spark.sql(
        f"SELECT valueset_id, system, code FROM {table} "
        f"WHERE valueset_id IN ({in_list})").collect()
    by_url: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        by_url.setdefault(row.valueset_id, []).append((row.system, row.code))
    missing = [u for u in urls if u not in by_url]
    if missing:
        raise SynthesizerError(
            f"{table} holds no expansion for: {missing} — ingest terminology first")

    observed: dict[str, str] = {}
    for url in urls:
        oid = url.rsplit("/", 1)[-1]
        lines = [json.dumps({"valueset_id": url, "system": system, "code": code}) + "\n"
                 for system, code in sorted(set(by_url[url]))]
        data = "".join(lines).encode()
        (directory / f"valueset-{oid}.ndjson").write_bytes(data)
        observed[url] = _sha256(data)
        pinned = spec.get("sets", {}).get(url)
        if not repin and pinned != observed[url]:
            raise SynthesizerError(
                f"expansion snapshot for {url} is {observed[url]}, pins say {pinned} — "
                f"the terminology table changed; review and re-pin with `fixtures --repin`")
    if repin:
        spec["sets"] = dict(sorted(observed.items()))
    return len(urls)


def write_pins(pins: dict) -> None:
    PINS_PATH.write_text(json.dumps(pins, indent=1, sort_keys=True) + "\n")
