"""CONTENT-IMPORT-MEASURES — import quality measures into Unity Catalog. The one import path.

The caller hands over FHIR resources it has already read: ``Measure``, ``Library`` and ``ValueSet`` resources, or
``Bundle``s of them, plus any value-set codes it holds as rows. This module writes them to the store tables of the
environment the caller names, ``<catalog>.knowledge_store`` (``DATA-ENVIRONMENT-LAYOUT``), and tags nothing: the environment's published views select the stores, so the Apps and
``cql(library=...)`` find the measures there (``BIND-PUBLISHED-VIEW``). Where
the content comes from is the caller's business: nothing here reads a file or fetches anything. The deploy loads
the content in ``examples/`` through it (``scripts/deploy/shipped_sources``), and the skill ``import-quality-measures``
uses it for anything else (``CONTENT-IMPORT-SKILL``).

Everything is checked before anything is written, and every problem is reported at once:

- a ``Measure`` must name a ``Library`` that is in this import or already in the knowledge store
  (``CONTENT-MEASURE-LIBRARY-LINK``);
- every value set a library declares must have codes, in this import or already in the knowledge store. A
  measure run against a missing value set silently under-counts every population that retrieves through it
  (``CORE-NO-SILENT-MISLOWER``);
- a ``ValueSet`` resource must carry its codes: an ``expansion``, or a ``compose`` that only lists concepts. One
  defined by filters or by other value sets has to be expanded first.

A value set's codes replace that value set's earlier codes whole (``CONTENT-INCREMENTAL``); measures and libraries
merge on their canonical ``(url, version)``.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .transform import TransformResult, ValueSetExpansionRow, _decode_content, transform_resources

log = logging.getLogger("cql_databricks.data_bindings.measure_import.measures")

#: A CQL value-set declaration: ``valueset "Name": 'url'`` (CQL 1.5, section 5.4).
_CQL_VALUESET_RE = re.compile(r"""^\s*(?:public\s+|private\s+)?valueset\s+"[^"]*"\s*:\s*'([^']+)'""", re.M)

#: How many names an error lists before it says how many more there are.
_LISTED = 20


class ImportContentError(ValueError):
    """The content cannot be imported as given. Nothing was written."""


@dataclass(frozen=True)
class ImportSummary:
    """What an import wrote, or would write on a dry run."""
    knowledge: str                     # catalog.schema of the measures, libraries and value-set codes
    measures: list[str]                # canonical url|version of each Measure
    libraries: list[str]               # canonical url|version of each Library
    valuesets: list[str]               # each value set given codes
    skipped: dict[str, int] = field(default_factory=dict)  # resourceType -> resources not imported
    rows: dict[str, int] = field(default_factory=dict)     # table -> rows written; empty on a dry run


def flatten(resources: Iterable[Mapping[str, Any]]) -> list[dict]:
    """The resources, with every ``Bundle`` replaced by its entries' resources, at any depth."""
    out: list[dict] = []
    for resource in resources:
        if resource.get("resourceType") == "Bundle":
            out.extend(flatten(entry["resource"] for entry in resource.get("entry") or [] if "resource" in entry))
        else:
            out.append(dict(resource))
    return out


def valueset_codes(valueset: Mapping[str, Any], *, source: str, now: str) -> list[ValueSetExpansionRow]:
    """The codes of one ``ValueSet`` resource: its ``expansion``, else a ``compose`` that only lists concepts."""
    url = valueset.get("url")
    if not url:
        raise ImportContentError(f"ValueSet {valueset.get('id', '(no id)')} has no url")
    version = (valueset.get("expansion") or {}).get("timestamp") or valueset.get("version")
    codes: list[tuple[str, str, str | None]] = []

    def walk(contains: list[dict]) -> None:
        for c in contains:
            if c.get("system") and c.get("code") and not c.get("abstract"):
                codes.append((c["system"], c["code"], c.get("display")))
            walk(c.get("contains") or [])

    if valueset.get("expansion"):
        walk(valueset["expansion"].get("contains") or [])
    else:
        compose = valueset.get("compose") or {}
        for include in compose.get("include") or []:
            if include.get("filter") or include.get("valueSet") or not include.get("concept"):
                raise ImportContentError(
                    f"ValueSet {url} is defined by filters or other value sets and carries no expansion: "
                    "expand it first, then import the expanded ValueSet or its codes")
            codes.extend((include["system"], c["code"], c.get("display")) for c in include["concept"])
        if compose.get("exclude"):
            raise ImportContentError(f"ValueSet {url} excludes codes and carries no expansion: expand it first")
    if not codes:
        raise ImportContentError(f"ValueSet {url} has no codes")
    return [ValueSetExpansionRow(valueset_id=url, system=s, code=c, display=d, version=version, source_url=url,
                                 source_package=source, ingested_at=now) for s, c, d in codes]


def declared_valuesets(library: Mapping[str, Any]) -> set[str]:
    """The value sets one ``Library`` declares: in its CQL, its data requirements and its dependencies."""
    urls = set(_CQL_VALUESET_RE.findall(_decode_content(dict(library), "text/cql") or ""))
    for requirement in library.get("dataRequirement") or []:
        for code_filter in requirement.get("codeFilter") or []:
            if code_filter.get("valueSet"):
                urls.add(code_filter["valueSet"])
    for artifact in library.get("relatedArtifact") or []:
        ref = artifact.get("resource") or ""
        if artifact.get("type") == "depends-on" and "/ValueSet/" in ref:
            urls.add(ref)
    return {url.split("|", 1)[0] for url in urls}


def _listed(names: Iterable[str]) -> str:
    names = sorted(names)
    more = f", and {len(names) - _LISTED} more" if len(names) > _LISTED else ""
    return ", ".join(names[:_LISTED]) + more


def _existing(spark, fqtn: str, column: str) -> set[str]:
    """The distinct values of ``column`` already in ``fqtn``; empty when the table does not exist yet."""
    if not spark.catalog.tableExists(fqtn):
        return set()
    return {row[0] for row in spark.sql(f"SELECT DISTINCT {column} FROM {fqtn}").collect()}


def import_measures(spark, resources: Iterable[Mapping[str, Any]], *, catalog: str, source: str,
                    codes: Iterable[Mapping[str, Any]] = (), lineage: str = "import",
                    dry_run: bool = False) -> ImportSummary:
    """CONTENT-IMPORT-MEASURES — import measures, libraries and value-set codes into the stores of the environment in
    ``catalog`` (`DATA-ENVIRONMENT-LAYOUT`): ``knowledge_store.library``, ``.measure`` and ``.valueset``.

    ``resources`` are FHIR resources (``Measure``, ``Library``, ``ValueSet``, or ``Bundle``s of them); any other
    type is counted in ``skipped`` and not imported. ``codes`` are value-set codes already in rows:
    ``{valueset_id, system, code}``, with ``display`` and ``version`` optional. ``source`` names where this
    content came from, as provenance on every row. ``dry_run`` checks everything and writes nothing.
    """
    from cql_databricks.resolvers.environment import KNOWLEDGE_STORE

    from .load import _qualify, ensure_valueset_table, load

    if not catalog:
        raise ImportContentError("no catalog: an import writes an environment's stores, which are in its catalog")
    schema = KNOWLEDGE_STORE
    now = datetime.now(timezone.utc).isoformat()
    flat = flatten(resources)

    skipped: dict[str, int] = {}
    for r in flat:
        if r.get("resourceType") not in ("Measure", "Library", "ValueSet"):
            skipped[str(r.get("resourceType"))] = skipped.get(str(r.get("resourceType")), 0) + 1
    knowledge = transform_resources([r for r in flat if r.get("resourceType") in ("Measure", "Library")],
                                    source_package=source, source_year=0, source_lineage=lineage)
    not_cql = sum(r.get("resourceType") == "Library" for r in flat) - len(knowledge.libraries)
    if not_cql:
        skipped["Library (no CQL or ELM content)"] = not_cql
    no_library = sum(r.get("resourceType") == "Measure" for r in flat) - len(knowledge.measures)
    if no_library:
        skipped["Measure (names no Library)"] = no_library

    problems: list[str] = []
    expansions: list[ValueSetExpansionRow] = []
    for vs in (r for r in flat if r.get("resourceType") == "ValueSet"):
        try:
            expansions.extend(valueset_codes(vs, source=source, now=now))
        except ImportContentError as e:
            problems.append(str(e))
    for i, row in enumerate(codes):
        missing = [k for k in ("valueset_id", "system", "code") if not row.get(k)]
        if missing:
            problems.append(f"code row {i} has no {', '.join(missing)}")
            continue
        expansions.append(ValueSetExpansionRow(
            valueset_id=row["valueset_id"], system=row["system"], code=row["code"], display=row.get("display"),
            version=row.get("version"), source_url=row["valueset_id"], source_package=source, ingested_at=now))
    if not (knowledge.measures or knowledge.libraries or expansions or problems):
        raise ImportContentError("nothing to import: no Measure, no Library with CQL or ELM, and no value-set codes")

    knowledge_fqn = _qualify(catalog, schema, "library")
    valueset_fqn = _qualify(catalog, schema, "valueset")
    libraries = {r.url for r in knowledge.libraries} | _existing(spark, knowledge_fqn, "url")
    for m in knowledge.measures:
        unknown = [ref for ref in json.loads(m.resource_json).get("library") or []
                   if ref.split("|", 1)[0] not in libraries]
        if unknown:
            problems.append(f"Measure {m.url}|{m.version} names {_listed(unknown)}, which is not in this import "
                            f"or in {catalog}.{schema}")
    declared = set()
    for lib in knowledge.libraries:
        declared |= declared_valuesets(json.loads(lib.resource_json))
    loaded = {row.valueset_id.split("|", 1)[0] for row in expansions}
    without_codes = declared - loaded - _existing(spark, valueset_fqn, "valueset_id")
    if without_codes:
        problems.append(f"{len(without_codes)} value set(s) the libraries declare have no codes in this import or "
                        f"in {catalog}.{schema}: {_listed(without_codes)}")
    if problems:
        raise ImportContentError("cannot import:\n- " + "\n- ".join(problems))

    summary = dict(
        knowledge=f"{catalog}.{schema}",
        measures=[f"{m.url}|{m.version}" for m in knowledge.measures],
        libraries=[f"{lib.url}|{lib.version}" for lib in knowledge.libraries],
        valuesets=sorted(loaded), skipped=skipped)
    if dry_run:
        return ImportSummary(**summary)

    rows = load(spark, TransformResult(measures=knowledge.measures, libraries=knowledge.libraries,
                                       valueset_expansions=expansions),
                catalog=catalog, authority_schema=schema, valueset_schema=schema)
    ensure_valueset_table(spark, catalog=catalog, valueset_schema=schema)
    log.info("imported into %s: %s", summary["knowledge"], ", ".join(f"{k}={v}" for k, v in sorted(rows.items())))
    return ImportSummary(**summary, rows=rows)
