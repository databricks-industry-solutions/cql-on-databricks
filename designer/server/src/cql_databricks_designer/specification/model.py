"""SPEC-XSD / SPEC-NARRATIVE-TAGS — the Specification's PROSE, as dataclasses.

These MIRROR ``schema/specification.xsd``; when they disagree with it, they are wrong. Only the
PROSE half of the schema is modelled here, and that is deliberate: structure, polarity, metrics,
source links and the Appendix are derived from the ELM in the client (``specDocument.ts``), so a
server-side copy of them would be a second derivation able to disagree with the first.

What the server owns is what the ELM cannot answer — intent, rationale, scope, guidance — keyed by
``(library, define)`` so the client can join it onto the structure it already derived.

TWO SOURCES, ONE SHAPE (``SPEC-MEASURE-METADATA``). Prose reaches this model from the measure's
published FHIR ``Measure`` resource (``authored`` by the steward — ``measure_metadata.py``) and from
a narration instance (``narrated`` — ``parser.py``). They compose in ``compose()``,
authored wins the slot, and every field says which it is: ``provenance`` + ``author`` + ``source_ref``.

Wire shape is camelCase (``coding-standards.md``); ``None`` fields are omitted so an absent narration
is absent on the wire rather than a null the renderer has to special-case.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The narration triple every element carries, in render order. Named for the CQL annotation tags
# they align to (designer/docs/narrative-tags.md) — the element name, the tag name and the rendered heading
# are one word by construction.
TRIPLE = ("description", "rationale", "guidance")


@dataclass
class ListItem:
    """One line of a rule set. Carries its own citation, which is what makes the list countable.

    SPEC-READ-REF — and its own `segments`, for the same reason `NarratedText` has them: a bullet is
    where a read is most often NAMED ("recorded on the problem list or as an encounter diagnosis"), so a
    binding that only worked in the lead sentence would miss the place it is actually needed. Emitted
    only when the item holds something that is not prose; `text` alone fully describes the rest.
    """

    text: str
    source_span: str | None = None
    segments: list["Segment"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"text": self.text}
        if self.source_span is not None:
            out["sourceSpan"] = self.source_span
        if any(not isinstance(s, TextRun) for s in self.segments):
            out["segments"] = [s.to_dict() for s in self.segments]
        return out


@dataclass
class NarratedList:
    """SPEC-NARRATIVE-DEVICES — Include / Exclude blocks, or a plain rule set."""

    items: list[ListItem] = field(default_factory=list)
    kind: str = "plain"
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"block": "list", "kind": self.kind,
                               "items": [i.to_dict() for i in self.items]}
        if self.title is not None:
            out["title"] = self.title
        return out


@dataclass
class NarratedTable:
    """SPEC-NARRATIVE-DEVICES — a code table. `check_against` names what the rows must match, so the
    diff that keeps them honest can be written without another schema change."""

    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    row_spans: list[str | None] = field(default_factory=list)
    title: str | None = None
    check_against: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"block": "table", "columns": self.columns, "rows": self.rows}
        if any(self.row_spans):
            out["rowSpans"] = self.row_spans
        for key, value in (("title", self.title), ("checkAgainst", self.check_against)):
            if value is not None:
                out[key] = value
        return out


@dataclass
class TextRun:
    """A stretch of plain prose inside a narrated element. One half of `NarratedText.segments`."""

    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "text", "text": self.text}


@dataclass
class MetricReference:
    """SPEC-METRIC-REF — a figure CITED in prose, resolved by the renderer against the metric set.

    The instance carries this instead of a number, and that is the whole point: a narrator who may only
    cite a figure cannot state a wrong one, and a document whose figures are references does not go
    stale when the next run moves them. `grain` picks which of the metric's two grains is meant
    (`SPEC-METRIC`); `rendering` picks whether the sentence wants the count or its share of the base.

    No `provenance`. A figure's provenance belongs to the run that produced it and travels on the
    metric payload; a second copy on the prose could disagree with it.
    """

    library: str
    define: str
    grain: str = "subject"
    rendering: str = "count"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "metric", "library": self.library, "define": self.define,
                "grain": self.grain, "as": self.rendering}


@dataclass
class ReadReference:
    """SPEC-READ-REF — a phrase in the prose that NAMES A READ, bound to the retrieve it names.

    The instance carries the BINDING and the phrase; it never carries a count. The renderer resolves
    the binding against the retrieve model derived from the ELM (`specRetrieves.ts`) — the same model
    the CQL's own retrieve tokens resolve through — so a chip in the prose and a token in the code can
    never disagree about which read they mean.

    `text` is the PROSE, not the read's name: the sentence has to go on reading as a sentence, and
    "the problem list" is what belongs in a clinical paragraph. `profile` is the identifier, matched
    against the `templateId` the compiler emits, and `type` is the fallback for an unprofiled read.
    A binding that resolves to nothing renders as plain prose — degraded, never wrong.
    """

    text: str
    profile: str | None = None
    resource_type: str | None = None
    #: SPEC-INLINE-ANCHOR — the line of the enclosing define where this read is issued.
    source_span: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": "read", "text": self.text}
        if self.profile is not None:
            out["profile"] = self.profile
        if self.resource_type is not None:
            out["type"] = self.resource_type
        if self.source_span is not None:
            out["sourceSpan"] = self.source_span
        return out


@dataclass
class DefineReference:
    """SPEC-DEFINE-REF — a phrase in the prose that NAMES ANOTHER ELEMENT, bound to the define it names.

    THE GAP THIS CLOSES. `ConceptRef` / `RuleRef` have been in `SPEC-XSD` since the first draft — the
    schema comment says today's renderer "scans prose for known define names, a heuristic this element
    exists to retire" — and until now the loader FLATTENED them to plain text, so no instance used one
    and the retirement never happened. The consequence was visible on the page: *example condition
    diagnosis* was a followable reference inside a CQL block and inert prose in the sentence that
    explained it, which is exactly backwards for a document whose whole premise is that the reader
    should not need the code (`SPEC-EXPLAIN-ACCOUNTING`).

    THE SAME SHAPE AS `ReadReference`, deliberately. `text` is the PROSE — the words a clinician reads,
    which are rarely the define's own name ("example condition diagnosis" for
    `Example Condition Diagnosis`, but also "the encounter during period" for `Encounters During Period`) —
    and the binding is library-qualified because two libraries may declare one name. A binding that
    resolves to nothing renders as plain prose: degraded, never wrong.

    `kind` distinguishes a Concept from a Rule for the renderer's type chip, and it is the ELEMENT KIND
    the narrator asserted rather than anything derived. Where it disagrees with the document's own
    structure the document wins — this is a pointer, not a second definition.
    """

    text: str
    library: str
    define: str
    kind: str = "concept"
    #: SPEC-INLINE-ANCHOR — the reference SITE's line, where the phrase names something this define
    #: actually reaches. Absent for a transitive mention, whose anchor is the reference itself.
    source_span: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": "define", "text": self.text, "library": self.library,
                               "define": self.define, "elementKind": self.kind}
        if self.source_span is not None:
            out["sourceSpan"] = self.source_span
        return out


@dataclass
class ParameterReference:
    """SPEC-PARAMETER-REF — an execution parameter's VALUE cited in prose.

    The only citation here whose value comes from the RUN'S INPUTS rather than from the logic or its
    results, which is what makes it the one that resolves with no data source attached: a parameter is
    selected in the compute pill (`APP-EXEC-PARAMS`), not computed. So *the 2026 measurement period*
    reads the same in Static as in Live.

    It carries the CQL parameter's NAME and never its value, for the reason `MetricReference` carries
    neither: a narrator who types "2026" is wrong the moment anyone steps the period.
    """

    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "parameter", "name": self.name}


@dataclass
class TerminologyReference:
    """SPEC-TERMINOLOGY-REF — a phrase in the prose that NAMES A VALUE SET or a DIRECT REFERENCE CODE.

    The last of `SPEC-EXPLAIN-ACCOUNTING`'s outward coverage, and arguably the one a clinical reviewer
    wanted most: terminology is what decides which patients match, and it was the one linkable thing
    accounted for in PROSE ONLY. The OID sat in the Appendix with no way to get there from the sentence
    naming it, while the same name inside the rendered CQL was followable.

    ONE TYPE FOR BOTH KINDS, DERIVED NOT DECLARED. Which of the two a name is, is a fact the ELM already
    states, so the renderer resolves it out of the Appendix and labels it. Unlike `DefineReference.kind`
    — Concept-versus-Rule is an assertion about the document that a narrator makes; valueset-versus-code
    is an identifier the compiler established, and writing one of those is what `SPEC-EXPLAIN-INPUTS`
    forbids.

    `text` is the PROSE and `name` is the DECLARATION. Library-qualified, because value-set names are
    not unique across an include closure, and unqualified is dropped rather than guessed: a link to the
    wrong OID is a clinical error, not a navigation one. It never carries the OID — that is derived
    (`SPEC-DERIVED-APPENDIX`), and a typed one is wrong the moment the binding is rebound.
    """

    text: str
    library: str
    name: str
    #: SPEC-INLINE-ANCHOR — the line where this define USES the terminology, never the header line that
    #: declares it: the declaration is what the reference already leads to.
    source_span: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": "terminology", "text": self.text,
                               "library": self.library, "name": self.name}
        if self.source_span is not None:
            out["sourceSpan"] = self.source_span
        return out


#: One piece of a narrated element's inline content, in document order.
Segment = (TextRun | MetricReference | ReadReference | DefineReference | ParameterReference
           | TerminologyReference)


@dataclass
class NarratedText:
    """One prose element. `provenance` is REQUIRED by the schema and load-bearing in the UI: prose a
    human published in the CQL (`authored`) and prose interpreted about it (`narrated`) look
    different on the page, so a reader never has to guess whose words they are reading."""

    #: The PLAIN-TEXT projection — a nav summary, a catalogue row, a title attribute. Derived from
    #: `segments` in one place so the two cannot disagree; a cited figure reads as "—" here, because a
    #: one-line summary is not the place to resolve one.
    text: str
    provenance: str = "narrated"
    #: WHO asserted it. Set for `authored` prose — "authored" with nobody behind it is not a claim
    #: a reader can weigh.
    author: str | None = None
    #: WHERE it was quoted from, `canonical|version`. What makes a verbatim quotation checkable.
    source_ref: str | None = None
    method: str | None = None
    review_state: str | None = None
    reviewed_by: str | None = None
    reviewed_on: str | None = None
    #: The CQL annotation tag this prose belongs in — the machine-readable statement of alignment.
    source_tag: str | None = None
    #: SPEC-NARRATIVE-DEVICES — the lines of the define's CQL this prose is ABOUT ("2-5").
    source_span: str | None = None
    #: Device blocks following the lead prose, in document order.
    blocks: list[NarratedList | NarratedTable] = field(default_factory=list)
    #: SPEC-METRIC-REF — the inline content, when some of it is not prose. Emitted ONLY when the element
    #: cites a figure: prose with nothing to resolve is fully described by `text`, and shipping a
    #: single-run copy of every sentence would double the payload to say the same thing twice. The
    #: renderer normalises the absent case to one text run, so it still has exactly one path.
    segments: list[Segment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"text": self.text, "provenance": self.provenance}
        if self.blocks:
            out["blocks"] = [b.to_dict() for b in self.blocks]
        # Emitted when ANY segment is not plain prose. Was `MetricReference` alone; `ReadReference`
        # (SPEC-READ-REF) is the second thing a sentence can hold that `text` does not describe, and
        # testing for one kind by name is how the next one silently fails to ship.
        if any(not isinstance(s, TextRun) for s in self.segments):
            out["segments"] = [s.to_dict() for s in self.segments]
        if self.source_span is not None:
            out["sourceSpan"] = self.source_span
        for key, value in (
            ("author", self.author),
            ("sourceRef", self.source_ref),
            ("method", self.method),
            ("reviewState", self.review_state),
            ("reviewedBy", self.reviewed_by),
            ("reviewedOn", self.reviewed_on),
            ("sourceTag", self.source_tag),
        ):
            if value is not None:
                out[key] = value
        return out


@dataclass
class Narration:
    """The narration triple (+ title) for one element — a population, a Concept or a Rule.

    All four are optional. A define with none is rendered under its own name: degraded, never wrong,
    and never filled in with a guess (`SPEC-CONCEPTS`)."""

    title: NarratedText | None = None
    description: NarratedText | None = None
    rationale: NarratedText | None = None
    guidance: NarratedText | None = None

    def is_empty(self) -> bool:
        return not any((self.title, self.description, self.rationale, self.guidance))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in (
            ("title", self.title), ("description", self.description),
            ("rationale", self.rationale), ("guidance", self.guidance),
        ):
            if value is not None:
                out[key] = value.to_dict()
        return out


@dataclass
class Intent:
    """`Overview/Intent` — WHY the measure exists (`@intent`), the clinical reasoning behind it
    (`@clinicalRationale`), and the guideline it operationalises (`@clinicalRecommendation`). Three
    fields, because they are separately authored and reviewed by different people."""

    statement: NarratedText | None = None
    clinical_rationale: NarratedText | None = None
    recommendation: NarratedText | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in (("statement", self.statement),
                           ("clinicalRationale", self.clinical_rationale),
                           ("recommendation", self.recommendation)):
            if value is not None:
                out[key] = value.to_dict()
        return out


@dataclass
class ScopeNonGoals:
    """`Overview/ScopeNonGoals` — the boundary. `non_goals` is a LIST because each non-goal is cited,
    reviewed and struck out on its own; a semicolon-separated blob cannot be."""

    in_scope: NarratedText | None = None
    non_goals: list[NarratedText] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.in_scope is not None:
            out["inScope"] = self.in_scope.to_dict()
        if self.non_goals:
            out["nonGoals"] = [n.to_dict() for n in self.non_goals]
        return out


@dataclass
class Overview:
    description: NarratedText | None = None
    intent: Intent | None = None
    scope: ScopeNonGoals | None = None
    #: The measure author's own "how to read it" (`@guidance`, `Measure.usage`) — distinct from
    #: `how_to_read`, which is about this DOCUMENT and is the product's voice.
    guidance: NarratedText | None = None
    how_to_read: NarratedText | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.description is not None:
            out["description"] = self.description.to_dict()
        if self.intent is not None and (intent := self.intent.to_dict()):
            out["intent"] = intent
        if self.scope is not None and (scope := self.scope.to_dict()):
            out["scope"] = scope
        if self.guidance is not None:
            out["guidance"] = self.guidance.to_dict()
        if self.how_to_read is not None:
            out["howToRead"] = self.how_to_read.to_dict()
        return out


@dataclass
class Citation:
    """One `Measure.relatedArtifact` — the evidence the measure cites, carried one per artifact so a
    reader can follow a single claim."""

    label: str | None = None
    citation: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in
                (("label", self.label), ("citation", self.citation), ("url", self.url))
                if v is not None}


@dataclass
class SpecificationOverlay:
    """One measure's narration, keyed for joining onto the client-derived structure.

    `elements` is flat and keyed ``"<library>::<define>"``. Flat because the CLIENT decides whether a
    define is a Concept, a Rule or a hoisted Common rule — that is `SPEC-PLACEMENT`, derived from the
    ELM graph — and a server-side nesting would be a second opinion on placement able to contradict
    it. The overlay says what a define MEANS; it does not say where the define goes.
    """

    library: str
    version: str
    title: str | None = None
    #: SPEC-SCORING-METHODOLOGY — `Identity/@scoring`, the DECLARED scoring type. The document explains
    #: how a measure is scored from this rather than inferring it from which populations exist: the
    #: inference cannot tell a proportion from a ratio, and a ratio rendered with the proportion formula
    #: reads authoritative and is wrong. Absent means the document says nothing about scoring.
    scoring: str | None = None
    generated: str | None = None
    precedence: str | None = None
    overview: Overview = field(default_factory=Overview)
    #: "<library>::<define>" -> that define's narration triple.
    elements: dict[str, Narration] = field(default_factory=dict)
    #: Library name -> its one-line catalogue overview (SPEC-DEFINITIONS).
    library_overviews: dict[str, NarratedText] = field(default_factory=dict)
    #: SPEC-LICENSE — the steward's terms, verbatim. Displayed with the document, never summarised.
    copyright: NarratedText | None = None
    disclaimer: NarratedText | None = None
    citations: list[Citation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "library": self.library,
            "version": self.version,
            "overview": self.overview.to_dict(),
            "elements": {k: v.to_dict() for k, v in self.elements.items()},
            "libraryOverviews": {k: v.to_dict() for k, v in self.library_overviews.items()},
        }
        for key, value in (("title", self.title), ("scoring", self.scoring),
                           ("generated", self.generated), ("precedence", self.precedence)):
            if value is not None:
                out[key] = value
        for key, prose in (("copyright", self.copyright), ("disclaimer", self.disclaimer)):
            if prose is not None:
                out[key] = prose.to_dict()
        if self.citations:
            out["citations"] = [c.to_dict() for c in self.citations]
        return out


def element_key(library: str, define: str) -> str:
    """SPEC-ANCHOR — the join key, ALWAYS library-qualified. Two libraries may declare one name, so
    an unqualified key attaches the wrong prose to the wrong define."""
    return f"{library}::{define}"
