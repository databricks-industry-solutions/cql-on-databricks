"""CONTENT-ECQI-TERMS-GATE, CONTENT-ECQI-USER-PACKAGE, CONTENT-ECQI-LOAD-UNCHANGED — import an eCQI measure package the user chooses.

The repository ships no official measure content and no list of measures. A user who wants one names the
package (its eCQI file name, or a zip they already hold); this module shows the third-party terms, requires the
user to type ``I AGREE``, downloads the package from the eCQI Resource Center to the user's own machine, shows
each Measure's own ``copyright`` field verbatim, and requires ``I AGREE`` again before reading or loading any
content. There is no default acceptance and no fallback: anything but ``I AGREE`` cancels, and a
non-interactive run must pass ``--accept-terms`` explicitly.

The package is loaded UNCHANGED (``CONTENT-ECQI-LOAD-UNCHANGED``): its FHIR ``Measure`` and ``Library`` resources go to
the knowledge tables as shipped, with no Measurement Period edit, no stripping and no relabelling. The period is
a run-time parameter (``LANG-PARAMETER``). Value-set expansions come from VSAC with the user's own UMLS key
(``vsac.py``); nothing is fetched from anywhere else.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone

import requests

log = logging.getLogger("content_ingest.ecqi")

#: Where the eCQI Resource Center serves measure packages.
ECQI_PACKAGE_BASE = "https://ecqi.healthit.gov/sites/default/files/ecqm/measures"

_TIMEOUT = 180  # seconds — the packages run to a few MB

#: The words a user types to accept. Compared exactly, after trimming the line.
ACCEPTANCE = "I AGREE"

#: CONTENT-ECQI-TERMS-GATE — DRAFT, pending legal review. Do not present it as final until legal has approved it.
TERMS_STATUS = "DRAFT — pending legal review"

TERMS = """\
Third-party measure content: read before continuing

This tool downloads electronic clinical quality measure (eCQM) packages directly from the eCQI
Resource Center (ecqi.healthit.gov), operated by the U.S. Centers for Medicare & Medicaid Services
(CMS) and the Office of the National Coordinator for Health IT (ONC). The download goes from that site
to you. Databricks does not distribute, license or sublicense this content and has no rights in it.

The measures are owned by their stewards, for example the National Committee for Quality Assurance
(NCQA) or CMS. They also contain codes from third-party code systems, including CPT® (American Medical
Association), SNOMED CT® (SNOMED International), LOINC® (Regenstrief Institute), ICD-10 (World Health
Organization) and RxNorm (U.S. National Library of Medicine), each under its own license.

Each package states its own terms. Some stewards permit only internal, noncommercial use without
their approval. NCQA, for example, requires approval and a license for any other use, including
vendors embedding the measures in a product or service that calculates measure results for customers.

By typing I AGREE, you confirm that:

1. You are downloading this content yourself, for your own use, directly from its source.
2. You have read the copyright notice and terms shown below for each package, and you will comply
   with them.
3. You, or the organization you act for, hold every approval and license your use requires, including
   any steward approval (such as NCQA's) and any code-system license (such as the AMA CPT license or
   the UMLS license).
4. You will not redistribute this content, including by committing it to a shared or public
   repository, except as its terms permit.

Databricks provides this tool "as is" and makes no representation that any use of the downloaded
content is permitted. Results calculated with this tool are not certified, validated or endorsed by
NCQA, CMS or any measure steward, and Databricks is not affiliated with them. Databricks is not
responsible for your use of the downloaded content.
"""


class TermsNotAccepted(Exception):
    """The user did not type ``I AGREE``. Nothing past the point of refusal ran."""


class PackageError(Exception):
    """The named package is not an eCQI FHIR measure package, or could not be fetched. Always fatal."""


Prompt = Callable[[str], str]


def require_acceptance(text: str, *, accept_terms: bool, prompt: Prompt, out: Callable[[str], None]) -> None:
    """Show ``text`` and require ``I AGREE``. ``accept_terms`` is the explicit non-interactive acceptance: the text
    is still shown, and the acceptance is still recorded."""
    out(text)
    if accept_terms:
        out(f"Accepted with --accept-terms ({ACCEPTANCE}).")
        return
    answer = prompt(f"Type {ACCEPTANCE} to continue, or anything else to cancel: ")
    if answer.strip() != ACCEPTANCE:
        raise TermsNotAccepted("cancelled: the terms were not accepted")


def package_url(package: str) -> str:
    """The eCQI download URL for a package file name (``.zip`` added when absent)."""
    if "/" in package or "\\" in package:
        raise PackageError(f"{package!r} is neither an eCQI package file name nor an existing zip")
    name = package if package.endswith(".zip") else f"{package}.zip"
    return f"{ECQI_PACKAGE_BASE}/{name}"


def download(url: str, dest_dir: str) -> str:
    """Download ``url`` into ``dest_dir`` and return the path. Never reuses a cached copy: the user accepted the
    terms for this download."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, url.rsplit("/", 1)[-1])
    log.info("downloading %s", url)
    resp = requests.get(url, timeout=_TIMEOUT, stream=True)
    if resp.status_code != 200:
        raise PackageError(f"eCQI returned HTTP {resp.status_code} for {url}; check the package name on its "
                           "eCQI page")
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return dest


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_fhir_resources(zip_path: str) -> list[dict]:
    """The FHIR ``Measure`` / ``Library`` resources a package ships under ``resources/*.json``, as shipped. Each
    package also ships an XML twin of every resource; only the JSON is read."""
    resources: list[dict] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in sorted(zf.namelist()):
                if not member.endswith(".json") or os.path.dirname(member) != "resources":
                    continue
                data = json.loads(zf.read(member))
                if data.get("resourceType") in ("Measure", "Library"):
                    resources.append(data)
    except zipfile.BadZipFile as e:
        raise PackageError(f"{zip_path} is not a zip archive") from e
    if not any(r.get("resourceType") == "Measure" for r in resources):
        raise PackageError(f"{zip_path} ships no Measure resource under resources/; not an eCQI FHIR package")
    return resources


def copyright_notice(resources: list[dict]) -> str:
    """Every Measure's ``copyright`` field, verbatim, labelled with the measure it belongs to."""
    parts = []
    for measure in (r for r in resources if r.get("resourceType") == "Measure"):
        label = measure.get("title") or measure.get("name") or measure.get("url") or "(unnamed measure)"
        notice = measure.get("copyright")
        parts.append(f"--- {label} {measure.get('version', '')}".rstrip() + "\n"
                     + (notice if notice else "(This measure states no copyright notice.)"))
    return "\n\n".join(parts)


def valueset_urls(resources: list[dict]) -> list[str]:
    """The value sets the package's libraries declare, from each Library's ``dataRequirement`` code filters and
    ``relatedArtifact`` dependencies, as shipped."""
    urls: set[str] = set()
    for lib in (r for r in resources if r.get("resourceType") == "Library"):
        for req in lib.get("dataRequirement") or []:
            for cf in req.get("codeFilter") or []:
                if cf.get("valueSet"):
                    urls.add(cf["valueSet"].split("|", 1)[0])
        for art in lib.get("relatedArtifact") or []:
            ref = art.get("resource") or ""
            if art.get("type") == "depends-on" and "/ValueSet/" in ref:
                urls.add(ref.split("|", 1)[0])
    return sorted(urls)


def acquire(package: str, *, accept_terms: bool, cache_dir: str, prompt: Prompt = input,
            out: Callable[[str], None] = print) -> tuple[str, list[dict]]:
    """CONTENT-ECQI-TERMS-GATE — the gate, in order. Returns the zip path and its Measure/Library resources.

    1. Show the terms and require acceptance, before any network call.
    2. Download the package (or take the local zip the user named).
    3. Show each Measure's own copyright verbatim and require acceptance again, before any content is loaded.
    Acceptance is logged with the package, its SHA-256, the local user and the time.
    """
    require_acceptance(f"[{TERMS_STATUS}]\n\n{TERMS}", accept_terms=accept_terms, prompt=prompt, out=out)
    path = package if os.path.isfile(package) else download(package_url(package), cache_dir)
    resources = extract_fhir_resources(path)
    require_acceptance(f"Copyright and terms stated by {os.path.basename(path)}:\n\n{copyright_notice(resources)}\n",
                       accept_terms=accept_terms, prompt=prompt, out=out)
    log.info("CONTENT-ECQI-TERMS-GATE: terms accepted for %s (sha256=%s) by %s at %s%s", os.path.basename(path),
             sha256_file(path), getpass.getuser(), datetime.now(timezone.utc).isoformat(),
             " with --accept-terms" if accept_terms else "")
    return path, resources
