# Narrative tags

The CQL annotation vocabulary for the Specification's prose (`SPEC-NARRATIVE-TAGS`). Each piece of prose
the Specification shows has a named home: a CQL annotation tag. This document lists the tags, the
Specification element each one fills (`designer/server/src/cql_databricks_designer/schema/specification.xsd`) and what the reader sees.

The tag name and the XSD element name are the same word. `@intent` in the CQL is `Overview/Intent/Statement`
in the schema and the Intent section on the page.

## How a tag gets into the CQL

A tag is a `@name: value` line in the comment block directly above a declaration. The CQL spec allows any
valid identifier as a tag name, so the vocabulary is open. A tag the Designer does not recognise is
ignored, never guessed at.

```cql
/*
@description: Adults with hypertension whose most recent blood pressure is below 140/90 mmHg during the measurement period.
@intent: Identify hypertensive patients achieving blood pressure control.
@scope: Patients 18-85 with hypertension and a qualifying outpatient visit in the period.
*/
library HypertensionBPControl version '1.0.0'
```

| Placement | Tag sits above | Reaches |
|---|---|---|
| Root | the `library` declaration | the whole measure, in the Overview |
| Element | a `define` | that one population criterion, Concept or Rule |

With `EnableAnnotations`, the translator writes tags into `annotation[].t[]` as `{name, value}` pairs.
Root tags land on the library's annotation, element tags on the statement's.

The Designer reads element-level tags from the ELM (`specDocument.ts`, `narrationFromTags`). It does not
read root-level tags from the ELM. The Overview comes from the Measure resource (described below).

## Root-level tags: the Overview

The Overview presents them in this order.

| Tag | Specification element | Page section | Answers |
|---|---|---|---|
| `@description` | `Overview/Description` | Description | What the measure computes, in one paragraph. |
| `@intent` | `Overview/Intent/Statement` | Intent | Why the measure exists: the outcome it is meant to move. |
| `@clinicalRationale` | `Overview/Intent/ClinicalRationale` | Clinical Rationale | The clinical reasoning: evidence, guideline, why this threshold or interval. |
| `@clinicalRecommendation` | `Overview/Intent/Recommendation` | Clinical Recommendation | The guideline text the measure implements. |
| `@scope` | `Overview/ScopeNonGoals/InScope` | Scope & Non-Goals | What the measure covers. |
| `@nonGoal` | `Overview/ScopeNonGoals/NonGoal` | Scope & Non-Goals, one item each | One thing the measure does not do. Repeatable, one per tag. |
| `@guidance` | `Overview/Guidance` | Guidance | The author's instructions for applying the measure. |
| `@howToRead` | `Overview/HowToRead` | How to read this document | How to read this document. The Designer's voice about the document, not the author's about the measure. |

No `@nonGoal` tags means nobody wrote any down, not that there are none. The page then omits the list.

## Element-level tags: populations, Concepts and Rules

Population criteria, Concepts and Rules are all CQL defines (`SPEC-CONCEPTS`, `SPEC-RULES`), so they take
the same tags.

| Tag | Specification element | Rendered as | Answers |
|---|---|---|---|
| `@title` | `Title` | the heading | The clinician-facing name, when the define name doesn't read well. |
| `@description` | `Narrative` / `Rule/Text` | the lede | What this is, in one or two sentences. |
| `@rationale` | `Rationale` | Why this matters | Why it exists clinically. |
| `@guidance` | `Guidance` | How to read it | How to read it correctly: inclusive boundaries, nulls, tie-breaks, the common misreading. |
| `@comment` | none | Author's note | Whatever else the author wrote. |

`@description`, `@rationale` and `@guidance` are three different questions: what, why, and how to read it.
A reviewer needs all three to check a criterion without opening the code.

`@comment` is a standard cqframework tag. HL7's own libraries use it, for example FHIRHelpers. It renders
under the description, as a separate field, never joined into it. It has no Specification element. A
section with any prose keeps its CQL block closed under it; a section with none opens it (`SPEC-CQL-VIEW`).

Example:

```cql
/*
@title: Encounters During Period
@description: An encounter during the measurement period.
@rationale: The measure focuses on patients in active care during the period.
@guidance: Any encounter type and status qualifies; only the date matters.
*/
define "Encounters During Period":
  [Encounter] E
    where E.period during "Measurement Period"
```

## Provenance

Every prose element has a `provenance` attribute.

| Value | Means | Shown as |
|---|---|---|
| `authored` | A person or organisation asserted it: a tag in the CQL, or a field of the measure's FHIR `Measure` resource (`SPEC-MEASURE-METADATA`). | a byline naming the author, for the resource's fields |
| `derived` | Built from the CQL: structure, polarity, metrics, source links. Never prose. | no mark |

The page shows only `authored` prose (`DOC-EXPLAIN-REMOVE`). The schema still has a `narrated` value, but
nothing the Designer serves carries it, and the page refuses to render it (`NarratedRun` in
`MeasureNarrative.tsx`). The assistant explains a measure on request, in its own drawer, and nothing it
writes reaches the page. Adjacent fields with the same provenance are grouped into one run (`proseRuns` in
`specDocument.ts`).

`authored` prose carries `author` and, when quoted from a published artifact, `sourceRef`
(`canonical|version`). External measures are read only (`APP-NARRATIVE-TAGS`) until derived into an owned
copy (`APP-DERIVE-MEASURE`).

## Where the prose comes from

There are two sources, applied field by field in one order.

### The measure's FHIR resource (`SPEC-MEASURE-METADATA`)

The server reads the measure's published `Measure` resource from tagged knowledge
(`Corpus.measure_resource`). It quotes these fields verbatim as `authored`, attributed to
`Measure.publisher` (`specification/measure_metadata.py`):

| `Measure` field | Slot (`sourceTag`) |
|---|---|
| `description` | `description` |
| `purpose` | `intent` |
| `rationale` | `clinicalRationale` |
| `clinicalRecommendationStatement` | `clinicalRecommendation` |
| `usage` | `guidance` |

The resource covers the Overview only. It has no field for a single define, so Concepts and Rules come from
CQL tags or have no prose.

A library with no tags and no Measure resource shows no prose. Generated narration, cached per user,
was removed (`APP-NARRATIVE-OVERLAY`, superseded), and so were the hand-written instances that once stood
in for it (`SPEC-STATIC-OVERLAY`, superseded).

### Precedence

CQL tag, then FHIR resource, field by field. The losing text is dropped, not kept beside the winner.

## Narrative devices (`SPEC-NARRATIVE-DEVICES`)

The forms a narrated answer takes. They come from how published measure specifications are commonly
written, such as AHRQ Quality Indicators. A checkable device turns a claim into something a test can compare with the
ELM.

| Device | What it is | Checkable |
|---|---|---|
| Fixed frame | Every criterion answers the same questions in the same order. | yes |
| Include / Exclude blocks | Two lists, never one mixed paragraph. The AHRQ form. Polarity is `SPEC-POLARITY`. | yes |
| Named set reference | Name the value set, don't list its codes inline. Codes live in the Appendix. | yes |
| Code table | code, display, system, one row each. | yes, against the ELM literal |
| Anchored timing | Every date says what it is anchored to. | partly |
| Inclusivity and units | "40 to 75 inclusive", "strictly below 100". | yes |
| Note on the known misreading | The mistake people make. This is what `@guidance` is for. | no |
| Interpretation direction | "A higher rate indicates better quality", from `Measure.improvementNotation`. | yes |
| Exclusion vs exception | Denominator exclusion and denominator exception are different population kinds. | yes |
| Numerator / denominator symmetry | Both stated in the same shape. | yes |
| Definitions table | Terms defined once: parameters and library overviews. | partly |
| Timeline figure | The measurement period and look-back, to scale. | yes |
| Worked example | One patient walked through the criterion. | no |

In the schema, Include / Exclude blocks and plain lists are `NarratedList` (`kind` `include`, `exclude` or
`plain`), and code tables are `NarratedTable` with an optional `checkAgainst` (`specification/model.py`).

Avoid these:

- Numbered steps where order isn't real. ELM has no evaluation order for `A and not B` (`SPEC-RULES`).
- A bulleted rule list with no coverage check. A list reads as complete.
- Prose that mixes inclusion and exclusion in one sentence.
- Paraphrased function names. Quote the function's own `@description`, or show the bare name.

## Free-text comments (`SPEC-COMMENT-NARRATION`)

An ELM `Annotation` carries two things:

| Field | Holds |
|---|---|
| `t[]` | `{name, value}` tag pairs |
| `s` | the source narrative: the declaration's text, including the comment block above it |

`annotation[]` also holds `CqlToElmInfo` and `CqlToElmError` entries. Filter on `type == "Annotation"`.

The Designer reads only `t[]`. It skips `s` (`narrationIndex` in `specDocument.ts`), so a plain comment
above a define is not shown as prose. `SPEC-COMMENT-NARRATION` describes reading it: a comment block with
no blank line before the declaration documents it; one separated by a blank line is a file-level note and
is dropped. That reading is not built.

## Requirements

| ID | Where |
|---|---|
| `SPEC-NARRATIVE-TAGS` | this document |
| `SPEC-MEASURE-METADATA` | "The measure's FHIR resource" |
| `APP-NARRATIVE-OVERLAY` | Superseded by `DOC-EXPLAIN-REMOVE` |
| `SPEC-STATIC-OVERLAY` | Superseded by `APP-NARRATIVE-OVERLAY` |
| `DOC-EXPLAIN-REMOVE` | "Provenance" |
| `SPEC-NARRATIVE-DEVICES` | "Narrative devices" |
| `SPEC-COMMENT-NARRATION` | "Free-text comments" |
