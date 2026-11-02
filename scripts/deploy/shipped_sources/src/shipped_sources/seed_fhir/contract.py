"""Pure, Spark-free naming and tagging helpers for the FHIR mounts the shipped data sources land (DATA-CONTRACT).

Plain functions of their inputs, unit-testable without a Spark session or a Compiler Service. The Spark
I/O lives in `seeder`.
"""
from __future__ import annotations

# Known code systems per FHIR resource type — the coverage declaration for `cql.concepts`
# (DATA-RESOLUTION). Over-declaration is correctness-safe (membership is re-checked above the mount;
# over-inclusion is only slower, never wrong), and today the engine's best-match resolver is DESIGN-only,
# so these primarily drive discovery + observability. A type not listed simply omits `cql.concepts`
# (unknown coverage is KEPT by the resolver, never pruned).
KNOWN_SYSTEMS: dict[str, tuple[str, ...]] = {
    "Observation": ("http://loinc.org",),
    "Condition": ("http://snomed.info/sct",),
    "Procedure": ("http://snomed.info/sct",),
    "Encounter": ("http://snomed.info/sct",),
    "Immunization": ("http://hl7.org/fhir/sid/cvx",),
    "MedicationRequest": ("http://www.nlm.nih.gov/research/umls/rxnorm",),
    "MedicationAdministration": ("http://www.nlm.nih.gov/research/umls/rxnorm",),
    "DiagnosticReport": ("http://loinc.org",),
    "AllergyIntolerance": ("http://snomed.info/sct",),
}


def valueset_id_from_filename(stem: str) -> str:
    """The `valueset_id` a value-set expansion file contributes to the tall `valuesets` table. The engine
    keys membership by the value set URL's LAST SEGMENT (`valueSetId.split('/').last`) and names the
    expansion file `valueset-<segment>.ndjson` (SparkTerminology). So a file `valueset-diabetes.ndjson`
    keys rows as `diabetes` — exactly what the compiled `WHERE valueset_id = 'diabetes'` filter selects.
    A stem without the `valueset-` prefix is used verbatim (already the canonical id)."""
    return stem[len("valueset-"):] if stem.startswith("valueset-") else stem


def cql_tblproperties(fhir_type: str, *, valid_from: str | None = None,
                      valid_to: str | None = None) -> dict[str, str]:
    """The `cql.*` coverage tags stamped on a `retrieve_<Type>` table (DATA-RESOLUTION). `cql.fhir.type`
    is always present (the type this view supplies); `cql.concepts` declares known code-system coverage
    when we know it; `cql.valid.*` bounds the validity window when supplied (omitted = unknown = never
    pruned). Values follow the doc's format: `system:<url>` for a code system."""
    props: dict[str, str] = {"cql.fhir.type": fhir_type}
    systems = KNOWN_SYSTEMS.get(fhir_type)
    if systems:
        props["cql.concepts"] = ",".join(f"system:{s}" for s in systems)
    if valid_from:
        props["cql.valid.from"] = valid_from
    if valid_to:
        props["cql.valid.to"] = valid_to
    return props
