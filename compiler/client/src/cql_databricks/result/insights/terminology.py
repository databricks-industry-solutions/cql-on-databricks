"""SPEC-TERMINOLOGY-EXTRACT — the coded values a define's results actually carry.

WHAT QUESTION THIS ANSWERS, and which one it deliberately does not. It reports the terminology PRESENT
ON THE RECORDS A DEFINE RETURNED, grouped by where on the record it sits: this definition's results
carry these diagnosis codes, these body sites, this clinical status. It does NOT report which value set
qualified a record, and it does not reconstruct a filter.

**THE PATHS ARE CURATED, NOT DISCOVERED.** Every coded field is reachable by walking the emitted struct,
and that was the first design; it is rejected, on measurement. The Condition struct this pipeline writes
is 4,390 characters carrying ELEVEN `coding: array<struct<…>>` sites, most of them administrative
(`meta.security`, `meta.tag`, extensions) — and the structural signature that finds them is treacherous
in both directions. A field carrying `system: string` also matches `Identifier` and `Reference.assigner`;
**`Quantity` carries both `system` AND `code`**, where the code is a UCUM unit rather than clinical
terminology. Discovery therefore needs a curated exclusion list to be readable, and curating exclusions
is strictly worse than curating inclusions: you maintain the same artifact and get noise when you are
wrong instead of silence. Naming the paths also gives each section a HUMAN name — "Diagnosis", "Body
site" — where discovery could only print a field path at a clinical reader (`APP-AUDIENCE`).

**COMPLETENESS IS A TEST, NOT A RUNTIME PATH.** The schema walk survives as a check
(`test_terminology.py`): a coding site present in the emitted struct and absent from the package here is
reported, so the packages cannot silently go stale when the emit changes shape. That buys the awareness
discovery offered without putting its noise in front of a reader.

**THE MODEL IS A PARAMETER.** Packages are keyed by (data model, type), not by type alone. CQL is
model-agnostic by construction — a library declares `using FHIR version '4.0.1'` or `using QDM version
'5.6'`, and QDM's coded fields are neither named nor shaped like FHIR's. Keying on the type alone would
make `Encounter` mean whichever model happened to be loaded first, which is the kind of collision this
repo has already paid for once (`SPEC-ANCHOR`, two libraries declaring one name). A model this module
has no package for yields NO SECTIONS rather than a guess.

Nothing here re-derives what the compiler owns (`APP-DECOUPLE`): the accessor expressions describe
the shape of the table THIS PIPELINE WROTE, which is a fact about our own emit, not about CQL semantics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..relation import SUBJECT_ID, literal, quote
from ..results import Execute, result_relation
from .extractors import DEFAULT_MODEL, KIND_CODING, Element, elements_for

log = logging.getLogger("cql_databricks.result.insights.terminology")

#: The value column of a resource-valued relation. A boolean or scalar define names its value column
#: after the define instead and carries no resource, so it has no terminology to extract.
RESOURCE_VALUE_COLUMN = "__val"


@dataclass(frozen=True)
class TerminologyCode:
    """One coded value found at one path, with how much of the population carries it."""

    system: str | None
    code: str | None
    display: str | None
    subjects: int
    records: int


@dataclass(frozen=True)
class TerminologySection:
    """One curated path's codes, ranked by how many subjects carry them."""

    path: str
    label: str
    resource_type: str
    codes: list[TerminologyCode]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "label": self.label,
            "resourceType": self.resource_type,
            "codes": [
                {"system": c.system, "code": c.code, "display": c.display,
                 "subjects": c.subjects, "records": c.records}
                for c in self.codes
            ],
        }


def resource_types_sql(relation: str, value_col: str = RESOURCE_VALUE_COLUMN) -> str:
    """Which resource types this relation actually holds.

    Read from the DATA rather than assumed from the define's reads: a define may return something other
    than what it retrieved, and the package is selected per type. Cheap — one `DISTINCT` over a relation
    already keyed by subject.
    """
    return (f"SELECT DISTINCT {quote(value_col)}.resourceType AS resource_type "
            f"FROM {relation} WHERE {quote(value_col)}.resourceType IS NOT NULL")


def extraction_sql(relation: str, resource_type: str, paths: tuple[Element, ...],
                   *, value_col: str = RESOURCE_VALUE_COLUMN, limit_per_path: int = 25) -> str:
    """One statement counting every curated path's codes.

    ONE BRANCH PER PATH, `UNION ALL`, never nested laterals. Two `LATERAL VIEW explode`s over one row
    CROSS-PRODUCT: a Condition with 3 diagnosis codings and 2 categories would report 6 of each, and the
    error scales with the record rather than announcing itself. `flow.py` makes the same call for the
    same reason, and every branch projects identical columns so the union is shape-safe.

    Counts are fan-out immune by construction — `COUNT(DISTINCT __subject_id)` for subjects, never
    `COUNT(*)`, which on these relations reports row counts in the billions (`relation.py`).
    """
    if not paths:
        return ""
    branches = []
    for p in paths:
        codings = p.accessor.replace("{r}", quote(value_col))
        branches.append(
            f"SELECT {literal(p.path)} AS path, {literal(p.label)} AS label, "
            f"c.system AS system, c.code AS code, c.display AS display, "
            f"COUNT(DISTINCT {SUBJECT_ID}) AS subjects, COUNT(*) AS records "
            f"FROM {relation} LATERAL VIEW explode({codings}) t AS c "
            f"WHERE {quote(value_col)}.resourceType = {literal(resource_type)} AND c.code IS NOT NULL "
            f"GROUP BY c.system, c.code, c.display")
    # Ranked HERE rather than in the client: the tail is the point of the section (a value set of 170
    # codes firing on one), and a client-side sort over a truncated list would rank a window, not the
    # data. The per-path cap keeps a wide code system from crowding out the other sections.
    return (" UNION ALL ".join(branches)
            + f" ORDER BY path, subjects DESC LIMIT {int(limit_per_path) * max(len(paths), 1)}")


def extract_terminology(ref, columns: dict[str, str], *, eval_id: str, execute: Execute,
                        model: str = DEFAULT_MODEL) -> list[dict]:
    """The curated coded paths of one define's results, as wire dicts.

    TWO STATEMENTS, and the first one is not an optimisation. The packages are keyed per resource type,
    so the type has to be known before the branches can be built — and it is read from the DATA rather
    than assumed from the define's reads, because a define may return something other than what it
    retrieved. A relation holding two types gets both packages.

    ABSENT, NEVER EMPTY-AS-ZERO. A define whose value column is not the resource struct (a boolean or
    scalar define names its value column after itself) has no coded record to read, so it returns no
    sections at all — the pane then says nothing rather than drawing a section with nothing in it.
    """

    if RESOURCE_VALUE_COLUMN not in columns:
        return []  # not resource-valued: no record, no codes, nothing to say
    relation = result_relation(ref, eval_id)

    try:
        type_rows = execute(resource_types_sql(relation)) or []
    except Exception as e:  # noqa: BLE001 — a profile is never worth failing a page for
        log.info("terminology: could not read resource types for %s: %s", relation, e)
        return []

    sections: list[dict] = []
    for row in type_rows:
        resource_type = str(row[0]) if row and row[0] else ""
        paths = elements_for(model, resource_type, kind=KIND_CODING)
        if not paths:
            # An uncurated (model, type) is silence, not a guess — see `paths_for`.
            log.info("terminology: no package for %s/%s", model, resource_type)
            continue
        sql = extraction_sql(relation, resource_type, paths)
        try:
            rows = execute(sql) or []
        except Exception as e:  # noqa: BLE001
            log.info("terminology: extraction failed for %s/%s: %s", relation, resource_type, e)
            continue
        by_path: dict[str, TerminologySection] = {}
        for path, label, system, code, display, subjects, records in rows:
            section = by_path.get(path)
            if section is None:
                section = TerminologySection(path=path, label=label,
                                             resource_type=resource_type, codes=[])
                by_path[path] = section
            section.codes.append(TerminologyCode(
                system=system, code=code, display=display,
                subjects=int(subjects or 0), records=int(records or 0)))
        # A CURATED PATH THAT FOUND NOTHING IS STATED, not dropped. The package asserts this field is
        # worth looking at, so its emptiness is a finding ("no body site recorded on any of these") —
        # which is the opposite of the discovery design, and is exactly why curation earns the right
        # to say it (`SPEC-METRIC`: absence stated, never a zero).
        for p in paths:
            by_path.setdefault(p.path, TerminologySection(
                path=p.path, label=p.label, resource_type=resource_type, codes=[]))
        sections.extend(by_path[p.path].to_dict() for p in paths)
    return sections
