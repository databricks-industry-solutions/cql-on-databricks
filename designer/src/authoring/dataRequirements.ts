// PHASE-S2/S5 / APP-DATA-REQS — the data-requirements ANALYSIS behind the Data requirements view.
//
// GOAL: the real analysis comes from the engine's retrieve/`RetrieveHints` surface (DATA-RETRIEVE-VIEW,
// the compiler area) via the OSS `cql-databricks` `describe` call (browser → BFF → Compiler Service).
// That surface reports, per retrieve, the STATICALLY-DETERMINED pushdown predicates the optimizer knows
// at compile time: terminology semi-joins (`valueSets`), single-code membership (`codes`), arbitrary
// column predicates (`columnPredicates` — date-range / period windows), and the projected columns
// (`project`). Value sets additionally expand to their code lists + code systems via terminology.
//
// That plumbing (describe → client → BFF /api/describe) is NOT wired yet, so this module MOCKS an
// analysis of the SAME shape (APP-DECOUPLE — a client-side stand-in, no engine dependency). What it
// can read from source, it reads for real: the value set bound to each retrieve (`[Type: "VS"]`) and the
// declared value-set canonicals. What needs the compiled ELM (date windows, projected columns, code
// expansions) is synthesized DETERMINISTICALLY from the resource type / value-set name, so the same
// measure always renders the same requirements. When the client surface lands, `analyzeDataRequirements`
// becomes a thin adapter over the `describe` response and this synthesis is dropped.

import { parseDefinitions } from "./logic.ts";

/** A single pushdownable predicate on a retrieve — mirrors a `RetrieveHints` entry (DATA-RETRIEVE-VIEW). */
export interface Predicate {
  /** valueset = terminology semi-join · code = single-code membership · date = period/date-range window
   *  · column = any other resource-rooted column predicate. */
  kind: "valueset" | "code" | "date" | "column";
  /** The resource property the predicate is rooted at (e.g. "code", "onset", "status"). */
  path: string;
  /** Plain-language predicate shown to the author (APP-AUDIENCE) — no query-plan jargon. */
  text: string;
  /** For a value-set predicate, the bound value set's node id (`vs:Name`) so the UI can cross-link. */
  valueSetId?: string;
  /** True when the analysis MOCKED this (needs the compiled ELM) rather than reading it from source. */
  mocked?: boolean;
}

/** Per-FHIR-resource requirement: the retrieve + the predicates that filter/narrow it. */
export interface RetrieveRequirement {
  id: string;
  type: string;
  /** How many criteria read this resource (source-derived). */
  dependents: number;
  predicates: Predicate[];
  /** Projected columns the read can be narrowed to (`RetrieveHints.project`); mocked. */
  project: string[];
}

export interface Code {
  code: string;
  display: string;
  /** Code-system display name (e.g. "SNOMED CT"). */
  system: string;
}

/** A value set expanded to its codes + code systems — the terminology side of the requirements. */
export interface ValueSetRequirement {
  id: string;
  name: string;
  /** A synthetic OID (or the declared canonical when it is a `urn:oid:`). */
  oid: string;
  /** The declared canonical URL from `valueset "X": '<url>'`, when present. */
  canonical: string | null;
  /** Distinct code-system display names across the expansion. */
  codeSystems: string[];
  codes: Code[];
  /** How many criteria reference this value set (source-derived). */
  dependents: number;
  /** The expansion is mocked (real expansions come from terminology via the client). */
  mocked: boolean;
}

export interface DataRequirements {
  retrieves: RetrieveRequirement[];
  valueSets: ValueSetRequirement[];
}

// ── source scan ────────────────────────────────────────────────────────────
// Declared value sets: `valueset "Name": 'canonical'`.
const VALUESET_DECL_RE = /\bvalueset\s+"([^"]+)"\s*:\s*'([^']*)'/g;
// A retrieve with an optional code filter: [Type], [Type: "VS"], [Type: prop in "VS"].
const RETRIEVE_RE = /\[\s*([A-Z][A-Za-z0-9]*)\s*(?::\s*([^\]]*?))?\s*\]/g;
// Inside a retrieve filter, the bound value-set name (quoted) and the property it binds (before `in`/`:`).
const FILTER_VS_RE = /(?:([A-Za-z][\w.]*)\s+in\s+)?"([^"]+)"/;

const vsId = (name: string) => `vs:${name}`;
const rtId = (type: string) => `rt:${type}`;

/** The temporal property each clinical resource is windowed on, and the plain-language window text.
 *  Compile-time date-range pushdown (`columnPredicates`) — mocked here (needs the compiled query). */
const TEMPORAL_FIELD: Record<string, string> = {
  Condition: "prevalencePeriod",
  Encounter: "period",
  Observation: "effective",
  Procedure: "performed",
  MedicationRequest: "authoredOn",
  MedicationDispense: "whenHandedOver",
  MedicationStatement: "effective",
  Immunization: "occurrence",
  DiagnosticReport: "effective",
  Claim: "billablePeriod",
};

/** Analyze a CQL library's data requirements (APP-DATA-REQS). Value-set bindings + declared
 *  canonicals are read from source; date windows, projected columns, and code expansions are mocked
 *  deterministically until the engine `describe` surface is wired (see module header). */
export function analyzeDataRequirements(cql: string): DataRequirements {
  const canonicals = new Map<string, string>();
  for (let m = VALUESET_DECL_RE.exec(cql); m !== null; m = VALUESET_DECL_RE.exec(cql)) {
    canonicals.set(m[1]!, m[2] ?? "");
  }

  const defs = parseDefinitions(cql);

  // Per retrieve type: dependents (criteria that read it) + the value sets bound to it, from source.
  const retrieves = new Map<string, { dependents: Set<string>; valueSets: Set<string>; codeProp: string }>();
  const vsDependents = new Map<string, Set<string>>();

  for (const d of defs) {
    for (let m = RETRIEVE_RE.exec(d.cql); m !== null; m = RETRIEVE_RE.exec(d.cql)) {
      const type = m[1]!;
      const filter = m[2] ?? "";
      const entry = retrieves.get(type) ?? { dependents: new Set(), valueSets: new Set(), codeProp: "code" };
      entry.dependents.add(d.name);
      if (filter) {
        const fm = FILTER_VS_RE.exec(filter);
        if (fm && canonicals.has(fm[2]!)) {
          entry.valueSets.add(fm[2]!);
          if (fm[1]) entry.codeProp = fm[1];
          (vsDependents.get(fm[2]!) ?? vsDependents.set(fm[2]!, new Set()).get(fm[2]!)!).add(d.name);
        }
      }
      retrieves.set(type, entry);
    }
  }

  const retrieveReqs: RetrieveRequirement[] = [...retrieves.entries()].map(([type, e]) => {
    const predicates: Predicate[] = [];
    // 1) Value-set membership — read from the retrieve's inline filter (real).
    for (const vs of e.valueSets) {
      predicates.push({
        kind: "valueset", path: e.codeProp, valueSetId: vsId(vs),
        text: `${e.codeProp} in “${vs}” value set`,
      });
    }
    // 2) Date-range window — mocked from the resource type (needs the compiled query).
    const field = TEMPORAL_FIELD[type];
    if (field) {
      predicates.push({
        kind: "date", path: field, mocked: true,
        text: `${field} during the Measurement Period`,
      });
    }
    // 3) Projected columns the read can be narrowed to (RetrieveHints.project) — mocked.
    const project = ["id", e.codeProp, ...(field ? [field] : []), "status"].filter(
      (v, i, a) => a.indexOf(v) === i,
    );
    return { id: rtId(type), type, dependents: e.dependents.size, predicates, project };
  });
  retrieveReqs.sort((a, b) => a.type.localeCompare(b.type));

  // Every declared value set gets a (mocked) expansion; dependents come from the source scan.
  const valueSetReqs: ValueSetRequirement[] = [...canonicals.entries()].map(([name, canonical]) => {
    const exp = mockExpansion(name, canonical);
    return {
      id: vsId(name), name, oid: exp.oid, canonical: canonical || null,
      codeSystems: exp.codeSystems, codes: exp.codes,
      dependents: (vsDependents.get(name)?.size) ?? 0, mocked: true,
    };
  });
  valueSetReqs.sort((a, b) => a.name.localeCompare(b.name));

  return { retrieves: retrieveReqs, valueSets: valueSetReqs };
}

// ── deterministic mock terminology expansion ─────────────────────────────────
// Real expansions come from the value-set authority via the client; these stand in so the view has
// content, and are STABLE per value-set name (a seeded generator, no randomness). The codes and code
// systems are synthetic: a stand-in must never look like real terminology.

interface CodeSystemDef { name: string; gen: (rng: () => number, i: number) => { code: string; display: string } }

const DISPLAY_STEMS = [
  "disorder", "screening", "assessment", "history of", "encounter for", "finding", "procedure",
  "measurement", "therapy", "status", "evaluation", "management",
];

const CODE_SYSTEMS: CodeSystemDef[] = [
  { name: "CqlTest", gen: (r) => ({ code: `t-${1000 + Math.floor(r() * 8999)}`, display: "" }) },
  { name: "CqlTestAlt", gen: (r) => ({ code: `a-${1000 + Math.floor(r() * 8999)}`, display: "" }) },
];

function mockExpansion(name: string, canonical: string): { oid: string; codeSystems: string[]; codes: Code[] } {
  const seed = hash(name);
  const rng = mulberry32(seed);
  // OID: reuse a declared urn:oid canonical, else synthesize a stable one under the UUID arc (2.25), which
  // no terminology authority issues value sets from.
  const oid = /^urn:oid:/.test(canonical)
    ? canonical.replace(/^urn:oid:/, "")
    : `2.25.${seed}`;

  // 1–2 code systems, deterministic; 4–24 codes across them.
  const systemCount = 1 + (seed % 2);
  const systems = pick(CODE_SYSTEMS, systemCount, rng);
  const codeCount = 4 + Math.floor(rng() * 21);
  const codes: Code[] = [];
  for (let i = 0; i < codeCount; i++) {
    const sys = systems[i % systems.length]!;
    const { code } = sys.gen(rng, i);
    const stem = DISPLAY_STEMS[Math.floor(rng() * DISPLAY_STEMS.length)]!;
    codes.push({ code, display: `${name} ${stem}`, system: sys.name });
  }
  return { oid, codeSystems: systems.map((s) => s.name), codes };
}

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

/** A tiny seeded PRNG (deterministic) so mock expansions are stable per value-set name. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick<T>(arr: T[], n: number, rng: () => number): T[] {
  const copy = [...arr];
  const out: T[] = [];
  for (let i = 0; i < n && copy.length; i++) out.push(copy.splice(Math.floor(rng() * copy.length), 1)[0]!);
  return out;
}
