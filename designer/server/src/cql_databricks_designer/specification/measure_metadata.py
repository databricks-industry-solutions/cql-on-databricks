"""SPEC-MEASURE-METADATA — the Specification's AUTHORED prose, read from the FHIR Measure resource.

WHY THIS EXISTS. The Define lens narrates a measure so a clinician can review it without opening the
CQL. For an external measure that narration was entirely ours — AI-drafted prose about someone else's
logic — including for the handful of things the measure's author had *already written down and
published*. A measure's description, its rationale, the guideline it operationalises and its usage
guidance are all fields of the `Measure` resource the knowledge corpus reads (`KNOWLEDGE-LAKE-DIRECT`). Paraphrasing
them was strictly worse than quoting them: an interpretation where an assertion was available.

So the Overview is built from the resource FIRST, verbatim, `authored`, attributed to the steward
(`Measure.publisher`) and stamped with the exact `canonical|version` it was read from. Narration
fills only what the resource is silent about — typically Scope and Non-Goals, which no FHIR
field carries, and every Concept and Rule below the populations, which the resource says nothing
about at all.

WHAT IS NOT HERE. Nothing per-define: `Measure` has no vocabulary for "Has Example Condition". External
measures rarely carry a CQL `@description` outside their helper libraries, which this document renders
as transparent plumbing. The body of the document is narration's job and this module does not pretend
otherwise.

VERBATIM OR ABSENT. A field is quoted whole or omitted — never truncated, never summarised, never two
authored fields concatenated into one. The one transformation applied is DECODING the field's HTML
character references (``_markdown``), because the escaping is transport, not prose — undecoded, the
reader sees the literal ``&gt;= 20 years`` on the page.

WHAT THESE FIELDS ACTUALLY CONTAIN (observed in published measure resources):
There is NO markdown structure in them. A ``rationale`` can be a single run of thousands of characters
with **zero newlines**. The ``description``, ``usage``, ``clinicalRecommendationStatement`` and ``copyright``
fields are single-line too. No double spaces — the paragraph boundaries visible on a steward's published
page left no trace here to recover. The ``Measure.text.div`` field is no help: it renders the rationale
as a single ``<td class="content-container">`` just as flat. The client splits on blank lines, but
there are none. **Restoring the paragraphs is an INGESTION question, not a rendering one** — the prose
arrives flattened, and nothing downstream can honestly un-flatten it without deciding where the
author's breaks went.
"""
from __future__ import annotations

import html
from typing import Any

from cql_databricks_designer.specification.model import Citation, Intent, NarratedText, Overview, SpecificationOverlay

#: `Measure` field -> the `sourceTag` naming the slot its prose occupies (designer/docs/narrative-tags.md).
#: Recorded even though the prose did not come from a CQL tag: the tag names the SLOT, which is what
#: makes a resource-authored field and a CQL-authored one comparable.
DESCRIPTION = ("description", "description")
PURPOSE = ("purpose", "intent")
RATIONALE = ("rationale", "clinicalRationale")
RECOMMENDATION = ("clinicalRecommendationStatement", "clinicalRecommendation")
GUIDANCE = ("usage", "guidance")

#: SPEC-SCORING-METHODOLOGY — the scoring codes FHIR defines for ``Measure.scoring``
#: (http://terminology.hl7.org/CodeSystem/measure-scoring), which are also the XSD's ``ScoringKind``.
#: An unrecognized code is DROPPED rather than passed through: a downstream surface that cannot state
#: a scoring type it does not know renders nothing, and a typo reaching it as a string would make that
#: indistinguishable from a measure that declared nothing at all.
SCORING_CODES = frozenset({"proportion", "ratio", "continuous-variable", "cohort"})


def _scoring(resource: dict[str, Any]) -> str | None:
    """``Measure.scoring`` as a plain code, or None.

    SPEC-SCORING-METHODOLOGY — the published resource is the AUTHORITY for how a measure is scored, and
    it is normative FHIR rather than anything we derive. Until this landed the only source was
    ``Identity/@scoring`` on a hand-written Specification instance, which is a transcription of exactly
    this field: three libraries carry one, so every other measure had no scoring at all and the header
    printed the literal "proportion" over all of them regardless.

    The same argument as SPEC-DERIVED-APPENDIX for value-set OIDs — a hand-copied value goes stale
    silently, and the source of truth is one HTTP hop away.
    """
    scoring = resource.get("scoring")
    if not isinstance(scoring, dict):
        return None
    for coding in scoring.get("coding") or ():
        if not isinstance(coding, dict):
            continue
        code = _str(coding.get("code"))
        if code in SCORING_CODES:
            return code
    # `text` is the human label, not a code; only accept it where it happens to BE one.
    text = _str(scoring.get("text"))
    return text if text in SCORING_CODES else None


def authored_overlay(resource: dict[str, Any], *, library: str, version: str) -> SpecificationOverlay:
    """The authored half of a Specification, from one FHIR ``Measure`` resource.

    ``library``/``version`` are the CQL identity the document is keyed by; the resource's own
    ``url|version`` is the FHIR identity and travels on each field as ``sourceRef`` — the two differ
    routinely (FHIR ``1.0.000`` against CQL ``0.5.000``) and conflating them would misattribute the
    quotation.
    """
    author = _str(resource.get("publisher"))
    ref = _source_ref(resource)

    def prose(field_and_tag: tuple[str, str]) -> NarratedText | None:
        field, tag = field_and_tag
        text = _markdown(resource.get(field))
        if text is None:
            return None
        return NarratedText(text=text, provenance="authored", author=author, source_ref=ref,
                            source_tag=tag)

    intent = Intent(statement=prose(PURPOSE), clinical_rationale=prose(RATIONALE),
                    recommendation=prose(RECOMMENDATION))
    return SpecificationOverlay(
        library=library,
        version=version,
        title=_str(resource.get("title")),
        scoring=_scoring(resource),
        overview=Overview(
            description=prose(DESCRIPTION),
            # Scope and Non-Goals have no FHIR field. Absent here, not empty: narration supplies them.
            intent=intent if any((intent.statement, intent.clinical_rationale, intent.recommendation))
            else None,
            guidance=prose(GUIDANCE),
        ),
        copyright=_notice(resource, "copyright", author, ref),
        disclaimer=_notice(resource, "disclaimer", author, ref),
        citations=_citations(resource),
    )


def compose(authored: SpecificationOverlay | None,
            narration: SpecificationOverlay | None) -> SpecificationOverlay | None:
    """One Specification from the two sources of prose. AUTHORED WINS THE SLOT.

    Not a fallback path — the same precedence the CQL's own tags already have over narration
    (designer/docs/narrative-tags.md), extended to the other place a steward publishes prose. The
    losing text is DROPPED, not kept as a shadow copy: two versions of the same field is how the two
    drift, and a reader offered both has to adjudicate a document that should have decided for them.

    Returns None when neither source exists — the library simply has no Specification, and the
    document renders from the CQL alone.
    """
    if authored is None:
        return narration
    if narration is None:
        return authored

    a, n = authored.overview, narration.overview
    intent = Intent(
        statement=_pick(a.intent, n.intent, "statement"),
        clinical_rationale=_pick(a.intent, n.intent, "clinical_rationale"),
        recommendation=_pick(a.intent, n.intent, "recommendation"),
    )
    return SpecificationOverlay(
        library=authored.library,
        version=authored.version,
        title=authored.title or narration.title,
        # SPEC-SCORING-METHODOLOGY — AUTHORED WINS, as every other field here does. `Measure.scoring`
        # is normative FHIR published by the steward; `Identity/@scoring` on a narration instance is a
        # hand transcription of it, and three libraries have one. The order below was reversed while the
        # resource carried nothing into this model.
        #
        # THIS FUNCTION IS A FIELD-BY-FIELD REBUILD, which means every field added to the overlay has to
        # be added HERE TOO or it silently vanishes for exactly the measures that have both sources — the
        # ones anybody looks at. That is how `scoring` reached the client as `undefined` while the loader
        # was demonstrably setting it, and the same shape of defect as the document model dropping
        # `segments`. A spread would not fix it either: the whole point is that each field states which
        # source wins.
        scoring=authored.scoring or narration.scoring,
        generated=narration.generated,
        precedence=narration.precedence,
        overview=Overview(
            description=a.description or n.description,
            intent=intent if any((intent.statement, intent.clinical_rationale, intent.recommendation))
            else None,
            # No FHIR counterpart — narration owns the boundary outright.
            scope=n.scope,
            guidance=a.guidance or n.guidance,
            # The product's voice about this DOCUMENT; the resource has nothing to say about it.
            how_to_read=n.how_to_read,
        ),
        elements=narration.elements,
        library_overviews=narration.library_overviews,
        copyright=authored.copyright,
        disclaimer=authored.disclaimer,
        citations=authored.citations,
    )


def _pick(authored: Intent | None, narrated: Intent | None, field: str) -> NarratedText | None:
    return (getattr(authored, field, None) if authored else None) \
        or (getattr(narrated, field, None) if narrated else None)


def _notice(resource: dict[str, Any], field: str, author: str | None,
            ref: str | None) -> NarratedText | None:
    # `copyright` and `disclaimer` are `markdown` like the Overview fields and carry the same escaped
    # characters, so they decode by the same rule — a notice is quoted prose too.
    text = _markdown(resource.get(field))
    if text is None:
        return None
    return NarratedText(text=text, provenance="authored", author=author, source_ref=ref)


def _citations(resource: dict[str, Any]) -> list[Citation]:
    """`Measure.relatedArtifact` as citations, in resource order.

    An artifact with no readable text and no link is dropped: a row that says nothing is worse than a
    shorter list. `document.url` is read as well as `url` because eCQM content uses both.
    """
    out: list[Citation] = []
    for artifact in resource.get("relatedArtifact") or []:
        if not isinstance(artifact, dict):
            continue
        document = artifact.get("document")
        url = _str(artifact.get("url")) or (
            _str(document.get("url")) if isinstance(document, dict) else None)
        citation = Citation(
            label=_str(artifact.get("label")) or _str(artifact.get("display")),
            citation=_str(artifact.get("citation")),
            url=url,
        )
        if citation.label or citation.citation or citation.url:
            out.append(citation)
    return out


def _source_ref(resource: dict[str, Any]) -> str | None:
    """`canonical|version`, the FHIR reference form. Version alone or url alone is still useful."""
    url, version = _str(resource.get("url")), _str(resource.get("version"))
    if url and version:
        return f"{url}|{version}"
    return url or version


def _str(value: Any) -> str | None:
    """A non-blank string, or None. FHIR omits empty elements, but synced content does carry `""`."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _markdown(value: Any) -> str | None:
    """A `markdown` field, with its HTML character references DECODED.

    NOT A CONTRACTION OF "VERBATIM" — the opposite. The published fields carry HTML entities where the
    prose contains characters that are markup in HTML: a `rationale` can hold ``&gt;= 20 years of age``
    and ``&lt;120 mmHg``. We render this text as text, so React escapes
    the ampersand and the reader sees the literal string ``&gt;= 20 years of age`` on the page. The
    steward wrote ">=", the page showed "&gt;=", and quoting them accurately is the entire reason this
    module prefers their words to a paraphrase.

    Decoding the transport encoding is not editing the prose. What stays forbidden is unchanged: no
    truncation, no summarising, no reflowing, no concatenating two fields.

    THIS DOES NOT RECOVER PARAGRAPHS, and the distinction matters because it is the thing readers ask
    about. The facts: a published `rationale` can be thousands of characters holding ZERO newlines. The fields
    `description`, `usage`, `clinicalRecommendationStatement` and `copyright` are all single-line too.
    There are no double spaces, so the paragraph boundaries left no trace to recover. `Measure.text.div`
    does not carry them either — the narrative renders the rationale into one `<td class="content-container">`
    as an unbroken run. The breaks a reader sees on a steward's site come from an artifact we do not ingest;
    recovering them is an INGESTION question, not a rendering one.
    """
    text = _str(value)
    return html.unescape(text) if text is not None else None
