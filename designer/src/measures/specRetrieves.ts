// SPEC-DATA-REQUIREMENTS / SPEC-RETRIEVE-LENS — what the logic READS.
//
// "What is a read" is derived server-side once, by `cql_databricks_designer/explain/reference_set.py`,
// and this module CONSUMES that answer. The reference set rides back on `/api/translate` with the ELM
// it was derived from, so the model this document renders and the reads resolved into it describe one
// compilation.
//
// WHAT STAYS HERE, and why it is not a second derivation:
//
//   REACHABILITY. `model` has had elimination applied; the reference set covers the whole library. That
//   filter is a DOCUMENT decision (which defines this reader is looking at), not a fact about the CQL.
//
//   AGGREGATION. The reference set lists reads PER DEFINE, because that is what a narration cites. This
//   document needs the inverse — one row per distinct read, with its call sites — and that shape is the
//   Data requirements section's, not the ELM's.
//
//   `mustSupport`. It comes from the published `Library.dataRequirement`, a FHIR resource rather than the
//   ELM, and `reference_set.py` is deliberately pure-ELM. It is an enrichment joined on here.
//
// WHY NOT `Library.dataRequirement` ALONE. The published resource carries the answer a measure's published
// specification renders, and it is authoritative for `mustSupport` — the element paths the logic touches, which
// nothing else knows. But it is PER LIBRARY and says nothing about which define asked for a row, so
// it cannot answer the two questions this document needs answered:
//
//   REACHABILITY. Union a published measure's pinned closure and you get hundreds of rows, most of
//   them from a helper library whose functions can touch anything, where the measure itself reads a
//   few dozen. The difference is everything nothing reaches. Elimination already governs which DEFINES appear
//   (APP-SPEC-MODEL); a retrieve inside an unreachable define is unreachable by exactly the same
//   argument, and deriving retrieves per define is what lets one rule cover both.
//
//   TRACEABILITY. Every ELM `Retrieve` carries a `locator` — `"43:7-43:65"`, the clause's own line
//   and column. That is the anchor a reader follows from a data-requirement row back to the CQL that
//   asked for it, and it is exact rather than a name-matching heuristic.
//
// So: the ELM decides WHAT IS READ and WHERE FROM, and `Library.dataRequirement` enriches it with
// `mustSupport`. Neither is a fallback for the other — they answer different halves, and the join is
// on the triple they share.

import type { SpecModel } from "./specModel.ts";
import type { DataRequirement } from "../library-client.ts";

/** One distinct read: a resource type, the profile it is read against, and the terminology that
 *  filters it. The GRAIN IS THE TRIPLE, not the resource type — a measure may read Condition multiple
 *  times, across different profiles and value sets, and those are distinct bindings. */
export interface SpecRetrieve {
  /** Dedupe key: `type|filter`. Two defines issuing the identical read are ONE row with two call
   *  sites, because it is one data requirement.
   *
   *  PROFILE IS NOT IN THE KEY. Including it would split Condition/Exclusion A into two rows — one per
   *  QICore profile — and that split is only as trustworthy as the `templateId` behind it. Until the
   *  profile is confirmed correct it is not shown, and a key that splits on a value the reader cannot
   *  see produces rows that look identical and are not. Held on `profiles` so nothing is lost. */
  key: string;
  /** FHIR resource type, unqualified: `Condition`, `Encounter`, `Observation`. */
  type: string;
  /** The QICore / US Core profiles this read resolves against, last segment only, deduped.
   *  DERIVED BUT NOT DISPLAYED pending confirmation that `templateId` means what it appears to. Kept
   *  because the linker still resolves a retrieve token through it, and because dropping derived data
   *  from the model to hide it in the UI is how a fact gets re-derived differently later. */
  profiles: string[];
  /** The value set the read is filtered by, BY NAME — the name Terminology declares, so the two join
   *  without either restating the other's OID (SPEC-DERIVED-APPENDIX). */
  valueSet: string | null;
  /** A direct reference code used as the filter, where the read is filtered by a code rather than a
   *  value set. Rare, and not the same thing as a code the logic merely compares against. */
  code: string | null;
  /** The element the filter applies to (`code`, `type`). Unfiltered reads have none. */
  codeProperty: string | null;
  /** `Library.dataRequirement.mustSupport` — the element paths the logic actually touches. The one
   *  fact only the translator knows, and the one a data binding has to satisfy. Empty when the
   *  library's resource carries no `dataRequirement` (some ingested copies do not). */
  mustSupport: string[];
  /** Every reachable define that issues this read, with the anchor back to its own CQL. */
  usedBy: RetrieveSite[];
}

/** One call site: which define asked, and where in that define's body the clause sits. */
export interface RetrieveSite {
  define: string;
  library: string;
  /** SPEC-NARRATIVE-DEVICES — a line range in the DEFINE'S OWN body, the same anchor a narrated claim
   *  uses, so the citation machinery highlights a retrieve exactly as it highlights a claim. */
  span: string | null;
}

/** SPEC-EXPLAIN-INPUTS — one library's reference set, as `/api/translate` returns it.
 *
 *  Hand-mirrored from `cql_databricks_designer/explain/reference_set.py`, which is the ONE derivation of what a
 *  read is. Only what this document consumes is typed here; the payload carries more (bodies, metric
 *  keys, reference sites) for the narration generator. */
export interface RefSetRead {
  resourceType: string;
  profile?: string;
  valueSet?: string;
  /** A DIRECT REFERENCE CODE used as the filter — not a value set, and never reported as one. */
  code?: string;
  codeProperty?: string;
  /** BODY-RELATIVE, 1-based, within the define — already the `sourceSpan` convention, so nothing here
   *  recomputes it from a locator. The arithmetic is done once, not repeated here. */
  line?: number;
}

/** A terminology name with the library that DECLARES it. Library-qualified because two libraries in one
 *  closure may bind one name to different OIDs (`SPEC-TERMINOLOGY-REF`). */
export interface RefSetTerm { name: string; library?: string; line?: number }

export interface RefSetDefine {
  name: string;
  library: string;
  reads?: RefSetRead[];
  valueSets?: RefSetTerm[];
  codes?: RefSetTerm[];
}

export interface ReferenceSet {
  library: string;
  version: string;
  defines: RefSetDefine[];
}

/** The visible defines per library, from the MODEL.
 *
 *  MEMBERSHIP COMES FROM THE MODEL, NEVER FROM HERE — the caller hands in one `filterSpecModel` has
 *  already resolved against the reader's "Show eliminated" choice, so re-testing `eliminated` would
 *  overrule it: turning the toggle ON left this stripping the very entries it was asked to reveal. */
function visibleDefines(model: SpecModel): Map<string, Set<string>> {
  const visible = new Map<string, Set<string>>();
  for (const lib of model.libraries) {
    visible.set(lib.name, new Set(lib.entries.map((e) => e.name)));
  }
  for (const pop of model.populations) {
    const set = visible.get(pop.library) ?? new Set<string>();
    set.add(pop.name);
    visible.set(pop.library, set);
  }
  return visible;
}

/** SPEC-DATA-REQUIREMENTS — every read the VISIBLE logic performs.
 *
 *  IT DOES NOT DERIVE THEM. The reads come from the server's reference set, which walked the ELM once
 *  (`SPEC-EXPLAIN-INPUTS`); this INVERTS them from per-define into per-read-with-call-sites, which is
 *  the shape the Data requirements section asks for and is not a fact about the CQL.
 *
 *  `model` has already had elimination applied (APP-SPEC-MODEL), so a define absent from it is a
 *  define nothing reaches, and its reads are absent too. That is the whole reachability rule: not a
 *  second opinion about what matters, the SAME opinion the rest of the document runs on. The reference
 *  set covers the whole library, so this filter is what narrows it to the document.
 *
 *  `dataRequirementsByLibrary` is enrichment only. A library whose ingested resource lacks
 *  `dataRequirement` still contributes its reads; the `mustSupport` column is simply empty for them,
 *  which is honest — the alternative is a row that silently claims the logic touches nothing. */
export function buildRetrieves(
  model: SpecModel,
  referenceSets: ReferenceSet[],
  dataRequirementsByLibrary: Map<string, DataRequirement[]>,
): SpecRetrieve[] {
  const visible = visibleDefines(model);

  // `mustSupport` by the pair a ROW is identified by — library, type, filter. Not by profile: the
  // published resource splits one read across its profiles, this document does not, so the paths are
  // UNIONED. Taking the first profile's list instead would silently under-report what a binding has to
  // supply, which is the one thing this column exists to state.
  const mustSupport = new Map<string, string[]>();
  for (const [library, requirements] of dataRequirementsByLibrary) {
    for (const req of requirements) {
      for (const filter of req.codeFilter?.length ? req.codeFilter : [undefined]) {
        const vs = filter?.valueSet ? filter.valueSet.split("/").pop() ?? null : null;
        const key = `${library}|${req.type}|${vs ?? ""}`;
        const merged = new Set([...(mustSupport.get(key) ?? []), ...(req.mustSupport ?? [])]);
        mustSupport.set(key, [...merged]);
      }
    }
  }

  const out = new Map<string, SpecRetrieve>();
  for (const refSet of referenceSets) {
    const reachable = visible.get(refSet.library);
    if (!reachable) continue;
    for (const define of refSet.defines) {
      if (!reachable.has(define.name)) continue;
      for (const read of define.reads ?? []) {
        const valueSet = read.valueSet ?? null;
        const code = read.code ?? null;
        const key = `${read.resourceType}|${valueSet ?? code ?? ""}`;
        const span = read.line != null ? String(read.line) : null;
        const site = { define: define.name, library: refSet.library, span };
        const existing = out.get(key);
        if (existing) {
          if (read.profile && !existing.profiles.includes(read.profile)) {
            existing.profiles.push(read.profile);
          }
          if (!existing.usedBy.some(
            (s) => s.define === site.define && s.library === site.library && s.span === site.span)) {
            existing.usedBy.push(site);
          }
          continue;
        }
        out.set(key, {
          key, type: read.resourceType, profiles: read.profile ? [read.profile] : [], valueSet, code,
          codeProperty: read.codeProperty ?? null,
          mustSupport: mustSupport.get(`${refSet.library}|${read.resourceType}|${valueSet ?? ""}`) ?? [],
          usedBy: [site],
        });
      }
    }
  }
  // Resource type, then value set — so the reads of one resource read together and the table's shape
  // matches the question ("what does this measure take from Condition?").
  return [...out.values()].sort((a, b) =>
    a.type.localeCompare(b.type) || (a.valueSet ?? "").localeCompare(b.valueSet ?? ""));
}

/** SPEC-DERIVED-APPENDIX — the terminology the VISIBLE logic actually names.
 *
 *  ALSO CONSUMED, NOT DERIVED. The reference set attributes every value set and code to the
 *  library that DECLARES it, resolving `Exclusions."Excluded Care Encounter"` through the ELM's own include table
 *  — so this file does not repeat that lookup with an alias map of its own, a second copy of the same lookup.
 *
 *  ELIMINATION EXTENDS TO TERMINOLOGY, for the same reason it extends to reads: a value set only an
 *  unreachable define mentions is one this measure does not use, and listing it invites a reader to go
 *  expand codes for a rule that never fires.
 *
 *  Keyed `library::name`, because two libraries may bind one name to different OIDs and collapsing them
 *  would hide the disagreement. */
export function referencedTerminology(
  model: SpecModel,
  referenceSets: ReferenceSet[],
): { valueSets: Set<string>; codes: Set<string> } {
  const valueSets = new Set<string>();
  const codes = new Set<string>();
  const visible = visibleDefines(model);

  for (const refSet of referenceSets) {
    const reachable = visible.get(refSet.library);
    if (!reachable) continue;
    for (const define of refSet.defines) {
      if (!reachable.has(define.name)) continue;
      for (const v of define.valueSets ?? []) valueSets.add(`${v.library ?? refSet.library}::${v.name}`);
      for (const c of define.codes ?? []) codes.add(`${c.library ?? refSet.library}::${c.name}`);
    }
  }
  return { valueSets, codes };
}

/** The reads a single define issues, from the retrieve list.
 *
 *  A retrieve already records every define that issues it (`usedBy`), so this is a FILTER over a list
 *  the model has rather than a second walk of the graph — the same reason `SPEC-NAV-COMPOSITION` reads
 *  the document instead of re-forming it. Matched on library AND name, because two libraries routinely
 *  declare the same define name (`SPEC-ANCHOR`). */
export function readsForDefine(
  retrieves: SpecRetrieve[], define: string, library: string,
): SpecRetrieve[] {
  return retrieves.filter((r) => r.usedBy.some((u) => u.define === define && u.library === library));
}

/** SPEC-DERIVED-APPENDIX — every value set and code ONE DEFINE REFERENCES, from the reference set.
 *
 *  NOT THE RETRIEVE FILTERS. Deriving from `SpecRetrieve.valueSet` / `.code` would be a correctness bug, not a
 *  refinement: terminology only appears on a retrieve when the
 *  translator PUSHED THE FILTER DOWN into it. A define that retrieves broadly and narrows in a `where`
 *  references terminology the retrieve never mentions, so a retrieve-derived list silently omits it.
 *
 *  Not hypothetical — a measure may reference codes in where clauses that don't appear in retrieve filters,
 *  which is a common pattern of retrieving broadly and separating by code afterwards. The retrieve-based
 *  derivation would silently omit those codes. This derivation correctly includes all referenced terminology.
 *
 *  `reference_set.py` already walks the ELM for every terminology reference in a define's body, which is
 *  the same one derivation `SPEC-EXPLAIN-INPUTS` made server-side to stop the browser walking ELM too.
 *  This reads that answer; it does not re-derive one (`APP-SPEC-MODEL`).
 *
 *  Library-qualified on the way in — two libraries in one closure may bind one name to different OIDs
 *  (`SPEC-TERMINOLOGY-REF`) — and returned as bare names, because the tab renders names and joins to the
 *  appendix by them. */
/*  CURRENTLY UNCONSUMED, and kept deliberately. Codes are read off the define's RESULTS rather
 *  than its bindings. This function's consumer is the value-set coverage report, which needs exactly
 *  this list. Kept rather than deleted because the push-down finding below is expensive to rediscover
 *  and cheap to hold; delete it with the coverage report if that is ever abandoned. */
export function terminologyForDefine(
  referenceSets: ReferenceSet[], define: string, library: string,
): { valueSets: string[]; codes: string[] } {
  const hit = referenceSets
    .flatMap((rs) => rs.defines)
    .find((d) => d.name === define && d.library === library);
  return {
    valueSets: [...new Set((hit?.valueSets ?? []).map((v) => v.name))],
    codes: [...new Set((hit?.codes ?? []).map((c) => c.name))],
  };
}

/** The terminology a set of READS binds — value sets by name, and the direct reference codes used where
 *  a read filters on a code rather than on membership.
 *
 *  Scoped to what a RETRIEVE states, so it answers "what did the translator push down", NOT "what does
 *  this define reference" — use `terminologyForDefine` for the latter. Kept for the read scope, where the
 *  question genuinely is about one retrieve.
 *
 *  STRUCTURAL, so it is real with no run. This is what lets the Terminology tab lead with what the logic
 *  says and treat hit counts as enrichment — a value set that is bound but that no run has exercised is
 *  a FINDING ("declared, never matched"), and deriving the list from hits instead would render it as
 *  absence. Deduped and order-preserving: a define issuing three reads against one value set binds it
 *  once, and the reading order is the document's. */
export function boundTerminology(
  reads: SpecRetrieve[],
): { valueSets: string[]; codes: string[] } {
  return {
    valueSets: [...new Set(reads.map((r) => r.valueSet).filter((v): v is string => !!v))],
    codes: [...new Set(reads.map((r) => r.code).filter((c): c is string => !!c))],
  };
}
