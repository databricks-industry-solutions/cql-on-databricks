// KNOWLEDGE-LAKE-DIRECT — the SPA's client for library discovery (via the BFF).
//
// Home's catalog fetches /api/libraries/discover which queries Knowledge Source UC tables
// directly — no server-side cache. Each record maps to the UI's `Definition` shape so the
// Browse/Home/Detail components are unchanged.

import { cachedGet, cachedPost, mutate, postKey, peek } from "./data-cache.ts";
import { currentEnvironment, withEnvironment } from "./environment.ts";
import type { Category, Definition, Status } from "./catalog.ts";
import type { MeasureMetricsDto } from "./measures/specMetrics.ts";
import type { CohortFlowDto, FlowGateRequest } from "./measures/specFlow.ts";
import type { DefineProfile, ResultSetAddress } from "./wire.ts";

/** RUN-CLIENT-REQUEST-REOPEN — compilation request context belongs in the POST body. */
export interface ResultReadScope {
  /** DOC-ENVIRONMENT-PICKER — the environment the calculation ran in. */
  environment: string;
  periodStart?: string;
  periodEnd?: string;
  draft?: string;
  resultSet?: ResultSetAddress;
  /** APP-COMPUTE-TARGET — the SQL warehouse to read on; omitted means the deployment's. */
  warehouseId?: string;
}

/** The record shape returned by GET /api/libraries/discover (PERF-DISCOVER-LIGHTWEIGHT). */
interface LibraryRecordDto {
  id: { name: string; version: string };
  /** RUN-ADDRESS-LOCATION — the store or folder this copy is read from. */
  location: string;
  kind: "measure" | "library";
  status: "draft" | "published";
  /** The FHIR model name from the ``using`` directive (e.g. "FHIR"). */
  model?: string;
  /** Human-readable title from the FHIR Measure resource (e.g. "Example Measure"). */
  title?: string;
  metadata: { description: string; tags: string[]; author: string };
  updated: string;
}

function shortDate(iso: string): string {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? new Date(t).toLocaleDateString(undefined, { month: "short", year: "numeric" }) : "";
}

/** Map a library record to the catalog's `Definition` (the components' shape).
 *  PERF-DISCOVER-LIGHTWEIGHT — source is NOT included; lazy-loaded via fetchLibrarySource. */
export function toDefinition(r: LibraryRecordDto): Definition {
  const cat: Category = r.kind === "library" ? "lib" : "measure";
  const status: Status = r.status === "published" ? "pub" : "draft";
  const qualitative =
    r.kind === "library" ? "reusable" : r.status === "published" ? "Ready to run" : "Not calculated";
  const year = r.updated ? new Date(r.updated).getFullYear().toString() : undefined;
  const base = `${r.kind === "library" ? "Shared library" : "eCQM"} · v${r.id.version}`;
  return {
    id: `${r.id.name}@${r.id.version}`,
    name: r.id.name,
    version: r.id.version,
    location: r.location,
    title: r.title || undefined,
    src: r.model ? `${base} · ${r.model}` : base,
    cat,
    status,
    stat: qualitative,
    statk: "",
    mine: r.metadata.author === "you",
    meta: `Updated ${shortDate(r.updated)}${r.metadata.author ? ` · ${r.metadata.author}` : ""}`,
    desc: r.metadata.description,
    year,
    // source omitted — lazy-loaded by fetchLibrarySource (PERF-DISCOVER-LIGHTWEIGHT).
  };
}

/** RUN-ADDRESS-LOCATION — `url` read at `location`, in the picked environment (`DOC-ENVIRONMENT-PICKER`). A folder
 *  is never served from the page's cache: the server's revision tracks the environment's knowledge, and a workspace
 *  file changes without moving it. */
function readAt<T>(path: string, location: string | null | undefined,
                   parse: (res: Response) => Promise<T>): Promise<T> {
  const url = withEnvironment(path);
  if (!location) return cachedGet(url, parse);
  const at = `${url}${url.includes("?") ? "&" : "?"}location=${encodeURIComponent(location)}`;
  return location.startsWith("folder:") ? fetch(at).then(parse) : cachedGet(at, parse);
}

/** Fetch the definitions in the picked environment's knowledge (KNOWLEDGE-LAKE-DIRECT), or those at one
 *  location (RUN-ADDRESS-LOCATION): the environment's knowledge, or one folder. */
export async function fetchDefinitions(location?: string | null): Promise<Definition[]> {
  return readAt("/api/libraries/discover", location, async (res) => {
    if (!res.ok) throw new Error(`library discover failed: ${res.status}`);
    const records = (await res.json()) as LibraryRecordDto[];
    return records.map(toDefinition);
  });
}

/** Lazy-load a single library's CQL source (PERF-DISCOVER-LIGHTWEIGHT), from its location. */
export async function fetchLibrarySource(name: string, version: string, location?: string | null): Promise<string> {
  return readAt(
    `/api/libraries/${encodeURIComponent(name)}/${encodeURIComponent(version)}/source`, location,
    async (res) => {
      if (!res.ok) throw new Error(`library source fetch failed: ${res.status}`);
      const body = (await res.json()) as { source: string };
      return body.source;
    });
}

/** DOC-OPEN-SAVE — where a library can be opened from: the picked environment's knowledge, and folders to browse
 *  from. */
export interface Locations {
  stores: { location: string; catalog: string; schema: string }[];
  folders: { location: string; label: string }[];
}

export async function fetchLocations(): Promise<Locations> {
  const res = await fetch(withEnvironment("/api/locations"));
  if (!res.ok) throw new Error(`locations failed: ${res.status}`);
  return (await res.json()) as Locations;
}

/** DOC-OPEN-SAVE — one workspace folder: its subfolders, and the libraries it holds when it holds CQL. */
export interface FolderListing {
  path: string;
  location: string;
  folders: { name: string; path: string }[];
  libraries: Definition[];
}

export async function fetchFolder(path: string): Promise<FolderListing> {
  const res = await fetch(`/api/folders?path=${encodeURIComponent(path)}`);
  if (!res.ok) {
    const body = await res.json().catch(() => null) as { error?: string } | null;
    throw new Error(body?.error ?? `folder failed: ${res.status}`);
  }
  const body = (await res.json()) as Omit<FolderListing, "libraries"> & { libraries: LibraryRecordDto[] };
  return { ...body, libraries: body.libraries.map(toDefinition) };
}

// ── SPEC-MEASURE-METADATA — the Specification's prose for a measure ─────────
//
// Hand-mirrors the server dataclasses in `cql_databricks_designer/specification/model.py`, which in turn mirror
// `schema/specification.xsd` (SPEC-XSD). When these three disagree, the XSD is right.
//
// PROSE ONLY, keyed library-qualified. Structure, polarity, metrics, source links and the Appendix
// are derived from the ELM in `specDocument.ts`; a second copy of them here could disagree with the
// first, so there isn't one.
//
// The server composes two sources into this one payload — the measure's published FHIR resource
// (`authored`, quoted verbatim and attributed) and the hand-written narration instance (`narrated`).
// Which one a field came from is on the field, so the page can say so.

/** One prose element. `provenance` decides how it renders: prose its author published is an
 *  assertion, prose interpreted about the measure is a draft, and a reader must never have to guess
 *  which they are reading (APP-NARRATIVE-PROVENANCE). */
/** SPEC-NARRATIVE-DEVICES — one line of a rule set, with its own citation.
 *
 *  SPEC-READ-REF — and its own `segments`, present only when the bullet holds something that is not
 *  prose. A bullet is where a read is most often named ("recorded on the problem list or as an
 *  encounter diagnosis"), so the binding has to work inside one and not only in the lead sentence. */
export interface SpecListItem { text: string; sourceSpan?: string; segments?: SpecSegment[] }

/** Include / Exclude blocks, or a plain rule set. Two lists rather than one paragraph alternating
 *  between them, because two blocks show the reader there are two decisions. */
export interface SpecListBlock {
  block: "list";
  kind: "include" | "exclude" | "plain";
  title?: string;
  items: SpecListItem[];
}

/** A code table — the most checkable device there is. `checkAgainst` names what the rows must match
 *  (a value set, or the define whose ELM literal they transcribe). */
export interface SpecTableBlock {
  block: "table";
  columns: string[];
  rows: string[][];
  rowSpans?: (string | null)[];
  title?: string;
  checkAgainst?: string;
}

export type SpecBlock = SpecListBlock | SpecTableBlock;

/** SPEC-METRIC-REF — a figure CITED in prose, substituted by the renderer from the metric set.
 *
 *  The instance carries the reference and never the number, which is what makes a narrated quantity
 *  falsifiable: a narrator who may only cite a figure cannot state a wrong one, and prose built around
 *  a reference does not go stale when the next run moves it. */
export interface SpecMetricRef {
  kind: "metric";
  library: string;
  define: string;
  /** Which of the metric's two grains the sentence means (`SPEC-METRIC`). */
  grain: "subject" | "element";
  /** `count` is the figure; `share` is it as a percentage of the define's base. */
  as: "count" | "share";
}

/** SPEC-READ-REF — a phrase in the prose that NAMES A READ, bound to the retrieve it names.
 *
 *  The same discipline as `SpecMetricRef`, applied to data requirements: the instance carries the
 *  BINDING and the words, never a count. The renderer resolves the binding against the retrieve model
 *  derived from the ELM — the same model the CQL's own retrieve tokens resolve through — so a chip in
 *  the prose and a token in the code can never mean different reads.
 *
 *  `text` is the PROSE ("the problem list"), because the sentence has to go on reading as a sentence;
 *  `profile` is the identifier the compiler emits as `templateId`. A binding that resolves to no
 *  retrieve renders as plain prose: degraded, never wrong. */
export interface SpecReadRef {
  kind: "read";
  text: string;
  /** QICore / US Core profile, last segment only — `qicore-condition-encounter-diagnosis`. */
  profile?: string;
  /** Unqualified FHIR type, for an unprofiled read. Resolves to the first read of that type. */
  type?: string;
  /** SPEC-INLINE-ANCHOR — the line of the enclosing define where this read is issued, so a bullet naming
   *  two reads anchors each to its own line instead of carrying one widened span over both. */
  sourceSpan?: string;
}

/** SPEC-DEFINE-REF — a phrase in the prose that NAMES ANOTHER ELEMENT, bound to the define it names.
 *
 *  The third and last of the inline bindings, and the one that closes the asymmetry the other two made
 *  visible: a define was followable inside a CQL block and inert in the sentence explaining it, which is
 *  backwards for a document whose premise is that the reader does not need the code
 *  (`SPEC-EXPLAIN-ACCOUNTING`). `ConceptRef` / `RuleRef` had been in the schema for exactly this since
 *  the first draft; the loader flattened them until now.
 *
 *  `text` is the PROSE ("primary example condition diagnosis"), not the declared name — substituting the
 *  latter would put `Example Condition Diagnosis` in title case mid-sentence. Library-qualified,
 *  because two libraries may declare one name. Resolving to nothing renders as plain prose. */
export interface SpecDefineRef {
  kind: "define";
  text: string;
  library: string;
  define: string;
  /** The narrator's assertion of what kind of element this is, for the type chip. The document's own
   *  structure wins where they disagree — this is a pointer, not a second definition. */
  elementKind: "concept" | "rule";
  /** SPEC-INLINE-ANCHOR — the reference site's line. ABSENT for a transitive mention: a phrase naming
   *  something two steps down the chain has no line in this body, and the reference is its own anchor. */
  sourceSpan?: string;
}

/** A stretch of plain prose between citations. */
export interface SpecTextRun { kind: "text"; text: string }

/** SPEC-PARAMETER-REF — an execution parameter's VALUE cited in prose.
 *
 *  The only inline reference that needs no run: a parameter is an execution INPUT selected in the compute
 *  pill (`APP-EXEC-PARAMS`), so `the <ref/> measurement period` renders as *the 2026 measurement period*
 *  in Static exactly as in Live. It carries the CQL parameter's name and never its value, because a typed
 *  year is wrong the moment anyone steps the period. Unresolved, it renders nothing. */
export interface SpecParameterRef {
  kind: "parameter";
  /** The CQL parameter's declared name — `Measurement Period`. */
  name: string;
}

/** SPEC-TERMINOLOGY-REF — a phrase in the prose that NAMES A VALUE SET or a DIRECT REFERENCE CODE.
 *
 *  The last gap in `SPEC-EXPLAIN-ACCOUNTING`'s outward coverage, and the one a clinical reviewer is most
 *  likely to want: terminology decides which patients match, and it was the only linkable thing accounted
 *  for in prose alone — the OID sat in the Appendix with no way to reach it from the sentence naming it,
 *  while the identical name inside the CQL block was followable.
 *
 *  ONE KIND FOR BOTH, resolved out of the Appendix by `(library, name)`; whether it is a value set or a
 *  code is DERIVED there rather than declared here, because that is a fact the ELM states and
 *  `SPEC-EXPLAIN-INPUTS` forbids a narrator to write identifiers the compiler established.
 *
 *  `text` is the PROSE ("an example condition diagnosis"), `name` is the DECLARATION (`Example Condition Diagnoses`).
 *  Library-qualified, because value-set names repeat across an include closure and resolving to the wrong
 *  OID is a clinical error, not a navigation one. Never carries the OID. Unresolved, it renders as prose. */
export interface SpecTerminologyRef {
  kind: "terminology";
  text: string;
  library: string;
  name: string;
  /** SPEC-INLINE-ANCHOR — the line where this define USES the terminology, not the header that declares
   *  it: the declaration is what the reference already leads to. */
  sourceSpan?: string;
}

export type SpecSegment =
  SpecTextRun | SpecMetricRef | SpecReadRef | SpecDefineRef | SpecParameterRef | SpecTerminologyRef;

export interface NarratedText {
  /** The PLAIN-TEXT projection — a nav summary, a catalogue row, a title attribute. A cited figure
   *  reads as "—" here, because a one-line summary has no metric set to resolve against. */
  text: string;
  /** The inline content, present ONLY where some of it is not prose. Prose with nothing to resolve is
   *  fully described by `text`, so the wire does not carry a single-run copy of every sentence; the
   *  renderer normalises the absent case to one run and therefore still has one path. */
  segments?: SpecSegment[];
  /** Device blocks following the lead prose, in document order. */
  blocks?: SpecBlock[];
  /** SPEC-NARRATIVE-DEVICES — the lines of the define's CQL this prose is ABOUT ("2-5"). Provenance
   *  says whose words these are; this says what they describe, which is what makes them checkable. */
  sourceSpan?: string;
  provenance: "authored" | "narrated" | "derived";
  /** WHO asserted it — set on `authored` prose ("Example Measure Steward"). */
  author?: string;
  /** WHERE it was quoted from, `canonical|version`. What makes the quotation checkable. */
  sourceRef?: string;
  /** A separate axis UNDER `narrated` — how the prose was produced, not what it is. */
  method?: "ai-drafted" | "human-written";
  reviewState?: "unreviewed" | "reviewed" | "accepted";
  reviewedBy?: string;
  reviewedOn?: string;
  /** SPEC-NARRATIVE-TAGS — the CQL annotation tag this prose belongs in (`intent`, `guidance`, …).
   *  What Accept writes back to, and what makes the tag alignment machine-checkable. */
  sourceTag?: string;
}

/** The narration triple (+ title) for one define, whatever role it plays in the document. A reader
 *  asks the same three questions of a population, a Concept and a Rule (designer/docs/narrative-tags.md). */
export interface SpecNarrationDto {
  title?: NarratedText;
  /** WHAT it is. */
  description?: NarratedText;
  /** WHY it matters clinically. */
  rationale?: NarratedText;
  /** HOW TO READ IT — boundaries, tie-breaks, the misreading people actually make. */
  guidance?: NarratedText;
}

/** `Overview/Intent` — why the measure exists, the clinical reasoning behind it, and the guideline it
 *  operationalises. Three separately authored fields, never concatenated. */
export interface SpecIntent {
  statement?: NarratedText;
  clinicalRationale?: NarratedText;
  recommendation?: NarratedText;
}

/** `Overview/ScopeNonGoals` — the boundary. `nonGoals` is a list because each is cited and reviewed
 *  on its own; an absent list means nobody wrote one down, not that there are none. */
export interface SpecScopeNonGoals {
  inScope?: NarratedText;
  nonGoals?: NarratedText[];
}

export interface SpecOverview {
  description?: NarratedText;
  intent?: SpecIntent;
  scope?: SpecScopeNonGoals;
  /** The measure AUTHOR's "how to read it" (`Measure.usage`) — how the logic is meant to be applied.
   *  Distinct from `howToRead`, which is about this document and is the product's voice. */
  guidance?: NarratedText;
  howToRead?: NarratedText;
}

/** One `Measure.relatedArtifact` — the evidence the measure cites. */
export interface SpecCitation { label?: string; citation?: string; url?: string }

/** One measure's prose, ready to join onto the derived document by `"<library>::<define>"`. */
export interface SpecificationOverlay {
  library: string;
  version: string;
  title?: string;
  /** SPEC-SCORING-METHODOLOGY — the DECLARED scoring type (`Identity/@scoring`). The document explains
   *  how the measure is scored from this rather than inferring it from which populations exist; absent
   *  means it says nothing about scoring. */
  scoring?: string;
  generated?: string;
  /** Which artifact wins when this document and the CQL appear to disagree. */
  precedence?: string;
  overview: SpecOverview;
  /** "<library>::<define>" -> that define's narration. Library-qualified because two libraries may
   *  declare one name, and an unqualified key attaches the wrong prose to the wrong define. */
  elements: Record<string, SpecNarrationDto>;
  libraryOverviews: Record<string, NarratedText>;
  /** SPEC-LICENSE — the steward's terms, verbatim. Shown with the document, never summarised. */
  copyright?: NarratedText;
  disclaimer?: NarratedText;
  citations?: SpecCitation[];
}

/** SPEC-ANCHOR — the overlay's join key. Must match `element_key()` on the server exactly. */
export function overlayKey(library: string, define: string): string {
  return `${library}::${define}`;
}

/** Fetch the Specification overlay for a library. Null when none is published for it — most
 *  libraries have none, and the document then renders from the CQL alone. */
export async function fetchSpecification(name: string, version: string,
                                         location?: string | null): Promise<SpecificationOverlay | null> {
  return readAt(
    `/api/libraries/${encodeURIComponent(name)}/${encodeURIComponent(version)}/specification`, location,
    async (res) => (res.ok ? ((await res.json()) as SpecificationOverlay) : null));
}

/** SPEC-DATA-REQUIREMENTS — one `Library.dataRequirement` entry: what the logic READS.
 *
 *  Mirrors the FHIR element, not a shape of our own, because that is what the CQL translator emits and
 *  what the published eCQM package ships. The grain is one per (type, profile, filter) TRIPLE, not one
 *  per resource type: a QI-Core measure reads Condition twice — as problems-health-concerns and as
 *  encounter-diagnosis — and those are different data with different bindings. */
export interface DataRequirement {
  /** FHIR resource type: `Condition`, `Encounter`, `Observation`. */
  type: string;
  /** QICore / US Core profile canonicals. What distinguishes two reads of one resource type. */
  profile?: string[];
  /** Element paths the logic actually touches — what a binding has to supply. Not recoverable from a
   *  source scan, and the reason this comes from the translator rather than a regex. */
  mustSupport?: string[];
  codeFilter?: { path?: string; valueSet?: string; code?: { code?: string; system?: string; display?: string }[] }[];
  dateFilter?: { path?: string; valuePeriod?: { start?: string; end?: string } }[];
}

/** Fetch what a library reads. `[]` is a real answer — a library of pure type conversions reads
 *  nothing — so it is not distinguished from a library whose resource carries none. */
export async function fetchDataRequirements(name: string, version: string,
                                            location?: string | null): Promise<DataRequirement[]> {
  return readAt(
    `/api/libraries/${encodeURIComponent(name)}/${encodeURIComponent(version)}/data-requirements`, location,
    async (res) => {
      if (!res.ok) return [];
      const body = (await res.json()) as { dataRequirements?: DataRequirement[] };
      return body.dataRequirements ?? [];
    });
}

// ── APP-DATA-PROFILE — one define's data profile ───────────────────────────────────────────────

/** Fetch the data profile for one define: the sample of returned values, the value profile, code-hit
 *  frequency, temporal spread, and the demographic and date stratifications.
 *
 *  Library-qualified because two libraries may declare one name (`SPEC-ANCHOR`), so an unqualified
 *  request would profile the wrong define.
 *
 *  THE SCOPE IS PART OF THE QUESTION, as it is for the metric set: a profile is a fact about ONE
 *  evaluation — one data source over one measurement period — so the same define has different true
 *  distributions per scope, and the server cannot locate the result relation without it. A profile read
 *  from a different evaluation than the figures rendered above it is the two-surfaces-disagreeing
 *  failure `APP-SPEC-MODEL` names, which is why the scope is required rather than defaulted.
 *
 *  Returns `null` rather than throwing: the profile is an ENRICHMENT of a document that reads without
 *  it, so its absence is a section that does not render, not a page that fails
 *  (the same split `/data-requirements` and `/specification` already draw). That includes the 409 for a
 *  define this evaluation never materialized. The provenance on the payload — now per BLOCK, since some
 *  are derived and some are still sampled — is the caller's to surface, never this function's to hide. */
function defineProfileUrl(
  name: string, version: string, define: string,
): string {
  return `/api/libraries/${encodeURIComponent(name)}/${encodeURIComponent(version)}`
    + `/defines/${encodeURIComponent(define)}/profile`;
}

export async function fetchDefineProfile(
  name: string, version: string, define: string, defineLibrary: string,
  scope: ResultReadScope,
): Promise<DefineProfile | null> {
  return cachedPost(
    defineProfileUrl(name, version, define), { defineLibrary, ...scope },
    async (res) => (res.ok ? ((await res.json()) as DefineProfile) : null));
}

/** What the data cache already holds for this profile, synchronously, or `undefined` on a miss
 *  (`PERF-CLIENT-REVISION`).
 *
 *  Exported from HERE, beside the fetch, because both derive the key from ONE url builder. A caller
 *  that rebuilt the URL to peek would be a second definition of the cache key, free to drift from the
 *  one the fetch writes under — and the failure would be a permanent miss that looks like a cold cache.
 *
 *  `undefined` for a miss, never `null`: a cached profile may legitimately BE `null` (a define this
 *  evaluation never materialized, the 409), and collapsing the two would refetch that absence forever.
 */
export function peekDefineProfile(
  name: string, version: string, define: string, defineLibrary: string,
  scope: ResultReadScope,
): DefineProfile | null | undefined {
  return peek<DefineProfile | null>(
    postKey(defineProfileUrl(name, version, define), JSON.stringify({ defineLibrary, ...scope })));
}

// ── every figure the Specification renders ────────────────────────────────

/** The three figure reads' shared status handling.
 *
 *  A 409 is the server's refusal for this scope — most often "nothing computed here yet" — and is a
 *  real answer: the document reads without figures, so it is `null` and renders as absence. Any other
 *  failure is a fault and throws with the server's error text, so the caller can show it. */
async function figures<T>(res: Response): Promise<T | null> {
  if (res.ok) return (await res.json()) as T;
  if (res.status === 409) return null;
  const body = await res.json().catch(() => null) as { error?: string } | null;
  throw new Error(body?.error ?? `figures failed: ${res.status}`);
}

/** APP-COHORT-ALGEBRA — where the cohort went at each gate, and which criterion was decisive.
 *
 *  A POST for the same reason the metric read is one: the GATES ARE THE QUESTION. The client owns the
 *  decomposition (`SPEC-CONCEPTS`, `specDocument.ts`) and re-reads it into the body; the server has no
 *  ELM graph and adds only figures. Null on a 409 — the rail simply does not render (`figures`).
 */
export async function fetchCohortFlow(
  name: string, version: string,
  gates: FlowGateRequest[],
  scope: ResultReadScope,
): Promise<CohortFlowDto | null> {
  if (gates.length === 0) return null;
  return cachedPost(
    `/api/libraries/${encodeURIComponent(name)}/${encodeURIComponent(version)}/flow`,
    { gates, ...scope },
    figures<CohortFlowDto>);
}

/** Fetch the whole measure's metric set: the two grains per define, plus the evaluated-subject total.
 *
 *  ONE REQUEST FOR THE WHOLE DOCUMENT, not one per define. `SPEC-ATTRITION` renders each figure beside
 *  its base, so the numbers have to be coherent — a Denominator larger than the Initial Population it
 *  narrows is not a rounding difference — and coherence cannot be assembled from independent requests.
 *
 *  A POST because the body IS the question: the client sends the defines and their ELM result types,
 *  since it owns that determination (`APP-SPEC-MODEL`) and a server-side re-derivation could
 *  disagree with it.
 *
 *  THE SCOPE IS PART OF THE QUESTION TOO. A figure is a fact about one complete selection — one data source
 *  over one measurement period — so the same measure has different true numbers per scope, and the server
 *  cannot even locate the result tables without it (`RUN-CLIENT-RESULT-SETS`). Required, not optional:
 *  a request without it has no answer rather than a default one.
 *
 *  `null` for the 409 the server returns when it has no figures for this scope (`figures`).
 *  Any other failure throws with the server's error. */
export async function fetchMeasureMetrics(
  name: string, version: string,
  defines: {
    name: string; library: string; resultType: string | null;
    /** SPEC-POPULATION-CASCADE — the cohort the define is counted among (`specMetrics.MetricCohort`). */
    cohort: { allOf: { name: string; library: string }[]; noneOf: { name: string; library: string }[] };
  }[],
  scope: ResultReadScope,
  /** RUN-CLIENT-MEASURE-SCORE — the declared scoring the server scores under; null returns no score. */
  scoring: string | null = null,
): Promise<MeasureMetricsDto | null> {
  return cachedPost(
    `/api/libraries/${encodeURIComponent(name)}/${encodeURIComponent(version)}/metrics`,
    { defines, ...scope, scoring },
    figures<MeasureMetricsDto>);
}

/** SPEC-READ-REF — how much data is behind each READ, from the tables the engine materialized for
 *  them (`RUN-READ-BARRIER`). Keyed by the client's own retrieve key, so the join needs no knowledge
 *  of table names on either side.
 *
 *  A read the evaluation did not materialize is simply ABSENT from the response — never zero
 *  (`SPEC-METRIC`). Materialization is an override that is off by default, so an empty set is the
 *  ordinary case rather than a fault, and `null` here is the server's 409 (`figures`). */
export interface ReadMetricDto { key: string; subjects: number; elements?: number }
export interface ReadMetricsDto {
  library: string;
  version: string;
  provenance: string;
  generated: string;
  reads: ReadMetricDto[];
}

export async function fetchReadMetrics(
  name: string, version: string,
  reads: { key: string; type: string; profiles: string[] }[],
  scope: ResultReadScope,
): Promise<ReadMetricsDto | null> {
  return cachedPost(
    `/api/libraries/${encodeURIComponent(name)}/${encodeURIComponent(version)}/reads`,
    { reads, ...scope },
    figures<ReadMetricsDto>);
}

// ── DOC-OPEN-SAVE — save a library where it was opened ────────────────────────────────────────────

/** The digest a save compares (`save.py`'s `cql_sha256`): the CQL's UTF-8 bytes, SHA-256, hex. */
export async function cqlSha256(cql: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(cql));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export interface SaveBody {
  location: string;
  name: string;
  version: string;
  cql: string;
  /** The digest of the CQL as opened; null for a library that was never saved. */
  baseSha256: string | null;
  /** The edited `Measure` resource, whole, when the Overview was edited. */
  measure?: Record<string, unknown> | null;
  /** Out-of-date copies the user agreed to delete. */
  deleteStale?: string[];
}

/** What a save answered. Only `saved` wrote anything. DOC-AUTO-SAVE — CQL that does not compile is written too,
 *  with no ELM: `compiled` is false and `diagnostics` says why. */
export type SaveOutcome =
  | { kind: "saved"; location: string; saved: string[]; deleted: string[]; sha256: string; compiled: boolean;
      diagnostics: string[] }
  | { kind: "stale"; paths: string[] }
  | { kind: "conflict"; error: string }
  | { kind: "refused"; error: string };

export async function saveLibrary(body: SaveBody): Promise<SaveOutcome> {
  // `mutate`: a store save moves the server's revision, which must clear what the page cached from that store.
  const res = await mutate("/api/libraries/save", {
    method: "POST", headers: { "content-type": "application/json" },
    // DOC-ENVIRONMENT-PICKER — includes the folder does not hold resolve in the picked environment.
    body: JSON.stringify({ ...body, environment: currentEnvironment() }),
  });
  const answer = await res.json().catch(() => ({})) as Record<string, unknown>;
  if (res.ok) return { kind: "saved", ...(answer as Omit<Extract<SaveOutcome, { kind: "saved" }>, "kind">) };
  const error = String(answer.error ?? `save failed: ${res.status}`);
  if (res.status === 409 && answer.kind === "stale") return { kind: "stale", paths: answer.paths as string[] };
  if (res.status === 409) return { kind: "conflict", error };
  return { kind: "refused", error };
}

/** DOC-PUBLISH — what publishing did with each library, dependencies first, the one published last
 *  (`CLIENT-PUBLISH`). */
export interface PublishSummary {
  store: string;
  libraries: { name: string; version: string; url: string;
               outcome: "published" | "unchanged" | "refused" | "not written"; detail: string[] }[];
}

/** `refused` wrote nothing; `failed` wrote the libraries the summary marks published, then stopped. */
export type PublishOutcome =
  | { kind: "published"; summary: PublishSummary }
  | { kind: "refused" | "failed"; error: string; summary: PublishSummary | null };

/** DOC-PUBLISH — publish the folder library at `location` into the picked environment's knowledge store, as the
 *  signed-in user (`DOC-ENVIRONMENT-PICKER`). */
export async function publishLibrary(body: { location: string; name: string; version: string }):
    Promise<PublishOutcome> {
  // `mutate`: publishing moves the server's revision, which must clear what the page cached from the store.
  const res = await mutate("/api/libraries/publish", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...body, environment: currentEnvironment() }),
  });
  const answer = await res.json().catch(() => ({})) as Record<string, unknown>;
  if (res.ok) return { kind: "published", summary: answer as unknown as PublishSummary };
  return { kind: res.status === 400 ? "refused" : "failed", error: String(answer.error ?? `publish failed: ${res.status}`),
           summary: (answer.summary as PublishSummary | null) ?? null };
}

/** DOC-OPEN-SAVE — New and Save As: create a library at `location`. New starts from the server's starting CQL
 *  (a measure gets a starting `Measure`); Save As sends the open library as `copyOf` and its CQL as edited. */
export interface NewBody {
  location: string;
  name: string;
  version: string;
  kind?: "measure" | "library";
  cql?: string;
  copyOf?: { location: string; name: string; version: string };
}

/** A create never deletes, so it never asks about stale copies. */
export type CreateOutcome = Exclude<SaveOutcome, { kind: "stale" }>;

export async function createLibrary(body: NewBody): Promise<CreateOutcome> {
  // `mutate`: a library created in a store moves the server's revision, which must clear the cached catalog.
  const res = await mutate("/api/libraries/new", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...body, environment: currentEnvironment() }),
  });
  const answer = await res.json().catch(() => ({})) as Record<string, unknown>;
  if (res.ok) return { kind: "saved", ...(answer as Omit<Extract<SaveOutcome, { kind: "saved" }>, "kind">) };
  const error = String(answer.error ?? `create failed: ${res.status}`);
  if (res.status === 409) return { kind: "conflict", error };
  return { kind: "refused", error };
}
