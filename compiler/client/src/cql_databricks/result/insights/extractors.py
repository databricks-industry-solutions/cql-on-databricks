"""DATA-INSIGHT-EXTRACTION — where the valuable values live on a record, per data model.

THE MECHANISM, IN ONE LINE. A curated declaration of `(data model, resource type) -> the elements worth
surfacing`, each carrying how to reach it in the emitted struct and what to call it on the page. The
queries are GENERATED from these; the catalog itself is not a query, which is why it can feed more than
one surface — the Terminology sections and the Demographics cuts are two extractions over one catalog,
and a mapping UI or a coverage report would be a third.

WHY CURATED AND NOT DISCOVERED. Measured on the emitted Condition struct: 4,390 characters carrying
eleven `coding: array<struct<…>>` sites, most administrative (`meta.security`, `meta.tag`, extensions).
The structural signature that finds them is treacherous in both directions — `Identifier` and
`Reference.assigner` carry `system`, and **`Quantity` carries `system` AND `code`**, where the code is a
UCUM unit rather than clinical terminology. Discovery therefore needs a curated EXCLUSION list to be
readable, and curating exclusions is strictly worse than curating inclusions: the same artifact to
maintain, and it fails by showing noise rather than by staying silent. Curation also gives every element
a HUMAN label instead of a field path, which is what a clinical reader is owed (`APP-AUDIENCE`).

COMPLETENESS IS A TEST, NOT A RUNTIME PATH (`test_terminology.py`): a coding site the emit writes that no
extractor names is REPORTED, so the catalog cannot go quietly stale when the emit changes shape.

THE MODEL IS PART OF THE KEY, NEVER ASSUMED. CQL is model-agnostic by construction — a library declares
`using FHIR version '4.0.1'` or `using QDM version '5.6'` — and QDM's fields are neither named nor shaped
like FHIR's. Keying on the resource type alone would make `Encounter` mean whichever model happened to
load first, which is the collision `SPEC-ANCHOR` already records for two libraries declaring one define
name. An uncurated `(model, type)` yields NOTHING rather than a guess.

NOT A PROBE (`SPEC-CLAUSE-PROBE`, rejected). Nothing here evaluates a sub-expression or re-applies a
filter. Every extraction reads values that are already sitting in a materialized relation, which is what
makes it a report about stored data rather than a second opinion about CQL semantics (`APP-DECOUPLE`).
"""

from __future__ import annotations

from dataclasses import dataclass

#: How an element's values are turned into buckets. The catalog states it because the SQL differs and
#: the difference is not inferable from the path.
KIND_CODING = "coding"    #: a CodeableConcept — explode its codings, one row per code
KIND_SCALAR = "scalar"    #: a single value per record — group it directly
KIND_BANDED = "banded"    #: a computed bucket (age from a birth date); the caller supplies the bands


@dataclass(frozen=True)
class Element:
    """One curated place a valuable value lives.

    ``accessor`` is a SQL fragment with ``{r}`` standing in for the record struct, written out rather
    than derived from ``path``: a singular ``CodeableConcept`` is ``{r}.code.coding`` while a REPEATING
    one is an array of them, so ``{r}.category.coding`` is ``array<array<Coding>>`` and needs
    ``flatten``. That shape is not inferable from the field name, and getting it wrong is a cast error
    at query time — exactly the kind of thing a curated entry should state plainly.
    """

    path: str
    #: The heading a reader sees. Clinical register, never the field name.
    label: str
    kind: str
    accessor: str
    #: Stable id for a stratum built from this element. Absent for coding elements, which are keyed by
    #: their path inside a section.
    id: str | None = None
    #: One line the pane prints under the block, where the value needs qualifying.
    note: str | None = None


def _cc(path: str, label: str) -> Element:
    """A singular CodeableConcept."""
    return Element(path=path, label=label, kind=KIND_CODING, accessor=f"{{r}}.{path}.coding")


def _cc_list(path: str, label: str) -> Element:
    """A REPEATING CodeableConcept — an array of them, so the codings need flattening."""
    return Element(path=path, label=label, kind=KIND_CODING,
                   accessor=f"flatten({{r}}.{path}.coding)")


def _scalar(eid: str, path: str, label: str, accessor: str, note: str | None = None) -> Element:
    return Element(path=path, label=label, kind=KIND_SCALAR, accessor=accessor, id=eid, note=note)


#: (data model -> resource type -> the elements worth extracting).
#:
#: Scoped to what eCQMs actually touch. A field is here because a reader has a reason to look at it, so
#: `meta.tag`, `meta.security` and generic extensions are deliberately absent.
EXTRACTORS: dict[str, dict[str, tuple[Element, ...]]] = {
    "FHIR": {
        # ── the record types a define returns, read for their terminology ──────────────────────────
        "Condition": (
            _cc("code", "Diagnosis"),
            _cc_list("category", "Category"),
            _cc("clinicalStatus", "Clinical status"),
            _cc("verificationStatus", "Verification status"),
            _cc("severity", "Severity"),
            _cc_list("bodySite", "Body site"),
        ),
        "Encounter": (
            _cc_list("type", "Encounter type"),
            _cc("serviceType", "Service type"),
            _cc("priority", "Priority"),
            _cc_list("reasonCode", "Reason"),
        ),
        "Observation": (
            _cc("code", "Observation"),
            _cc_list("category", "Category"),
            _cc("valueCodeableConcept", "Coded value"),
            _cc_list("interpretation", "Interpretation"),
            _cc("dataAbsentReason", "Data absent reason"),
        ),
        "Procedure": (
            _cc("code", "Procedure"),
            _cc_list("category", "Category"),
            _cc_list("reasonCode", "Reason"),
            _cc_list("bodySite", "Body site"),
        ),
        "MedicationRequest": (
            _cc("medicationCodeableConcept", "Medication"),
            _cc_list("category", "Category"),
            _cc_list("reasonCode", "Reason"),
        ),
        "Immunization": (
            _cc("vaccineCode", "Vaccine"),
            _cc("statusReason", "Status reason"),
        ),
        "DeviceRequest": (
            _cc("codeCodeableConcept", "Device"),
            _cc_list("reasonCode", "Reason"),
        ),
        "AllergyIntolerance": (
            _cc("code", "Substance"),
            _cc("clinicalStatus", "Clinical status"),
            _cc("verificationStatus", "Verification status"),
        ),
        # ── the SUBJECT, read for its demographics (APP-PROFILE-STRATA) ────────────────────────
        #
        # `age` is NOT here: it is `KIND_BANDED` and its bands depend on the measurement period, which
        # is a property of the evaluation rather than of the model. The caller composes it.
        # RACE AND ETHNICITY ARE NOT HERE, and the reason is the emitted schema rather than a choice.
        # US Core publishes them as NESTED extensions —
        # `extension[url=…us-core-race].extension[url=ombCategory].valueCoding` — and the extension
        # struct this pipeline writes is FLAT: measured against `demo_population.retrieve_patient_data`,
        # its fields are `url` plus the `value[x]` scalars, with no recursive `extension`. The accessor
        # was written, run, and failed with `No such struct field 'extension'`. So the two cuts
        # `insights/sample.py` fabricates stay fabricated and stay marked; recovering them needs the
        # emit to carry nested extensions, which is an engine-side question (`APP-DECOUPLE`), not a
        # cleverer accessor here.
        "Patient": (
            # A NOTE ONLY WHERE THERE IS SOMETHING TO SAY. The line under a cut used to be a uniform
            # "Read from the Patient binding", which every cut already implies and no reader acts on —
            # filler in the position a caveat belongs. Marital status gets none for the same reason.
            _scalar("sex", "gender", "Sex", "{r}.gender",
                    "Administrative gender as recorded, which is not necessarily clinical sex."),
            _scalar("marital", "maritalStatus", "Marital status",
                    "element_at({r}.maritalStatus.coding, 1).display"),
            _scalar("language", "communication.language", "Language",
                    "element_at({r}.communication, 1).language.text",
                    "The first recorded communication language, where a patient has several."),
            _scalar("state", "address.state", "State", "element_at({r}.address, 1).state",
                    "From the first recorded address, where a patient has several."),
            # ABSENCE IS NOT A STATEMENT OF LIFE. A NULL `deceased[x]` does not assert 'Living'.
            # FHIR is explicit that with no value "there is no statement on whether or not the
            # individual is deceased". NULL falls through to the Unknown bucket like every other cut.
            _scalar("deceased", "deceased", "Vital status",
                    "CASE WHEN {r}.deceasedBoolean = true OR {r}.deceasedDateTime IS NOT NULL "
                    "THEN 'Deceased' WHEN {r}.deceasedBoolean = false THEN 'Living' END",
                    "Absent means the record makes no statement, not that the patient is living."),
        ),
    },
}

#: The model a library is compiled against when nothing says otherwise. CQL's `using` declaration is the
#: authority; this is the default for callers that do not carry one through yet.
DEFAULT_MODEL = "FHIR"

#: The bucket every grouped extraction reports its missing values into.
#:
#: A BUCKET, NEVER A DROPPED ROW. A cohort where 40% have no recorded sex is a data-readiness finding,
#: and silently shrinking the bars turns that finding into a slightly smaller chart nobody questions.
#: `GROUP BY` puts NULLs in their own group, so this is a label for a row that already exists rather
#: than an extra query (`SPEC-METRIC`: absence is stated).
UNKNOWN_BIN = "Unknown"


def elements_for(model: str, resource_type: str, *, kind: str | None = None) -> tuple[Element, ...]:
    """The curated elements for one `(model, type)`, optionally of one kind only.

    EMPTY IS AN ANSWER. A model or type nobody has curated yields no elements, which reads on the page
    as "nothing recorded here" — never as an invented traversal of a struct this module does not
    understand.
    """
    found = EXTRACTORS.get(model, {}).get(resource_type, ())
    return tuple(e for e in found if kind is None or e.kind == kind)
