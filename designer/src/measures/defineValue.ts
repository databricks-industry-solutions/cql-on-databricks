// APP-VALUE-READABLE — how a define's RETURNED VALUE is rendered for a human.
//
// THE PANE RENDERS THE VALUE, and this is the renderer. `insights/profile.py:_top_values` hands the
// client a STRING and does so on purpose: a scalar define returns a CQL Quantity, a list-valued one
// returns a resource, so there is no single field to project, and picking one per FHIR type inside the
// engine boundary would be exactly the knowledge `APP-DECOUPLE` keeps out of that layer. What it
// recorded instead — "the pane truncates and carries the full value in its tooltip" — is this module.
// It is the ONLY value renderer, because three surfaces each formatting a value their own way is how the
// same Quantity comes to read as `{"value":139}` in one place and `139` in another.
//
// THREE THINGS COME OUT OF ONE VALUE, because a 300px pane cannot show a resource at full width:
//
//   summary  one line a clinician reads — "Example Reading A · 12 Mar 2024 · final", not
//            `{"resourceType":"Observation","status":"final","code":{"coding":[{"system":…`.
//   fields   the value's own fields, EMPTY ONES DROPPED, labelled, ordered clinically. Behind a
//            disclosure, because the summary is the answer and the fields are the evidence for it.
//   raw      the value exactly as it arrived, for the tooltip. Nothing is hidden, only demoted.
//
// PRUNING IS THE POINT OF THE WHOLE MODULE. A FHIR resource is a wide typed row and most of it is absent
// on any given record, so the JSON a reader was shown was mostly punctuation around nulls. `false` and
// `0` are VALUES and survive; `null`, `""`, `[]`, `{}` and any branch that reduces to those do not. An
// empty field states nothing, and forty of them state nothing forty times.
//
// THE RECOGNIZERS ARE A CLOSED SET — Quantity, Coding, CodeableConcept, Period/Interval, Reference,
// resource, date — because that is what CQL and the FHIR binding actually return here. This is display
// vocabulary, not classification: nothing downstream branches on which recognizer fired, and an
// unrecognized shape is not an error. It falls to the generic object rendering, which names its own most
// identifying fields rather than inventing semantics it does not have.

/** One labelled field of a value, already rendered. */
export interface ValueField {
  label: string;
  value: string;
  /** Machine data (an id, a reference, a bare code, a figure) rather than words — the design system's
   *  mono rule (`APP-STYLE-GUIDE` section 1) is decided here because only the renderer knows which
   *  a given field turned out to be. */
  code: boolean;
}

/** A define's returned value, read three ways. */
export interface ReadValue {
  summary: string;
  fields: ValueField[];
  raw: string;
}

/** The muted mark for a value that is absent. Never a blank — a blank reads as "nothing" where the
 *  truth is "null", and CQL's three-valued logic makes those different claims. */
const ABSENT = "—";

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v.trim() : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Envelope, provenance and extension fields — dropped from the field list. Not because they are never
 *  interesting, but because they are never the ANSWER, and on a real resource they are most of the
 *  bytes: `meta`, `text` and `extension` alone are what made the raw JSON unreadable. `resourceType` is
 *  dropped because the summary line already carries it. */
const NOISE = new Set([
  "meta", "text", "div", "extension", "modifierExtension", "implicitRules", "language", "contained",
  "resourceType",
]);

/** Keys whose rendered value is machine data, so it rides mono. */
const MACHINE = new Set(["id", "reference", "system", "url", "versionId"]);

/** What a field is CALLED, where the FHIR element name reads as jargon or as a mouthful. Everything not
 *  here is de-camel-cased, which handles the long tail correctly ("clinicalStatus" → "Clinical status"). */
const LABELS: Record<string, string> = {
  effectiveDateTime: "Effective", effectivePeriod: "Effective", effectiveInstant: "Effective",
  occurrenceDateTime: "Occurred", onsetDateTime: "Onset", abatementDateTime: "Resolved",
  recordedDate: "Recorded", authoredOn: "Authored",
  valueQuantity: "Value", valueCodeableConcept: "Value", valueString: "Value",
  valueBoolean: "Value", valueInteger: "Value",
  subject: "Patient", reasonCode: "Reason", component: "Components",
  gender: "Sex", birthDate: "Birth date",
  low: "From", high: "To", start: "From", end: "To",
};

/** The clinical reading order: what the thing IS, what it MEASURED, WHEN, then its state, then its
 *  keys. A resource's own field order is the FHIR spec's, which is alphabetical-ish and puts `id` and
 *  `identifier` above the code — the reverse of what a reviewer scans for. */
const FIELD_ORDER = [
  "code", "type", "class", "category", "medicationCodeableConcept", "vaccineCode",
  "value", "valueQuantity", "valueCodeableConcept", "valueString", "valueBoolean", "valueInteger",
  "component", "interpretation", "referenceRange",
  "effectiveDateTime", "effectivePeriod", "effectiveInstant", "period", "onsetDateTime",
  "occurrenceDateTime", "recordedDate", "authoredOn", "issued", "date",
  "status", "clinicalStatus", "verificationStatus",
  "subject", "patient", "encounter", "performer", "requester", "location",
  "identifier", "id",
];

/** What identifies the thing — the first of these that is present leads the summary line. */
const IDENTIFYING = ["code", "type", "class", "medicationCodeableConcept", "vaccineCode",
  "text", "display", "name"];
/** What it measured. */
const MEASURED = ["valueQuantity", "valueCodeableConcept", "valueString", "valueBoolean",
  "valueInteger", "value"];
/** When it happened. */
const WHEN = ["effectiveDateTime", "effectivePeriod", "effectiveInstant", "period", "onsetDateTime",
  "occurrenceDateTime", "recordedDate", "authoredOn", "issued", "date"];

/** Terminology systems by the name a clinician uses, not by URL. Anything else falls back to the URL's
 *  last path segment, which is what HL7's own CodeSystem URLs put there. */
const SYSTEMS: Record<string, string> = {
  "http://loinc.org": "LOINC",
  "http://snomed.info/sct": "SNOMED CT",
  "http://www.ama-assn.org/go/cpt": "CPT",
  "http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm",
  "http://hl7.org/fhir/sid/icd-10": "ICD-10",
  "http://hl7.org/fhir/sid/icd-10-cm": "ICD-10-CM",
  "http://hl7.org/fhir/sid/icd-9-cm": "ICD-9-CM",
  "http://hl7.org/fhir/sid/cvx": "CVX",
  "http://unitsofmeasure.org": "UCUM",
  "http://hl7.org/fhir/sid/ndc": "NDC",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** FULLY anchored, and a month is required. An unanchored pattern read the leading four digits of the
 *  code `1000-1` as a year and rendered a reading code as "1000" — a value column carries
 *  codes far more often than it carries year-precision dates. */
const ISO_DATE =
  /^(\d{4})-(\d{2})(?:-(\d{2}))?(?:[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;

/** Read one returned value. `v` is the wire's string for a profiled top-N value, or the live object a
 *  run collected — both arrive here, and a string that parses as JSON is treated as the value it
 *  encodes rather than as text about it. */
export function readValue(v: unknown): ReadValue {
  const parsed = parseJson(v);
  const pruned = prune(parsed);
  return {
    summary: summarize(pruned),
    fields: fieldsOf(pruned),
    raw: rawOf(v, parsed),
  };
}

/** Just the line, for a caller with no room for the fields. */
export function summarizeValue(v: unknown): string {
  return summarize(prune(parseJson(v)));
}

/** A string carrying JSON is the value it encodes. A string that is not JSON is a string — that is the
 *  contract, not a fallback: `to_json` is applied to STRUCT columns only, so a scalar define's value
 *  reaches here as its own text and must survive untouched. */
function parseJson(v: unknown): unknown {
  const s = typeof v === "string" ? v.trim() : null;
  if (s === null || !(s.startsWith("{") || s.startsWith("["))) return v;
  try {
    return JSON.parse(s) as unknown;
  } catch {
    return v;
  }
}

/** The value EXACTLY as it arrived, for the tooltip — which is what makes the pruning above a demotion
 *  rather than a loss. It is not re-indented: a value that came off the wire as one line of `to_json`
 *  output stays that line, so what the reader hovers is what the relation holds and not a second
 *  rendering of it with its own opinions. */
function rawOf(original: unknown, parsed: unknown): string {
  if (typeof original === "string") return original;
  if (original == null) return ABSENT;
  if (isObj(parsed) || Array.isArray(parsed)) return JSON.stringify(parsed);
  return String(original);
}

/** Drop everything that states nothing, recursively. `false` and `0` are values and stay. */
function prune(v: unknown): unknown {
  if (v === null || v === undefined) return undefined;
  if (typeof v === "string") return v.trim() === "" ? undefined : v;
  if (Array.isArray(v)) {
    const kept = v.map(prune).filter((x) => x !== undefined);
    return kept.length > 0 ? kept : undefined;
  }
  if (isObj(v)) {
    const kept: Obj = {};
    for (const [k, val] of Object.entries(v)) {
      const p = prune(val);
      if (p !== undefined) kept[k] = p;
    }
    return Object.keys(kept).length > 0 ? kept : undefined;
  }
  return v;
}

/** The one-line reading. */
function summarize(v: unknown): string {
  if (v === undefined) return ABSENT;
  // A boolean define's value IS its membership — `relation.met_expr` makes `true` the predicate and
  // both `false` and `null` "not in". So the words are the pane's own ("N met"), not `true`/`false`.
  if (v === true) return "Met";
  if (v === false) return "Not met";
  const n = num(v);
  if (n !== null) return n.toLocaleString();
  if (typeof v === "string") return date(v) ?? v;
  if (Array.isArray(v)) {
    const rest = v.length - 1;
    return summarize(v[0]) + (rest > 0 ? ` +${rest} more` : "");
  }
  if (isObj(v)) return summarizeObject(v);
  return String(v);
}

/** The FIELD reading. Identical to the summary except for a coded value, where the code itself is what
 *  a reviewer opened the disclosure for — the line above has no room for it and the field does. */
function detail(v: unknown): string {
  // A coded element is repeating far more often than not — `Encounter.type` and `Condition.category`
  // are arrays of CodeableConcept — so the recursion is what makes the code reachable at all.
  if (Array.isArray(v)) {
    const rest = v.length - 1;
    return detail(v[0]) + (rest > 0 ? ` +${rest} more` : "");
  }
  if (isObj(v)) {
    const coding = codingOf(v);
    if (coding) {
      const display = str(coding.display) ?? str(v.text);
      const spelled = spellCode(coding);
      if (display && spelled) return `${display} (${spelled})`;
      return display ?? spelled ?? summarize(v);
    }
  }
  return summarize(v);
}

function summarizeObject(o: Obj): string {
  const quantity = quantityOf(o);
  if (quantity) return quantity;

  const coding = codingOf(o);
  if (coding) return str(coding.display) ?? str(o.text) ?? spellCode(coding) ?? ABSENT;

  const range = rangeOf(o);
  if (range) return range;

  const reference = referenceOf(o);
  if (reference) return reference;

  // Generic — including every resource, whose `resourceType` only leads the line when nothing more
  // specific is present: on an Observation the code display already says what it is, and "Observation ·
  // Example Reading A" spends a third of a 300px row saying so twice.
  const parts: string[] = [];
  const identity = pick(o, IDENTIFYING);
  if (identity !== undefined) parts.push(summarize(identity));
  else {
    const type = str(o.resourceType);
    if (type) parts.push(spaceCamel(type));
  }
  const measured = pick(o, MEASURED);
  if (measured !== undefined) parts.push(summarize(measured));
  const when = pick(o, WHEN);
  if (when !== undefined) parts.push(summarize(when));
  const state = str(o.status) ?? (isObj(o.clinicalStatus) ? summarize(o.clinicalStatus) : null);
  if (state) parts.push(state);
  if (parts.length > 0) return parts.join(" · ");

  // Nothing identifying. Say what the value IS rather than pretending to have read it.
  const keys = Object.keys(o);
  const only = keys.length === 1 ? keys[0] : undefined;
  if (only !== undefined) return `${labelOf(only)} ${summarize(o[only])}`;
  return `${keys.length} fields`;
}

/** A Quantity — CQL's own scalar result and the shape behind every threshold in a measure. */
function quantityOf(o: Obj): string | null {
  const value = num(o.value);
  if (value === null) return null;
  const unit = str(o.unit) ?? str(o.code);
  return unit ? `${value.toLocaleString()} ${unit}` : null;
}

/** The Coding inside a value, whether the value IS one or carries one (`CodeableConcept.coding`).
 *
 *  A RESOURCE is never one, whatever it carries: `Narrative.text` is an object rather than a string so
 *  the text branch below cannot fire on it today, but a resource that ever did carry a text string would
 *  be summarized as its own narrative, which is a different value than the one the reader asked for. */
function codingOf(o: Obj): Obj | null {
  if (str(o.resourceType)) return null;
  if (str(o.code) && (str(o.system) || str(o.display))) return o;
  const coding = o.coding;
  if (Array.isArray(coding) && isObj(coding[0])) return coding[0];
  // A CodeableConcept whose codings were all empty still has its text, which is the human reading.
  if (str(o.text)) return o;
  return null;
}

/** "LOINC X0001" — the code as a reviewer would say it aloud. */
function spellCode(coding: Obj): string | null {
  const code = str(coding.code);
  if (!code) return null;
  const system = str(coding.system);
  return system ? `${systemName(system)} ${code}` : code;
}

function systemName(url: string): string {
  const known = SYSTEMS[url.replace(/\/$/, "")];
  if (known) return known;
  const tail = url.split(/[/#]/).filter(Boolean).pop();
  return tail ?? url;
}

/** A Period, a CQL Interval or a Range — one span, both ends stated, open ends said in words. A bare
 *  "12 Mar 2024" where the truth is "from 12 Mar 2024" is the anchoring error the Timing tab exists to
 *  expose, so it is not made here either. */
function rangeOf(o: Obj): string | null {
  const from = o.start ?? o.low;
  const to = o.end ?? o.high;
  if (from === undefined && to === undefined) return null;
  if (from !== undefined && to !== undefined) return `${summarize(from)} – ${summarize(to)}`;
  return from !== undefined ? `from ${summarize(from)}` : `until ${summarize(to)}`;
}

/** A Reference. Its display is the point; the pointer itself is shortened, since a 36-character UUID
 *  tells a reader only that there is one. */
function referenceOf(o: Obj): string | null {
  const display = str(o.display);
  const reference = str(o.reference);
  if (display) return display;
  if (!reference) return null;
  const [type, id, ...rest] = reference.split("/");
  if (rest.length > 0 || !id) return reference;
  return id.length > 12 ? `${type}/${id.slice(0, 8)}…` : reference;
}

/** A FHIR date, dateTime or instant, in the one written form that is unambiguous in every locale —
 *  "12 Mar 2024", never "03/12/2024". Partial dates are FHIR-legal and are rendered at the precision
 *  they carry rather than padded to a day nobody recorded. The time of day survives when it is stated
 *  and is not midnight: an example reading at 14:30 and one at 08:00 are different readings, and midnight
 *  is overwhelmingly a date that was widened rather than a measurement taken then. */
function date(s: string): string | null {
  const m = ISO_DATE.exec(s.trim());
  if (!m) return null;
  const [, year, month, day, hour, minute] = m;
  const monthIndex = Number(month) - 1;
  if (monthIndex < 0 || monthIndex > 11) return null;
  const parts = [MONTHS[monthIndex], year];
  if (day) parts.unshift(String(Number(day)));
  const written = parts.join(" ");
  return hour && !(hour === "00" && minute === "00") ? `${written} ${hour}:${minute}` : written;
}

/** The labelled fields, empties already gone, in clinical reading order. */
function fieldsOf(v: unknown): ValueField[] {
  if (Array.isArray(v)) {
    // A list-valued define returns several elements and each one is a field of the list.
    return v.map((item, i) => ({ label: String(i + 1), value: detail(item), code: false }));
  }
  if (!isObj(v)) return [];
  // A scalar dressed as an object — a Quantity, a Coding, a Period — is fully said by its own summary
  // line, and repeating `value`/`unit` beneath it is the empty-field noise this module exists to remove.
  if (quantityOf(v) || codingOf(v) || rangeOf(v) || referenceOf(v)) return [];
  return Object.keys(v)
    .filter((k) => !NOISE.has(k))
    .sort((a, b) => order(a) - order(b))
    .map((k) => ({
      label: labelOf(k),
      value: detail(v[k]),
      code: MACHINE.has(k) || num(v[k]) !== null || isQuantity(v[k]),
    }));
}

/** Keys the order list names come first, in its order; the rest keep theirs, after. */
function order(key: string): number {
  const i = FIELD_ORDER.indexOf(key);
  return i === -1 ? FIELD_ORDER.length : i;
}

function isQuantity(v: unknown): boolean {
  return isObj(v) && quantityOf(v) !== null;
}

function labelOf(key: string): string {
  return LABELS[key] ?? spaceCamel(key);
}

function spaceCamel(key: string): string {
  const [first, ...rest] = key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").split(/[\s_]+/).filter(Boolean);
  if (first === undefined) return key;
  return [first[0]!.toUpperCase() + first.slice(1), ...rest.map((w) => w.toLowerCase())].join(" ");
}

/** The first of `keys` the object carries. */
function pick(o: Obj, keys: string[]): unknown {
  for (const k of keys) {
    if (o[k] !== undefined) return o[k];
  }
  return undefined;
}
