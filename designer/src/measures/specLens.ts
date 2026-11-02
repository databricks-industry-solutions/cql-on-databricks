// SPEC-REF-LENS, APP-ASSISTANT-REFS — what an inline reference resolves AGAINST, built once and
// used by every surface that renders one.
//
// WHY IT LEFT `MeasureNarrative`. These resolvers were local to the document, which was right while the
// document was the only thing that rendered a reference. The assistant's answers now carry the same
// references (`APP-ASSISTANT-REFS`) and must resolve them the SAME way — a define named in a
// sentence, a define named in the CQL, and a define named in an answer are one object, so they get one
// lens, one gesture and one set of verbs. A second resolver in the drawer is how the two come to
// disagree about what a reference means, and the disagreement would be invisible: both would render a
// plausible link.
//
// IT IS A HOOK, CALLED BY EACH SURFACE, rather than a bundle threaded down as a prop. The document and
// the workspace both hold every input already, so a prop would only add a required argument to a
// component fifty tests construct — while the hook keeps ONE implementation of the resolution, which is
// the property that actually matters. Every member is memoised on its own inputs, so the second caller
// costs a map build per change of `spec`, not per render.
//
// EVERY RESOLVER MAY ANSWER NULL, and that is the contract rather than a defect: a reference to
// something this document does not render — an eliminated define, a name that has moved, a value set no
// visible logic names — degrades to the words the author wrote. Never wrong, only less.

import { useCallback, useMemo } from "react";
import type { SpecReadRef } from "../library-client.ts";
import type { Funnel, Selection } from "./model.ts";
import { isDefineSelected } from "./model.ts";
import type { MetricSet } from "./specMetrics.ts";
import { entriesByName, type SpecEntry, type SpecModel } from "./specModel.ts";
import type { SpecDocument } from "./specDocument.ts";
import type { SpecRetrieve } from "./specRetrieves.ts";
import type { InspectorContext, ProfileScope } from "./Workbench.tsx";

/** SPEC-TERMINOLOGY-REF — what the lens can say about a named declaration, all of it DERIVED.
 *
 *  `kind` is resolved here rather than declared in the instance: which of the two a name is, is a fact
 *  the ELM states, and a narrator who typed it could type it wrong with nothing to catch them. */
export interface TermLens {
  kind: "valueset" | "code";
  name: string;
  library: string;
  /** The OID for a value set, the code for a direct reference code — the identifier the Appendix row
   *  states. `null` where the library was published without one. */
  id: string | null;
  system: string | null;
  display: string | null;
  /** The DOM id of the Appendix ROW that declares it, so following the reference lands on the row
   *  rather than at the top of a table of forty (`SPEC-REF-LENS`: a reference's verb lands on the
   *  thing, never on the section that lists it). */
  anchor: string;
}

/** The Appendix row anchor for one declaration. Library-qualified, because the names are not unique. */
export function terminologyAnchor(library: string, name: string): string {
  return `mw-term-${`${library}--${name}`.replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

/** Everything a prose renderer needs to resolve the references inside a sentence.
 *
 *  ONE BUNDLE rather than five props, for the reason the bundle has always existed: this object is
 *  threaded to every prose renderer there is, and a fifth parallel prop beside it is how one path comes
 *  to be forgotten — silently, because a forgotten resolver renders the sentence as prose, which looks
 *  exactly like a sentence that had no reference in it. */
export interface ReadLens {
  resolve: (ref: SpecReadRef) => SpecRetrieve | null;
  /** Subjects behind the read, or null when this evaluation did not materialize it (`SPEC-METRIC`:
   *  absent, never zero). Normally null: read materialization is retired for readers
   *  (`SPEC-READ-REF`), and `CQL_MATERIALIZE_RETRIEVES` is the authoring switch that still fills it. */
  count: (key: string) => number | null;
  onSelect: (sel: Selection) => void;
  /** SPEC-REF-LENS — the pinned lens offers `Jump to section` on a bound phrase exactly as it does on a
   *  CQL token, so the bundle carries the navigating half too. */
  onNavigate: (sel: Selection) => void;
  selectedKey: string | null;
  /** SPEC-DEFINE-REF — the entry a DEFINE named in the prose resolves to, or null where this document
   *  does not render one (an eliminated define, or a name that has moved). */
  defineEntry: (name: string) => SpecEntry | null;
  defineSelected: (name: string) => boolean;
  /** SPEC-REF-LENS — what a reference lens's BODY reads: the inspector's own context, so the lens
   *  renders the pane's three tabs rather than a summary of them. */
  inspector: InspectorContext;
  /** SPEC-PARAMETER-REF — the execution parameter values a sentence may cite, keyed by CQL parameter
   *  NAME. Run INPUTS rather than results, which is why they resolve with no data source attached. */
  params: Record<string, string>;
  /** SPEC-TERMINOLOGY-REF — the value set or direct reference code a phrase names, resolved out of the
   *  ELM-derived Appendix by `(library, name)`. */
  terminology: (library: string, name: string) => TermLens | null;
}

/** The whole resolution surface, including the pieces the DOCUMENT needs beyond `ReadLens` itself —
 *  the CQL linker's retrieve lookup and the filtered Appendix it renders as a section. */
export interface SpecLens {
  byName: Map<string, SpecEntry>;
  defineEntry: (name: string) => SpecEntry | null;
  /** A CQL retrieve TOKEN (`ConditionEncounterDiagnosis`) to the read it names. */
  resolveRetrieve: (token: string) => SpecRetrieve | null;
  /** The Appendix, filtered to what VISIBLE logic names — the set the Terminology section renders. */
  terminology: { valuesets: SpecDocument["appendix"]["valuesets"];
                 codes: SpecDocument["appendix"]["codes"] };
  inspector: InspectorContext;
  reads: ReadLens;
}

/** What a caller must hold to build the lens. Every one of these is already owned by both the document
 *  and the workspace, which is why this is a hook rather than a prop. */
export interface SpecLensInputs {
  definitionId: string;
  /** NULLABLE, because the workspace holds the lens before the compile that produces a model — and a
   *  hook cannot be called conditionally. An absent model resolves nothing, which is the SAME answer
   *  the bundle already gives for a name it does not know: the reference degrades to its words. That
   *  is why this is not a second code path. */
  spec: SpecModel | null;
  doc: SpecDocument | null;
  metrics: MetricSet | null;
  live: boolean;
  funnel: Funnel | null;
  profileScope: ProfileScope | null;
  retrieves: SpecRetrieve[];
  /** SPEC-DERIVED-APPENDIX — the terminology VISIBLE logic names, `library::name`. Elimination governs
   *  terminology as it governs defines, so resolving against the unfiltered appendix would let a phrase
   *  point at a row that is not on the page. */
  namedTerminology: { valueSets: Set<string>; codes: Set<string> };
  readCounts: Map<string, number> | null;
  parameters: Record<string, string>;
  selected: Selection | null;
  onSelect: (sel: Selection) => void;
  onNavigate: (sel: Selection) => void;
}

export function useSpecLens(input: SpecLensInputs): SpecLens {
  const { definitionId, spec, doc, metrics, live, funnel, profileScope, retrieves, namedTerminology,
    readCounts, parameters, selected, onSelect, onNavigate } = input;

  // SPEC-REF-LENS — every entry by name, so a reference resolves to the entry the inspector would scope
  // to. Built with the model's OWN name resolution rather than a walk of its own: the lens renders the
  // inspector's body, so a name resolved differently here would put two different defines' figures
  // under one name (`APP-SPEC-MODEL`). Memoised as a map because a page of CQL resolves hundreds of
  // these per render.
  const byName = useMemo(() => (spec ? entriesByName(spec) : new Map<string, SpecEntry>()), [spec]);
  const defineEntry = useCallback(
    (name: string): SpecEntry | null => byName.get(name) ?? null, [byName]);

  // The join is by PROFILE and it is exact. QICore names the type `ConditionEncounterDiagnosis` and
  // profiles it `…/qicore-condition-encounter-diagnosis`; the ELM `Retrieve` carries that `templateId`,
  // so the kebab-cased token matches a value the compiler emitted rather than a convention we hoped
  // held. A token matching nothing stays plain syntax.
  const retrieveByToken = useMemo(() => {
    const out = new Map<string, SpecRetrieve>();
    for (const r of retrieves) {
      for (const profile of r.profiles) if (!out.has(profile)) out.set(profile, r);
      if (!out.has(r.type)) out.set(r.type, r);
    }
    return out;
  }, [retrieves]);

  const resolveRetrieve = useCallback((token: string): SpecRetrieve | null => {
    const kebab = token.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();
    for (const prefix of ["qicore-", "us-core-", ""]) {
      const hit = retrieveByToken.get(prefix + kebab);
      if (hit) return hit;
    }
    return retrieveByToken.get(token) ?? null;
  }, [retrieveByToken]);

  const appendix = doc?.appendix;
  const terminology = useMemo(() => ({
    valuesets: (appendix?.valuesets ?? []).filter(
      (v) => namedTerminology.valueSets.has(`${v.library}::${v.name}`)),
    codes: (appendix?.codes ?? []).filter((c) => namedTerminology.codes.has(`${c.library}::${c.name}`)),
  }), [appendix, namedTerminology]);

  // SPEC-TERMINOLOGY-REF — every declaration a phrase may name, keyed `library::name`.
  //
  // BUILT FROM THE FILTERED SET, not from `doc.appendix` directly, and that is the whole subtlety: a
  // phrase resolves exactly when there is something on the page to follow it to, and degrades to prose
  // when there is not (`APP-SPEC-MODEL`: one determination, not two).
  const termsByKey = useMemo(() => {
    const out = new Map<string, TermLens>();
    for (const v of terminology.valuesets) {
      out.set(`${v.library}::${v.name}`, {
        kind: "valueset", name: v.name, library: v.library, id: v.oid, system: null, display: null,
        anchor: terminologyAnchor(v.library, v.name),
      });
    }
    for (const c of terminology.codes) {
      const key = `${c.library}::${c.name}`;
      if (out.has(key)) continue;
      out.set(key, {
        kind: "code", name: c.name, library: c.library, id: c.code, system: c.system,
        display: c.display, anchor: terminologyAnchor(c.library, c.name),
      });
    }
    return out;
  }, [terminology]);

  // SPEC-REF-LENS — what the lens's body reads. It is the SAME bundle the inspector pane builds
  // (`InspectorContext`), which is what makes a pinned scope and a popped one one rendering of one
  // payload rather than two components that agree by hand.
  const inspector = useMemo<InspectorContext>(() => ({
    definitionId, spec, doc, metrics, live, funnel, scope: profileScope, onNavigate,
  }), [definitionId, spec, doc, metrics, live, funnel, profileScope, onNavigate]);

  // SPEC-READ-REF — PROFILE FIRST, TYPE ONLY AS A FALLBACK. "Problem list" and "encounter diagnosis"
  // are two profiles of ONE resource type, so a type-first lookup would give both phrases the same read
  // and the same number — the exact failure the binding exists to prevent.
  const reads = useMemo<ReadLens>(() => ({
    resolve: (ref) => {
      if (ref.profile) {
        const hit = retrieves.find((r) => r.profiles.includes(ref.profile!));
        if (hit) return hit;
      }
      return ref.type ? retrieveByToken.get(ref.type) ?? null : null;
    },
    count: (key) => readCounts?.get(key) ?? null,
    onSelect,
    onNavigate,
    selectedKey: selected?.kind === "retrieve" ? selected.key : null,
    // SPEC-DEFINE-REF — the SAME resolver the CQL tokens use, so a define named in a sentence and the
    // same define named in the code cannot show different figures or land in different places.
    defineEntry,
    defineSelected: (name: string) => isDefineSelected(selected, name),
    inspector,
    params: parameters,
    // SPEC-TERMINOLOGY-REF — resolved out of the SAME appendix the Terminology section renders, so a
    // phrase in a sentence and the row stating its OID cannot disagree (`APP-SPEC-MODEL`).
    terminology: (library, name) => termsByKey.get(`${library}::${name}`) ?? null,
  }), [retrieves, retrieveByToken, readCounts, onSelect, onNavigate, selected, defineEntry, parameters,
       termsByKey, inspector]);

  return { byName, defineEntry, resolveRetrieve, terminology, inspector, reads };
}
