"""SPEC-MEASURE-METADATA — the Specification's authored prose, quoted from the FHIR Measure resource.

The claim under test is narrow and load-bearing: what the steward published reaches the page AS THEY
WROTE IT, attributed to them.

The fixture is a synthetic FHIR `Measure` shaped the way published measures are, including the fields they
often leave out (no `purpose`).
"""
from __future__ import annotations

import pytest

from cql_databricks_designer.specification.measure_metadata import authored_overlay

LIBRARY = "HypertensionBPControl"
VERSION = "1.0.0"

RESOURCE = {
    "url": "http://example.org/fhir/Measure/HypertensionBPControl",
    "version": "1.0.0",
    "title": "Hypertension: Blood Pressure Below 140/90",
    "publisher": "Example Measure Steward",
    "description": "Adults 18 to 85 with hypertension whose most recent blood pressure in the measurement period is "
                   "below 140/90 mmHg.",
    # No `purpose` — real eCQMs routinely omit it, and that is the case that leaves narration standing.
    "rationale": "Controlling high blood pressure lowers the risk of heart attack, stroke and kidney disease.\n\n"
                 "Hypertension is a leading risk factor for cardiovascular morbidity and mortality.",
    "clinicalRecommendationStatement": "Regular blood pressure monitoring and management of hypertension improves outcomes.",
    "usage": "Hypertension diagnosis that starts on or before June 30 of the measurement period counts; qualifying visits are outpatient, telehealth, or preventive.",
    # SPEC-SCORING-METHODOLOGY — how the measure is scored, as published. Normative FHIR, and the
    # authority for a field that had no source but a hand transcription on three Specification
    # instances.
    "scoring": {
        "coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/measure-scoring",
            "code": "proportion",
            "display": "Proportion",
        }],
    },
    "copyright": "Example copyright notice for a synthetic measure.",
    "disclaimer": "This synthetic measure is not a clinical guideline.",
    "relatedArtifact": [
        {"type": "citation", "label": "Example 2024", "citation": "Example A, et al. 2024 Guideline.",
         "url": "https://example.org/guideline"},
        {"type": "documentation", "display": "Example Guidance"},
        {"type": "documentation"},  # nothing readable and no link — dropped
    ],
}


@pytest.fixture(scope="module")
def authored():
    return authored_overlay(RESOURCE, library=LIBRARY, version=VERSION)


def test_prose_is_quoted_verbatim(authored):
    """Not summarised, not reflowed, not truncated. The paragraph break in `rationale` is the
    author's and survives to the client, which renders it as a paragraph (`markdown.tsx`)."""
    assert authored.overview.description.text == RESOURCE["description"]
    assert authored.overview.intent.clinical_rationale.text == RESOURCE["rationale"]
    assert "\n\n" in authored.overview.intent.clinical_rationale.text


def test_html_character_references_are_decoded():
    """The published fields escape the characters that are markup in HTML, and we render this text AS
    text — so an undecoded field puts the literal string `&gt;= 20 years` in front of the reader.

    Published resources hold text such as `&gt;= 20 years of age` and `&lt;120 mmHg`. Decoding the transport encoding is not editing
    the prose; the verbatim rule above is unchanged and still asserted.
    """
    resource = dict(RESOURCE)
    resource["rationale"] = "many patients &gt;= 40 years had a reading above the threshold"
    resource["copyright"] = "Trademarks are indicated by (R) &amp; [R]."
    overlay = authored_overlay(resource, library=LIBRARY, version=VERSION)

    assert overlay.overview.intent.clinical_rationale.text.endswith(">= 40 years had a reading above the threshold")
    assert "&gt;" not in overlay.overview.intent.clinical_rationale.text
    # A notice is quoted prose too, and decodes by the same rule.
    assert overlay.copyright.text == "Trademarks are indicated by (R) & [R]."


def test_decoding_does_not_invent_paragraphs():
    """THE PARAGRAPHS ARE NOT RECOVERABLE HERE, and this pins that we do not pretend otherwise.

    A published `rationale` can be one run of thousands of characters with zero newlines, and
    `Measure.text.div` renders it into a single `<td>` just as flat. Splitting a wall of text on
    sentence boundaries would be us deciding where the author's paragraphs went, which is exactly the
    interpretation this module exists to avoid. If the breaks come back, they come back from INGESTION.
    """
    resource = dict(RESOURCE)
    resource["rationale"] = "First thought. Second thought. Third thought."
    overlay = authored_overlay(resource, library=LIBRARY, version=VERSION)
    assert overlay.overview.intent.clinical_rationale.text == resource["rationale"]
    assert "\n" not in overlay.overview.intent.clinical_rationale.text


def test_every_quoted_field_says_whose_words_it_is(authored):
    """`authored` with nobody behind it is not a claim a reader can weigh, and the point of quoting
    the steward rather than paraphrasing them is lost if the page cannot say who the steward is."""
    for prose in (authored.overview.description, authored.overview.guidance,
                  authored.overview.intent.clinical_rationale, authored.overview.intent.recommendation,
                  authored.copyright, authored.disclaimer):
        assert prose.provenance == "authored"
        assert prose.author == "Example Measure Steward"
        # `canonical|version` — the FHIR identity, which differs from the CQL one and must not be
        # confused with it.
        assert prose.source_ref == "http://example.org/fhir/Measure/HypertensionBPControl|1.0.0"


def test_fields_land_in_the_slot_their_tag_names(authored):
    """SPEC-NARRATIVE-TAGS — the tag names the slot even when the prose came from the resource rather
    than a CQL tag. That correspondence is what makes the two sources comparable."""
    assert authored.overview.description.source_tag == "description"
    assert authored.overview.intent.clinical_rationale.source_tag == "clinicalRationale"
    assert authored.overview.intent.recommendation.source_tag == "clinicalRecommendation"
    assert authored.overview.guidance.source_tag == "guidance"
    # `Measure.usage` is the AUTHOR's guidance about the measure — not "how to read this document".
    assert authored.overview.how_to_read is None


def test_absent_fields_are_absent(authored):
    """The resource has no `purpose` and no vocabulary at all for scope or non-goals. Absent, not
    empty: a blank Intent statement would claim the author declined to state one."""
    assert authored.overview.intent.statement is None
    assert authored.overview.scope is None


def test_citations_drop_only_the_unreadable_ones(authored):
    """A citation row with no text and no link says nothing; the two that carry something stay."""
    assert [c.label for c in authored.citations] == ["Example 2024", "Example Guidance"]
    assert authored.citations[0].url == "https://example.org/guideline"


# ── SPEC-SCORING-METHODOLOGY ──────────────────────────────────────────────────────────────────────
# THE FAILURE THESE EXIST FOR. Scoring had exactly one source: `Identity/@scoring` on a hand-written
# Specification instance, and three libraries have one. Every other measure therefore declared no
# scoring at all, while the document header printed the literal string "proportion" over all of them —
# a false statement in a clinical document, carrying the same authority as the FHIR version beside it.
# `Measure.scoring` is normative, published, and one field away.


def test_scoring_is_read_from_the_published_resource(authored):
    assert authored.scoring == "proportion"


def test_an_unrecognized_scoring_code_is_dropped_rather_than_passed_through():
    # A surface that cannot state a scoring type renders nothing for it, so a typo arriving as a string
    # would be indistinguishable from a measure that declared none — except that it would also break
    # every equality check downstream. Absent and unrecognized are ONE answer.
    resource = {**RESOURCE, "scoring": {"coding": [{"code": "percentage"}]}}
    assert authored_overlay(resource, library=LIBRARY, version=VERSION).scoring is None


def test_a_measure_that_declares_no_scoring_gets_none():
    resource = {k: v for k, v in RESOURCE.items() if k != "scoring"}
    assert authored_overlay(resource, library=LIBRARY, version=VERSION).scoring is None


def test_all_four_scoring_kinds_survive_the_read():
    for code in ("proportion", "ratio", "continuous-variable", "cohort"):
        resource = {**RESOURCE, "scoring": {"coding": [{"code": code}]}}
        assert authored_overlay(resource, library=LIBRARY, version=VERSION).scoring == code
