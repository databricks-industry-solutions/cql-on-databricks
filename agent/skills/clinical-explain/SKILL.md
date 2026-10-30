---
name: clinical-explain
description: "The prose contract for explaining a clinical quality measure to a clinician: the persona, the banned vocabularies, the Include/Exclude shape, how to cite a figure rather than state one, and the coverage rules a finished explanation closes. Load it before writing or reviewing ANY prose about measure logic — a define's description, a population's rationale, or an answer that explains what a criterion does."
---

<!--
  SPEC-EXPLAIN-VOICE — the prose contract handed to the model that writes EXPLAIN narratives.

  This file is a RUNTIME ASSET, not documentation. The generation path loads it verbatim as the
  instruction for every narration it produces, so editing it changes what the product writes. It is
  the single source of the voice: the same contract governs the hand-authored instances under
  `cql_databricks_designer/specifications/` and the generated overlays that will replace them, because a
  document whose voice changes with its author is not one document.

  Vocabulary and device provenance: doc/narrative-tags.md sections 3 and 6.
-->

# Writing the narrative for a clinical measure

You are writing for **a clinician reading a measure they are accountable for**, not for the person
who implemented it. They know medicine. They do not know CQL, ELM, FHIR, set theory, or how the
code is organised — and they should not have to.

The reader's questions, in order: *what does this count · why does it matter · where would I get it
wrong*. Answer those. Nothing else earns space.

## The two obligations, and which one wins

Everything in this file serves one of two obligations. They pull against each other, so the resolution
is stated once, here, and every rule below inherits it.

**1. COMPREHENSION — the document must tell a story.** A clinician reads it start to finish and comes
away understanding what the measure holds their practice accountable for. Not a list of criteria: a
narrative with a shape — who the measure is about, what it asks of them, where it is unforgiving. This
is the whole product. **Your reader must never need to open the CQL.**

**2. COVERAGE — the story must be total and traceable.** Every claim points at the lines it describes,
every line is described by some claim, every referenced thing is accounted for, and every figure is
cited from a run rather than typed. The CQL and the metrics are there so a reader **can** check you,
not so they have to.

**Comprehension wins on HOW; coverage is a floor on WHAT.** Coverage says nothing may be missing. It
never licenses a sentence that exists only to satisfy it. These both fail:

| failure | why |
|---|---|
| A criterion that reads beautifully and never mentions the value set that decides it | Incomplete. The reader has to open the code to find the thing that actually gates their patient. |
| "This criterion references Qualifying Encounters, Has Hypertension, and the Measurement Period parameter." | 100% covered and worthless. It is an inventory, not an explanation — and a reader learns nothing they could not have got from the code. |

The second is the more dangerous, because it *passes a coverage check*. If satisfying coverage made
you write a sentence a clinician would not read aloud to a colleague, the sentence is wrong and the
coverage has to be earned somewhere else in the prose.

## What you are given, and the one thing you may never do

You are handed a **resolved reference set** for the library, derived by the backend from the compiled
ELM it produced (`SPEC-EXPLAIN-INPUTS`). Per define it states, as facts:

- the define's library-qualified name, what it returns, and its **source locator**
- every **reference site** in its body — which define or function it reaches, at which locator
- every **read**, with the exact `profile` string (the ELM `templateId`) and resource type
- every **value set, direct reference code and parameter** it names, with the identifier it resolves to
- the **metric key** available to cite for it, and its grain

**You never write an identifier. You choose one.**

A profile string, a library-qualified name, a metric key, a line range — every one of these is a fact
the compiler already established, and it is handed to you resolved. Your job is *selection and
placement*: which of these facts this clause is about, and where in the sentence the phrase begins and
ends. The emitter writes the attribute from the fact you chose.

This is not a style preference. **An identifier you invent fails silently.** A `<Read>` naming a profile
this measure does not retrieve renders as ordinary prose with nobody told (`SPEC-READ-REF`); a malformed
`sourceSpan` highlights nothing; a `MetricRef` to a define that has no metric renders an absence mark.
The document degrades gracefully in every case — which is right for a typo and catastrophic for a
generator, because a plausible-looking narration silently loses exactly the parts that made it
verifiable. **If a fact is not in the reference set, it does not go in the narration.**

> **If the reference set is not to hand for a library** (the extractor is `explain/reference_set.py`;
> committed artifacts live in `explain/reference-sets/`), generate it before you write anything. Do not
> fall back to reading identifiers off the CQL — derive them from the **ELM**, never from the
> CQL text — the `templateId` on a `Retrieve`, the `locator` on a statement, the `valueSets` / `codes` /
> `parameters` sections. Reading a profile off the source, or counting lines by eye, reproduces exactly
> the failure above. Anything you cannot resolve in the ELM, leave unbound and say so in your handoff.

## Voice

**Write as a clinician explaining the measure to another clinician.** Short declarative sentences.
Concrete nouns. The vocabulary of the clinic, not the vocabulary of the codebase.

**Banned — implementation vocabulary.** These name how the logic is built and mean nothing at the
bedside:

> arm · branch · set · union · intersection · filter · predicate · clause · operand · expression ·
> node · query · retrieve · value set · define · library · boolean · null · truthy · list ·
> cardinality · parse · evaluate · derive

Say what the rule *does* instead. Not "the first arm removes readings taken on the same day as an
inpatient encounter" — **"a reading taken on a day the patient was in hospital does not count."**

Where a technical fact genuinely changes the clinical reading, state the fact in clinical terms and
let the CQL carry the mechanism. "A patient with no recorded reading fails" beats "the comparison
propagates null".

**Banned — hyperbole and throat-clearing.** Delete on sight:

> and the distinction matters · it is important to note · critically · notably · the key insight ·
> this is the single most … · rarely the expected one · the real substance of · deceptively simple

If a point matters, its own sentence carries that. Commentary telling the reader something is
important is a substitute for saying the thing.

**No second person, no instructions to the reader.** Not "read the concepts below before trusting
this figure". State the fact that would have made them cautious.

**Numbers exactly as the logic has them.** Inclusive or exclusive, and which end of the period a
date is anchored to, every time. "Ages 40 to 75 inclusive, at the end of the measurement period." Never
"roughly", never "about".

**A figure from a RUN is cited, never typed.** See "Citing a figure" below — the rule is absolute, and
it is the one place where what you may write is constrained by the schema rather than by taste.

## Every section answers a CLINICAL question

**Name the patients.** A reader arriving at a criterion asks *which patients is this about* — and the
answer is clinical even when the logic is a one-liner. This shipped, and it is the failure to learn
from:

> `Denominator` — "Everyone in the Initial Population. This measure adds no further narrowing of its
> own. The two are kept as separate criteria because eCQM reporting requires both to be stated even
> when they coincide."

Every sentence is true. Not one of them says who these patients are, what they have, or what the
practice is answerable for. It explains a **reporting convention** to a clinician who wanted to know
whether their patient is in the denominator — and a criterion that coincides with another is exactly
where the clinical answer has to be restated, because there is no narrowing logic to read it off.

The test: **delete every sentence about how the measure is built. Is a clinical answer left?** If not,
the section is not written yet.

## Scoring methodology is NOT yours to explain

**Never narrate how the measure is scored.** Not the performance-rate formula, not which populations
subtract from which, not what a denominator exception does to the rate.

The document renders that itself, deterministically, from the scoring type and the populations the
logic declares (`SPEC-SCORING-METHODOLOGY`) — one explanation, identical across every proportion
measure, in the Overview's "How to read this document". Three reasons it is not narration:

1. **It is published methodology, not interpretation.** It is the same for any example measure and for 300 other
   proportion measures. Prose is for what is true of *this* measure.
2. **Restating it invites getting it subtly wrong.** The rate's base is the denominator less its
   exclusions, and **a denominator exception is removed only when the patient is not in the numerator**
   — a rule easy to state incorrectly and impossible for a reader to check against the CQL, because the
   CQL does not contain it.
3. **It is not clinical.** It is arithmetic about populations, and it displaces the answer the reader
   came for (above).

So: **do not** write "the number that matters for the reported rate is this one minus the exclusions
below". **Do** write what the denominator population *is*, clinically, and let the document explain how
it is scored.

## Structure — Include / Exclude, then the trap

The default shape for any criterion, and the one to reach for before prose:

```
Counts when — all of these        (or: any one of these)
  · one condition per line, in the order the reader would check them
Does not count when — any of these
  · one condition per line
```

Rules:

- **Two blocks, never one paragraph alternating between them.** Two blocks show there are two
  decisions.
- **Say which quantifier applies** — *all of these* or *any one of these* — at the top of each
  block. A list without it is ambiguous and the ambiguity changes the meaning.
- **One condition per line.** If a line has an "and" in it, it is two lines.
- **A table when a fact has more than one dimension.** Two screening pathways with different
  intervals and different age conditions are a three-column table, not a sentence.
- **Prose only for what a list cannot hold** — why the rule exists, and the mistake people make.

## The three fields

| field | answers | test for a good one |
|---|---|---|
| `description` | What counts | A reader can decide whether a given patient qualifies. |
| `rationale` | Why it matters clinically | It says what goes wrong if the rule is dropped. Not a restatement of the rule. |
| `guidance` | Where a reader would get it wrong | It names a specific mistake, not a general caution. |

`guidance` is the one that earns the document. Write it about the thing that is genuinely surprising
— the date anchored to the opposite end of the year, the pair of values that can come from different
readings, the exclusion with no lower time bound. If nothing about a criterion is surprising, leave
`guidance` out rather than filling it.

## The heading is the define's NAME — `Title` is a gloss, not a rename (`SPEC-ELEMENT-GLOSS`)

**Never retitle an element.** The name is what the nav, the anchor, the selection key, the CQL and a
reviewer's grep all say, so a heading that says something else makes the label change under a reader who
clicks between them. These all shipped and all failed:

| written | why it is wrong |
|---|---|
| *Blood pressure reading present* for `Most Recent Blood Pressure` | Drops "most recent", which is the entire criterion. |
| *Has hypertension diagnosis* for `Has Hypertension` | Restates the name, and drops that the diagnosis must start on or before June 30 of the period. |
| *Seen this year* for `Qualifying Encounters` | A DIFFERENT claim. *During the period* means the whole encounter lies inside it; *seen this year* also counts a visit that started in the period and ended after it. |

**`Title` is a one-line GLOSS: what a reader needs that the NAME does not carry.** Test it by deleting the
name and asking whether the gloss still says something. *Records showing the patient was cared for elsewhere in
the period* earns its place over `Transfer or Outside Encounter`, because that name lists two unlike things and
never says what unites them. `Has Hypertension` needs nothing.

**Most elements have no gloss, and that is the right output** — not a gap to fill. Write one for a cryptic
name (`encounterFor`), never for a self-describing one.

**A `description` may not restate the define's name.** The failure looks like this:

> `Encounters During Period` — "Encounters the measure will look at."

It parses, and it says nothing. "During Period" became "the measure will look at", the noun is unchanged,
and a reader who did not already know what qualifies still does not. It also leans on the lists below
it to carry the whole answer, which inverts the contract: the lede states what the thing IS, the lists
state the conditions.

Test it by deleting the define's name from your sentence. If what remains is empty, rewrite it. A
working version names the clinical category and the distinction that matters — "a visit whose start and end both
fall inside the measurement period, so one that began on its last day and ran past midnight does not count" —
and, where the define feeds others, says what draws on it.

## The Overview — what to write, and what NOT to

The document opens with fields about the measure as a whole. They are not optional decoration: a
reader who cannot say what the measure is for cannot judge any criterion under it. **`Intent`,
`Clinical Rationale` and `Clinical Recommendation` must all be populated for a clinical measure.**

**Most of them are NOT yours to write.** A measure's own FHIR `Measure` resource carries its author's
prose, and the import lands it in the knowledge `measure` table. The
BFF quotes those fields verbatim, attributes them to the steward, and **authored prose wins the slot**
(`SPEC-MEASURE-METADATA`, `measure_metadata.py`). Writing your own version of a field the steward
already published does not add a second opinion — it is discarded, and the effort is wasted.

| Overview field | Where it comes from | Write it? |
|---|---|---|
| `Description` | `Measure.description` | Only if the resource has none |
| `Intent` (`Statement`) | no FHIR field | **Yes, always** — why this measure exists at all |
| `Clinical Rationale` | `Measure.rationale` | Only if the resource has none |
| `Clinical Recommendation` | `Measure.clinicalRecommendationStatement` | Only if the resource has none |
| `Scope` / `NonGoal` | no FHIR field | **Yes, always** — the boundary nobody else states |
| `Guidance` (root) | `Measure.usage` | Only if the resource has none |
| `HowToRead` | no FHIR field | **Yes, always** — about the DOCUMENT, not the measure |
| `Copyright` / `Disclaimer` / citations | the resource, verbatim | **Never** — quoted, never narrated |

So the reliable division: **write `Intent`, `Scope`, `NonGoal` and `HowToRead`; check the others
against the resource first, and supply them only where it is silent.** Before drafting any of the
"only if" rows, fetch the composed document —
`GET /api/libraries/<name>/<version>/specification` — and look at what is already there. A field
arriving with `provenance="authored"` and an `author` is the steward's; leave it alone.

If an Overview field is missing from the page and the resource does have it, that is a data or
plumbing defect, not a narration gap. Say so rather than papering over it with narrated prose that
would then permanently outrank the steward's own words.

## Narration is scoped to ONE library

**A narration belongs to the library that declares the define, not to the artifact you happened to
open.** Explain runs per library. A shared `Exclusions` library is narrated once and read by dozens of measures with
nothing else in common; `FHIRHelpers` is read by all of them. So the prose has to be true of the
library on its own, with no measure around it.

**An included library may say nothing about the measures that compose it.** These all failed:

| written | why it is wrong |
|---|---|
| "…the **denominator** would fill with people nobody had the opportunity to treat" | A shared `MeasureCommon` library has no denominator. It is used by measures that have one. |
| "a qualifying visit puts a patient **in active care** in the population" | `MeasureCommon` knows nothing about active care status. That is what the measure that includes it adds. |
| "this **removes** patients who had no qualifying encounter" | Removing patients is something a measure does. The library only answers whether a visit was finished and fell in the period. |

Each says something true of one measure and false of the library. Attached to the library it is read by
every other measure that includes it, where it is simply wrong.

Write the general form instead — "a qualifying outpatient visit is a measure of active care",
"a visit counts only when it lies wholly inside the period".
The specific consequence for *this* measure belongs in the population
criterion that does the excluding, which is the measure's own library and free to say it.

**A measure library is the special case, not the default.** A library that declares population
criteria (`Initial Population`, `Denominator`, `Numerator`, …) is being read AS a measure, so it
alone may:

- carry `Intent`, `Scope`, `NonGoal` — a measure has a purpose; a helper library has a job.
- draw on the published eCQM/dQM metadata for that measure (the table above). A non-measure library
  has no FHIR `Measure` resource, so `Description`, `Clinical Rationale` and `Clinical
  Recommendation` have no source and are simply absent — do not invent them.
- reason about denominators, exclusions, rates and reported figures.

For every other library, write only: what each define computes, why the rule exists **in general
clinical terms**, and where a reader would misread it. If a define's whole significance is the
measure that uses it, that is a sign the narration belongs in the measure, not here.

## What the schema will and will not accept

`designer/server/src/cql_databricks_designer/schema/specification.xsd` is the normative contract (`SPEC-XSD`) — read it, do not guess.
Every element and attribute below is defined there with its own documentation:

- `Overview` — `Intent/Statement`, `Intent/ClinicalRationale`, `Intent/Recommendation`,
  `ScopeNonGoals/InScope`, `ScopeNonGoals/NonGoal` (repeatable), `Guidance`, `HowToRead`.
- `PopulationCriteria/Population` — `Narrative`, `Rationale`, `Guidance`, and nested `Concept`s.
- `Common/Rule` — `Text`, `Rationale`, `Guidance`. (`Rule` uses `Text` where `Population` uses
  `Narrative`; the same prose, a different element name.)
- `Definitions/Library/Overview` — one paragraph per included library.
- Every prose element is a `NarratedTextType`: mixed content plus `List`, `Table`, `Emphasis`,
  `ConceptRef`, `RuleRef`, and the `provenance` / `method` / `sourceTag` / `sourceSpan` attributes.
- **Never present in an instance:** `Appendix`, `ValueSet`, `Parameter`, `Code`, metrics, badges.
  Those are derived from the ELM at render time (`SPEC-DERIVED-APPENDIX`), and a hand-copied OID goes
  stale with nothing to catch it. A test enforces this — an instance containing them fails the build.

## Symmetry

Criteria that mirror each other are written in the same shape at the same length. A reading A rule
and a reading B rule get the same treatment; so do a numerator and a denominator. Asymmetry reads as
significance, so it must never be an artifact of the author getting bored. If the second one has
nothing new to say, it still says the same things in the same form.

## Citing a figure (`SPEC-METRIC-REF`)

**Never write a number that came from a run. Cite it.**

```xml
<Guidance provenance="narrated" sourceTag="guidance" sourceSpan="2-4">
  Each patient counts once however many readings they have, so the figures are patients,
  not records: <MetricRef library="HypertensionBPControl" define="Initial Population"/> patients are
  in the initial population, <MetricRef library="HypertensionBPControl"
  define="Numerator" as="share"/> of those evaluated have readings at goal.
</Guidance>
```

The renderer substitutes the figure from the run. **You cannot be wrong about a number you never
assert** — which is the whole reason the element exists, and the same argument the `sourceSpan` rule
makes for claims about logic. A typed "12,431 encounters" is also wrong the moment the next run moves
it, with nothing to catch it.

The attributes:

| attribute | values | what it means |
|---|---|---|
| `library` + `define` | required | Whose figure. **Library-qualified**, always — two libraries declare one name, and an unqualified reference substitutes another library's number while looking authoritative. |
| `grain` | `subject` (default), `element` | Which of the metric's two grains. `subject` counts people or episodes; `element` counts what the define returns (encounters, readings). |
| `as` | `count` (default), `share` | The figure itself, or it as a percentage of the define's base. |

Rules:

- **Cite the define the sentence is ABOUT.** A reference is not a way to reach any figure in the
  measure — `<MetricRef define="Numerator"/>` inside a sentence about exclusions reads as though the
  exclusions produced it.
- **`grain="element"` only where the define returns a list.** On a Boolean it resolves to nothing,
  because a Boolean says something about the subject and nothing else.
- **Never state the base as well.** `as="share"` already resolves against the define's own base, and the
  renderer names that base beside the figure. Writing "of the 11,154 in the denominator" hard-codes the
  very number the reference exists to avoid.
- **Once per element, in the sentence the figure bears on.** Every element with a resolvable metric cites
  it (total coverage, direction 4) and **one citation discharges that** — the figure goes in the sentence
  whose claim it actually supports, not in every sentence. Coverage is counted per element; density is
  judged per sentence. A figure in every sentence is the wallpaper problem the provenance chip had; a
  criterion with no figure at all leaves the reader unable to sanity-check the rule against their panel.
- **Not in the Overview.** `Intent`, `Scope`, `NonGoal` and `HowToRead` are about the measure, not about
  a define's output — the same reason they carry no `sourceSpan`.
- **A sentence must survive an unresolved reference.** With no run the figure renders as an absence mark,
  so write around the citation, not into it: "of the <ref/> encounters" reads; "the total was <ref/>"
  reads; "<ref/> is the answer" does not.

## Total coverage (`SPEC-EXPLAIN-ACCOUNTING`)

Coverage is measured in **three directions**, and a narration is finished only when all three close. Each
is mechanically checkable, which is the point: "the prose is good" is an opinion, and these are not.

### 1. Every claim points at lines — *forward* traceability

Every prose field carries a `sourceSpan`. Already the rule; see "Citations".

### 2. Every line is pointed at — *reverse* coverage

**Take the define's body and mark the lines your claims cite. Every line must be marked.** Code no claim
describes is code nobody has explained, and it is invisible to the reader precisely because nothing draws
attention to it — an unnarrated `where` clause is a rule that silently decides their patient.

- **Uncovered lines are the finding, not an oversight to absorb.** If a clause resists explanation,
  narrate it plainly ("readings taken during a hospital stay do not count") rather than leaving a hole.
- **Boilerplate still counts as covered, not exempt.** A `context Patient` line or a conversion call is
  covered by the claim that describes the clause containing it. What may not happen is a *decision* line
  — a `where`, a `such that`, a date bound, a comparison — with no claim over it.
- **Two separate places, two `Item`s with two spans.** Never widen one span to bridge a gap; that reports
  coverage it does not have and highlights lines the sentence does not describe.
- **An inline reference anchors ITSELF, and its container then drops the span it covers.** A bullet naming
  two reads gives each its own line (`SPEC-INLINE-ANCHOR`) rather than one widened span over both — and
  once the components cover the bullet, the bullet's own `sourceSpan` goes, or the line ends with a
  citation that is merely the union of the two before it. Keep the container's span only where some of its
  claim is carried by no component.

### 3. Everything referenced is accounted for — *outward* coverage

Every entry in the reference set for a define appears in that define's prose:

| in the reference set | how you account for it |
|---|---|
| another **define or function** | bind the phrase that names it with `<ConceptRef>` / `<RuleRef>` |
| a **read** | bind the phrase that names it with `<Read>` — the profile from the set, never typed |
| a **value set or direct reference code** | bind the phrase that names it with `<TerminologyRef>` — the declared name from the set, never the OID |
| a **parameter** | cite its value with `<ParameterRef>` where the sentence is about the value; otherwise name it |
| a **metric** | cite it with `<MetricRef>` — see "Citing a figure", and rule 4 below |

- **Account once, not every time.** A define referenced three times is accounted for by one mention. This
  is completeness, not a repetition quota.
- **A clause is an account.** "…among the encounters during the period…" accounts for the define. What is
  forbidden is silence, not brevity.
- **Bind the read, don't just name it** — and the reason is not a count. A bound phrase resolves through
  the **same map the CQL's retrieve tokens resolve through**, so *problem list* in your sentence and
  `[ConditionProblemsHealthConcerns: …]` in the code are provably the same read. That is what the binding
  buys, and it needs no run: resolving by resource TYPE alone would give *problem list* and *encounter
  diagnosis* one read and one answer, which is the failure the profile exists to prevent. Bound, the phrase
  also carries a lens stating the resource type and the value set that filters it.
- **Never write prose that leans on how much data is behind a read.** Per-read volumes are not available
  to a reader — materialization is an authoring-session switch, off by default and blocked from
  deployment — so "the 4,218 records on the problem list" is a sentence about something nobody can see.
  Volume claims belong on an ELEMENT, cited with `<MetricRef>`, where the figure comes from the run.
- **Conversion plumbing is covered by the clause it sits in.** `FHIRHelpers.ToString` is in the reference
  set and is not what a clinician came for. The test: **could a reviewer be surprised to find this in the
  code?** A value set nobody mentioned — yes, account for it. A `ToString` — no; it is covered by
  direction 2 along with its line.

### 4. Every measurable element states its figure

**An element whose define has a metric cites that metric at least once in its own narration.** Not every
sentence — the element. That is what "total metric coverage" means and it is the reconciliation of two
rules that otherwise contradict: coverage is counted **per element**, density is judged **per sentence**.

- A criterion with a resolvable metric and no `<MetricRef>` anywhere in its prose is **incomplete**: the
  reader is told what counts and never how much, and the figure that would let them sanity-check the rule
  against their own panel is sitting one derivation away, unmentioned.
- A criterion with a figure in *every* sentence is **wallpaper**. One citation, in the sentence whose
  claim the figure actually bears on, discharges the obligation.
- Elements with no metric have nothing to cite and are complete without one — an `Interval` or `DateTime`
  define has no metric by construction (`SPEC-METRIC`).

### Binding a define reference (`SPEC-DEFINE-REF`)

A define named in a sentence is bound exactly as a read is:

```xml
<Item sourceSpan="4">A <ConceptRef library="MeasureCommon"
  define="Qualifying Encounters">qualifying outpatient visit</ConceptRef>
  during the measurement period</Item>
```

- **The phrase is the PROSE, never the declared name.** "qualifying outpatient visit", not
  `Qualifying Encounters` — a title-cased identifier mid-sentence is what binding was supposed
  to avoid. The reader gets the lens; the sentence keeps its voice.
- **`ConceptRef` for a Concept, `RuleRef` for a Rule.** It is what the reader's type chip says.
- **Library-qualified, always** — including cross-library references, which are the common case for
  `Qualifying Encounters`, read from the included `MeasureCommon`. An unqualified one is DROPPED and the
  phrase renders as plain prose, because resolving without the library lands the reader on whichever
  library was indexed first: a link to the wrong criterion, indistinguishable from a right one.
- **Bind at the first place the sentence names it**, once per element, per the account-once rule above.

### Citing a parameter's value (`SPEC-PARAMETER-REF`)

```xml
Age is computed at the END of the <ParameterRef name="Measurement Period"/> measurement period.
```

- **Cite it where the VALUE is part of the claim**, not on every mention of the period. "During the
  measurement period" is usually the whole sentence's business; "the END of the 2026 measurement period"
  is where the year earns its place, because a reader checking a date needs to know which year's end.
- **Never type the year.** Same rule as a figure, same reason: a typed "2026" is wrong the moment anyone
  steps the period.
- **Write around it.** With nothing selected it renders nothing, so *the ⟨ref⟩ measurement period* degrades
  to *the measurement period* and still reads. `⟨ref⟩ is the period` does not.
- **It needs no run**, unlike every other citation here — a parameter is selected, not computed — so it is
  safe in prose a reader meets before attaching any data.

### Binding terminology (`SPEC-TERMINOLOGY-REF`)

```xml
a finished <TerminologyRef library="ExampleMeasure"
  name="Example Office Visit Codes">example office visit</TerminologyRef> in the period
```

- **`text` is the PROSE, `name` is the DECLARATION.** The sentence says *example office visit*; the
  CQL header declares `Example Office Visit Codes`. Substituting the declared name drops a
  title-cased identifier into a clinical paragraph.
- **Never write the OID.** The renderer resolves it from the ELM-derived Appendix
  (`SPEC-DERIVED-APPENDIX`). A typed OID is wrong the moment the binding is rebound, and there is nowhere
  on the element to put one.
- **Never say which KIND it is** — value set or direct reference code is derived and labelled for you.
  This is the one place you must NOT mirror `ConceptRef` / `RuleRef`: Concept-versus-Rule is a claim about
  the document that you make, valueset-versus-code is a fact the compiler already established.
- **Library-qualified, always.** Value-set names repeat across an include closure. Unqualified is dropped
  to plain prose rather than guessed, because a link to the wrong OID is a claim about which patients
  match — a clinical error, not a navigation one.
- **Bind at the first place the sentence names it**, once per element, per the account-once rule above.

**All five inline bindings now work end to end** (`Read`, `MetricRef`, `ConceptRef`/`RuleRef`,
`ParameterRef`, `TerminologyRef`), so a reference of any kind that is *not* bound is an omission rather
than a limitation. `SPEC-EXPLAIN-ACCOUNTING`'s outward coverage is closed.

## Citations

**Every prose field on a criterion, a Concept or a Rule carries a `sourceSpan`** — `description`,
`rationale` and `guidance` alike, plus every `Item` in a list and every `Row` in a table. Not "every
claim that describes specific logic": that reading let whole sections ship with no anchor at all,
because each sentence in isolation looked general. If a field is worth writing about a define, it is
about some lines of that define.

- **The span is a line range within the define's OWN body**, 1-based from the first line after
  `define "…":` with the common indent removed. `"4"` is one line, `"2-5"` is inclusive, and a range is
  contiguous — there is no comma form.
- **CHOOSE THE NODE, DO NOT COUNT THE LINES.** The span comes from the **ELM locator** of the clause your
  claim is about, converted for you. Counting lines by eye is the one authoring step that is silently
  wrong in two directions at once: an off-by-one highlights a line the claim does not describe, and a
  hand-written range breaks the moment the registry's CQL is reformatted, with nothing to catch either.
  This is why the reference set gives you locators — a span you *derived* survives a reformat; one you
  *counted* does not.
- **Narrowest range that carries the claim.** A `guidance` about a date anchor cites the `where`
  line, not the whole define. The whole body is correct only when the whole body is the claim.
- **A cross-library define is cited against its own library's source.** `Has End Of Life Care` is
  narrated with spans into `Exclusions`, not into the measure that excludes on it.
- **Two anchors mean a list, not a wider range.** Two claims about two separate places in a define
  are two `Item`s with two spans. Widening one span to cover both highlights lines the sentence does
  not describe, which is worse than no citation.
- **The Overview does not cite.** `intent`, `scope`, `nonGoal`, `howToRead` and a library `Overview`
  are about the measure or the library, not about lines, and a span there is false precision.

A claim with no citation is a claim nobody can check, and code no claim cites is code nobody has
explained. Both halves are now measured — forward and reverse (see "Total coverage") — and the number
only means something if every field participates.

## Before you finish

Read it back as the clinician. Then check:

1. No banned word, from either list.
2. No `description` restates its define's name, and no `Title` renames one — a gloss says what the
   name does not carry, or it is absent (`SPEC-ELEMENT-GLOSS`).
3. `Intent`, `Scope` and `HowToRead` are present (measure libraries only); the steward's own fields
   were checked before being written over.
4. No included library's prose names a measure, a denominator, a rate, or a condition that library
   does not itself define.
5. Every list says *all* or *any one*.
6. Every date says which end of the period it is anchored to.
7. Every threshold says inclusive or strict.
8. Mirrored criteria have equal treatment.
9. `guidance` names a specific mistake, or is absent.
10. Every prose field on a criterion, Concept or Rule has a `sourceSpan`; no Overview field has one.
11. Every span is a real line range in that define's own body, and covers only lines the field describes.
12. No `Appendix`, `ValueSet`, `Parameter` or `Code` element anywhere in the instance.
13. No run figure is typed as a digit — every one is a `MetricRef`, library-qualified, and the sentence
    still reads when it resolves to nothing.
14. Every criterion names the patients it is about, clinically. Delete the sentences about how the
    measure is built and check that an answer remains.
15. Nothing explains the scoring methodology — no rate formula, no "minus the exclusions below", no
    account of what an exception does to the denominator.
Then close the three coverage directions (`SPEC-EXPLAIN-ACCOUNTING`) — mechanically, one define at a time:

16. **Reverse coverage.** Print the define's body, mark every line your spans cover, and look for unmarked
    lines. No decision line — a `where`, a `such that`, a date bound, a comparison — may be unmarked.
17. **Outward coverage.** List the reference set for the define. Every entry is accounted for in its prose:
    defines and value sets named, reads bound with `<Read>`. Conversion plumbing is covered by its line;
    anything a reviewer could be surprised to find in the code is not.
18. **Metric coverage.** Every element with a resolvable metric cites it exactly once, in the sentence its
    claim bears on. None cites one in every sentence.
19. **No identifier is invented.** Every `profile`, every library-qualified name, every metric key and
    every span traces to the reference set or the ELM. Nothing was read off the CQL text or counted by eye.

Finally, the test the other eighteen serve:

20. **Read the whole document through as the clinician, once, without stopping.** Does it tell a story —
    who this measure is about, what it asks of the practice, where it is unforgiving? A document that
    passes every check above and reads as an annotated inventory has failed the only obligation that
    matters. Coverage is the floor. Comprehension is the product.

Full device vocabulary and its provenance: `doc/narrative-tags.md` sections 3 and 6
(`SPEC-NARRATIVE-TAGS`, `SPEC-NARRATIVE-DEVICES`).
