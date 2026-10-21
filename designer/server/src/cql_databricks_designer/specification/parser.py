"""APP-NARRATIVE-OVERLAY — parse an XSD-conformant Specification instance into the wire overlay.

The instance is the shape a narration generator emits: ``schema/specification.xsd`` is the normative
contract for what a Specification IS (`SPEC-XSD`), so the wire contract does not move when the source of
the prose does. This module only parses; generating, validating and caching the instance is the
overlay's.

NARRATION ONLY (`SPEC-DERIVED-APPENDIX`). An instance carries prose and nothing else. Value-set OIDs,
direct reference codes and parameters are derived from the ELM in the client: prose can be wrong and a
reader can see that it is, but a restated OID goes stale silently and nothing catches it.

Parsing is stdlib ElementTree. A malformed instance raises ``ElementTree.ParseError``: the caller decides
what a failed narration means, and serving half a document is never it.
"""
from __future__ import annotations

import logging
from xml.etree import ElementTree

from cql_databricks_designer.specification.model import (
    DefineReference, Intent, ListItem, MetricReference, NarratedList, NarratedTable, NarratedText,
    ParameterReference,
    Narration,
    ReadReference,
    Overview, ScopeNonGoals, Segment, SpecificationOverlay, TerminologyReference, TextRun,
    element_key,
)

log = logging.getLogger("cql_databricks_designer.specification")

NS = "https://cql-databricks.databricks.com/spec/1.0"


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def parse_specification(text: str) -> SpecificationOverlay:
    """The overlay one Specification instance states. Raises ``ElementTree.ParseError`` on malformed XML."""
    return _parse(ElementTree.fromstring(text))


def _parse(root: ElementTree.Element) -> SpecificationOverlay:
    identity = root.find(_q("Identity"))
    library = identity.get("library", "") if identity is not None else ""
    version = identity.get("version", "") if identity is not None else ""
    title_el = identity.find(_q("Title")) if identity is not None else None

    provenance = root.find(_q("Provenance"))
    precedence = provenance.find(_q("Precedence")) if provenance is not None else None

    overlay = SpecificationOverlay(
        library=library,
        version=version,
        title=_text(title_el),
        scoring=identity.get("scoring") if identity is not None else None,
        generated=provenance.get("derivedAt") if provenance is not None else None,
        precedence=_text(precedence),
        overview=_overview(root.find(_q("Overview"))),
    )

    # Populations, their Concepts and every Rule beneath either — flattened to (library, define).
    # Placement is the CLIENT's determination (SPEC-PLACEMENT); the overlay only says what a define
    # means, so the nesting in the instance is for human readability and is discarded here.
    criteria = root.find(_q("PopulationCriteria"))
    if criteria is not None:
        for population in criteria.findall(_q("Population")):
            _collect_scope(population, population.get("library", library), overlay)
    common = root.find(_q("Common"))
    if common is not None:
        _collect_scope(common, library, overlay)

    # Only the per-library overview is read here. A catalogue ROW's one-liner is the same define's
    # `description` from the index above — one narration per define, reused wherever the define
    # appears, so the catalogue and the criteria sections cannot state different things about it.
    definitions = root.find(_q("Definitions"))
    if definitions is not None:
        for lib in definitions.findall(_q("Library")):
            name = lib.get("name")
            overview = _narrated(lib.find(_q("Overview")))
            if name and overview:
                overlay.library_overviews[name] = overview

    return overlay


def _collect_scope(scope: ElementTree.Element, library: str, overlay: SpecificationOverlay) -> None:
    """One Population (or Common) and everything under it, added to the flat element index."""
    define = scope.get("define")
    if define:
        _add(overlay, scope.get("library", library), define, _narration(scope))
    for concept in scope.findall(_q("Concept")):
        concept_lib = concept.get("library", library)
        concept_define = concept.get("define")
        if concept_define:
            _add(overlay, concept_lib, concept_define, _narration(concept))
        for rule in concept.findall(_q("Rule")):
            _add_rule(rule, concept_lib, overlay)
    for rule in scope.findall(_q("Rule")):
        _add_rule(rule, library, overlay)


def _add_rule(rule: ElementTree.Element, library: str, overlay: SpecificationOverlay) -> None:
    define = rule.get("define")
    if not define:
        return
    # A Rule's prose element is `Text`, not `Narrative` — same role, different name in the schema.
    _add(overlay, rule.get("library", library), define, Narration(
        title=_narrated(rule.find(_q("Title"))),
        description=_narrated(rule.find(_q("Text"))),
        rationale=_narrated(rule.find(_q("Rationale"))),
        guidance=_narrated(rule.find(_q("Guidance"))),
    ))


def _add(overlay: SpecificationOverlay, library: str, define: str, narration: Narration) -> None:
    if narration.is_empty():
        return
    # First writer wins. A define reached from two populations is narrated once; the instance states
    # it where it reads best and the duplicate is the same prose.
    overlay.elements.setdefault(element_key(library, define), narration)


def _narration(el: ElementTree.Element) -> Narration:
    return Narration(
        title=_narrated(el.find(_q("Title"))),
        description=_narrated(el.find(_q("Narrative"))),
        rationale=_narrated(el.find(_q("Rationale"))),
        guidance=_narrated(el.find(_q("Guidance"))),
    )


def _overview(el: ElementTree.Element | None) -> Overview:
    if el is None:
        return Overview()
    intent_el = el.find(_q("Intent"))
    scope_el = el.find(_q("ScopeNonGoals"))
    return Overview(
        description=_narrated(el.find(_q("Description"))),
        intent=Intent(
            statement=_narrated(intent_el.find(_q("Statement"))),
            clinical_rationale=_narrated(intent_el.find(_q("ClinicalRationale"))),
            recommendation=_narrated(intent_el.find(_q("Recommendation"))),
        ) if intent_el is not None else None,
        scope=ScopeNonGoals(
            in_scope=_narrated(scope_el.find(_q("InScope"))),
            non_goals=[n for n in (_narrated(x) for x in scope_el.findall(_q("NonGoal"))) if n],
        ) if scope_el is not None else None,
        guidance=_narrated(el.find(_q("Guidance"))),
        how_to_read=_narrated(el.find(_q("HowToRead"))),
    )


def _narrated(el: ElementTree.Element | None) -> NarratedText | None:
    """One prose element, with its inline markup flattened to text.

    `NarratedTextType` is mixed content carrying `ConceptRef` / `RuleRef` / `Emphasis` children
    (`SPEC-XSD` B6). Those are structural links the renderer will resolve; until it does, their
    surrounding prose still has to read as a sentence, so the child's own text is inlined and the
    tail preserved. Nothing is dropped and nothing is invented.
    """
    if el is None:
        return None
    blocks = _blocks(el)
    segments = _segments(el)
    text = _plain(segments)
    if not text and not blocks:
        return None
    return NarratedText(
        text=text or "",
        blocks=blocks,
        segments=segments,
        source_span=el.get("sourceSpan"),
        provenance=el.get("provenance", "narrated"),
        author=el.get("author"),
        source_ref=el.get("sourceRef"),
        method=el.get("method"),
        review_state=el.get("reviewState"),
        reviewed_by=el.get("reviewedBy"),
        reviewed_on=el.get("reviewedOn"),
        source_tag=el.get("sourceTag"),
    )


#: Children that are DEVICES rather than inline markup — they render as blocks after the prose, so
#: they must not be flattened into the sentence the way `Emphasis` and `ConceptRef` are.
_BLOCK_TAGS = ("List", "Table")


def _blocks(el: ElementTree.Element) -> list[NarratedList | NarratedTable]:
    """SPEC-NARRATIVE-DEVICES — the device blocks in one prose element, in document order."""
    out: list[NarratedList | NarratedTable] = []
    for child in el:
        if child.tag == _q("List"):
            out.append(NarratedList(
                kind=child.get("kind", "plain"),
                title=child.get("title"),
                # SPEC-READ-REF — an Item is parsed as SEGMENTS, not as flat text. It is where a read is
                # most often named, so a binding that only worked in the lead sentence would miss the
                # place it is actually used. `text` stays the plain projection of the same segments, so
                # the two cannot say different things.
                items=[ListItem(text=_plain(_segments(i)) or "",
                                source_span=i.get("sourceSpan"),
                                segments=_segments(i))
                       for i in child.findall(_q("Item"))],
            ))
        elif child.tag == _q("Table"):
            rows = child.findall(_q("Row"))
            out.append(NarratedTable(
                columns=[_text(c) or "" for c in child.findall(_q("Column"))],
                rows=[[_text(cell) or "" for cell in r.findall(_q("Cell"))] for r in rows],
                row_spans=[r.get("sourceSpan") for r in rows],
                title=child.get("title"),
                check_against=child.get("checkAgainst"),
            ))
    return out


def _segments(el: ElementTree.Element) -> list[Segment]:
    """SPEC-METRIC-REF / SPEC-READ-REF — one element's inline content, in order.

    TWO inline children cannot be flattened into the sentence, for opposite reasons.

    `MetricRef` stands for a number the renderer resolves, and inlining it would put the define's name
    where the figure belongs ("of the Encounters During Period encounters"). It has no text of its own.

    `Read` DOES have text — the phrase, which is exactly what belongs in the paragraph — but flattening
    it would throw away the binding, and the binding is the whole point: "the problem list" as plain
    prose is a phrase, and bound to `qicore-condition-problems-health-concerns` it is a reference with a
    count behind it. So the run is kept, carrying both.

    Every other inline child — `ConceptRef`, `RuleRef`, `Emphasis` — is a structural link whose own text
    still reads as prose, so it is inlined as before.

    Device children (`List`, `Table`) are skipped, tail and all: a list belongs after the sentence, not
    inside it.
    """
    out: list[Segment] = []

    def prose(chunk: str | None) -> None:
        """Append prose, merging into the previous run so runs never fragment mid-sentence."""
        text = _collapse(chunk)
        if not text:
            return
        if out and isinstance(out[-1], TextRun):
            out[-1] = TextRun(text=out[-1].text + text)
        else:
            out.append(TextRun(text=text))

    prose(el.text)
    for child in el:
        if child.tag in (_q(t) for t in _BLOCK_TAGS):
            continue
        if child.tag == _q("MetricRef"):
            library, define = child.get("library"), child.get("define")
            # An unqualified reference would substitute another library's figure (`SPEC-ANCHOR`), which
            # is worse than no figure: it reads as authoritative. Skipped, and the sentence renders with
            # the absence mark the renderer uses for an unresolved reference.
            if library and define:
                out.append(MetricReference(library=library, define=define,
                                           grain=child.get("grain", "subject"),
                                           rendering=child.get("as", "count")))
            else:
                log.warning("MetricRef with no library/define — dropped")
        elif child.tag == _q("ParameterRef"):
            # SPEC-PARAMETER-REF — the name only. A parameter with no name resolves to nothing and would
            # render as a silent gap in a sentence built around it, so it is dropped loudly instead.
            name = child.get("name")
            if name:
                out.append(ParameterReference(name=name))
            else:
                log.warning("ParameterRef with no name — dropped")
        elif child.tag in (_q("ConceptRef"), _q("RuleRef")):
            # SPEC-DEFINE-REF — the binding is KEPT rather than flattened. It used to be inlined with
            # `Emphasis`, which threw away the reference and left a define named in a clinical sentence
            # inert while the same name inside the CQL block was followable.
            #
            # The PHRASE is the element's text where it has any, and the define's name only as a
            # fallback: prose says "example condition diagnosis", and substituting the declared
            # name would put `Example Condition Diagnosis` mid-sentence in title case.
            #
            # UNQUALIFIED IS DROPPED, not guessed. Two libraries may declare one name (`SPEC-ANCHOR`),
            # so a reference with no library would resolve to whichever the client happened to index
            # first — a link to the wrong criterion, indistinguishable from a right one.
            library, define = child.get("library"), child.get("define")
            phrase = _collapse(child.text).strip() or (define or "")
            if library and define:
                out.append(DefineReference(
                    text=phrase, library=library, define=define,
                    kind="rule" if child.tag == _q("RuleRef") else "concept",
                    source_span=child.get("sourceSpan")))
            else:
                log.warning("%s with no library/define — flattened to prose: %r",
                            child.tag.rsplit('}', 1)[-1], phrase)
                prose(phrase)
        elif child.tag == _q("TerminologyRef"):
            # SPEC-TERMINOLOGY-REF — the binding is kept, and it is library-qualified for the reason
            # `ConceptRef` is, only sharper: value-set names repeat across an include closure, and a
            # phrase resolved to the wrong OID is a claim about which patients match rather than about
            # where a link goes. Unqualified is flattened to prose, never guessed.
            #
            # The PHRASE is the element's text, falling back to the declared name: a sentence reads
            # "an example condition diagnosis" and the declaration is `Example Condition Diagnoses`, but an EMPTY
            # element would render as a mark with nothing in it, which is worse than the identifier.
            library, name = child.get("library"), child.get("name")
            phrase = _collapse(child.text).strip() or (name or "")
            if library and name:
                out.append(TerminologyReference(text=phrase, library=library, name=name,
                                                source_span=child.get("sourceSpan")))
            else:
                log.warning("TerminologyRef with no library/name — flattened to prose: %r", phrase)
                prose(phrase)
        elif child.tag == _q("Read"):
            # SPEC-READ-REF — the phrase is REQUIRED and the binding is not enforceable here: a `Read`
            # naming a profile this measure does not retrieve is a real authoring error, but only the
            # client holds the retrieve model to check it against, and it degrades to plain prose there.
            # An EMPTY one is a different thing — it would render as a chip with nothing in it — so the
            # phrase falls back to the profile rather than vanishing.
            phrase = _collapse(child.text).strip()
            if phrase:
                out.append(ReadReference(text=phrase, profile=child.get("profile"),
                                         resource_type=child.get("type"),
                                         source_span=child.get("sourceSpan")))
            else:
                log.warning("Read with no phrase — dropped")
        else:
            prose(child.get("define") or child.text)
        prose(child.tail)

    # Trim the element's own edges. Interior boundary spaces are load-bearing (they separate prose from
    # a cited figure); the outermost ones are the file's indentation and would render as a stray space
    # before the first word.
    if out and isinstance(out[0], TextRun):
        out[0] = TextRun(text=out[0].text.lstrip())
    if out and isinstance(out[-1], TextRun):
        out[-1] = TextRun(text=out[-1].text.rstrip())
    return [s for s in out if not (isinstance(s, TextRun) and not s.text)]


def _collapse(chunk: str | None) -> str:
    """Collapse a run's internal whitespace WITHOUT closing up its edges.

    The instances wrap prose across lines for reviewability; the wrapping is a property of the file, not
    of the sentence, so it is normalised away rather than shipped to the browser as newlines. The edges
    have to survive that: the space before `<MetricRef/>` lives at the end of the run preceding it, and
    collapsing it away is how "of the 12,431 encounters" becomes "of the12,431encounters".
    """
    if not chunk:
        return ""
    core = " ".join(chunk.split())
    if not core:
        return " "
    lead = " " if chunk[:1].isspace() else ""
    trail = " " if chunk[-1:].isspace() else ""
    return f"{lead}{core}{trail}"


def _plain(segments: list[Segment]) -> str | None:
    """The plain-text projection of inline content — a nav summary, a title, a catalogue row.

    Derived from the same segments the renderer uses, so the summary and the rendered prose cannot say
    different things. A cited figure reads as an em-dash: a one-line summary has no metric set to
    resolve against, and printing the define's name there would state something the sentence does not.

    SPEC-READ-REF / SPEC-DEFINE-REF / SPEC-TERMINOLOGY-REF — a bound READ, DEFINE or TERMINOLOGY phrase
    reads as ITS OWN PHRASE, not as an em-dash. They are unlike a figure: a `MetricRef` has no text to
    fall back to, while these carry the very words the sentence is made of, and blanking them would turn
    "recorded on the problem list" into "recorded on the —".
    """
    # SPEC-PARAMETER-REF — a parameter projects to NOTHING, not to an em-dash. The two absences are
    # unlike, and the rule is the same one that separates a `Read` from a `MetricRef`: a sentence built
    # around a figure loses a noun without it ("the total was —"), while one built around a parameter
    # reads correctly without it — *the measurement period* is the sentence minus the year, not a hole.
    # An em-dash here put "the — measurement period" into every nav summary and title that quotes it.
    parts = ["" if isinstance(s, ParameterReference)
             else s.text if isinstance(s, (TextRun, ReadReference, DefineReference,
                                           TerminologyReference))
             else "—" for s in segments]
    return " ".join("".join(parts).split()) or None


def _text(el: ElementTree.Element | None) -> str | None:
    """One element as plain text — for attributes that are strings by nature (a `Title`, a table cell)."""
    if el is None:
        return None
    return _plain(_segments(el))
