// SPEC-NAV-COMPOSITION — the measure's dependency structure, rooted at the POPULATIONS.
//
// The left nav's second tab. The Outline tab answers "what is in this document"; this one answers
// "what does each population actually depend on, and how deep does it go" — the question a reviewer
// asks first and the flat catalogue cannot answer at all.
//
// THIS IS THE RETIRED `SPEC-INDEX-TREE`, ROOTED DIFFERENTLY AND STRIPPED OF ITS LABELS.
// Git history records why the original was removed, and both of its
// failures are addressed by construction rather than argued away:
//
//  1. IT WAS EMPTY WHERE IT WAS MOST NEEDED. As a per-define lens it had nothing to show for
//     `Qualifying Reading`, which references no other define — so a tab that could not
//     take focus sat beside the defines that most needed explaining. Rooted at the POPULATIONS it is
//     never empty: a population is by definition the thing everything else hangs off, and a define
//     with no dependencies is a LEAF of this view rather than an empty instance of it.
//  2. ITS EDGE LABELS OVER-CLAIMED. Each edge carried a connective (AND/OR/NOT) and an operation
//     (EXISTS, WITHOUT, FILTERS, EITHER OF) read off the ELM ancestors of the reference site, and on
//     real published content a top-level `Union` labelled every dependency `EITHER OF` — so exclusions
//     rendered as alternatives. **There are no edge labels here.** This view states REACHABILITY and
//     nothing else, which is a fact the graph has exactly. What an edge MEANS is what the narration
//     and the CQL are for, one click away on the row.
//
// THE TREE CARRIES DEFINES AND READS. A READ is the leaf: the tree bottoms out at the data the logic
// actually goes and gets, which is the question a reviewer is walking down the tree to reach, and it is
// the only row in the tree that a data source can be checked against. Value sets are carried BY the read
// that filters by them — a value set on its own says which codes exist, a read says which records this
// criterion will accept, and the second is
// the fact with data behind it (`SPEC-DATA-REQUIREMENTS`). Functions are OUT: transparent, walked
// through, never drawn. A helper is machinery, not a clinical dependency, and `FHIRHelpers.ToString`
// between a criterion and the read that decides it is a row of plumbing in the middle of a clinical
// argument. What a helper REACHES still surfaces as the caller's own child — INCLUDING ITS READS,
// which is what keeps a retrieve issued inside a helper from vanishing with the helper's row.
//
// EVERYTHING ELSE COMES FROM THE MODEL (APP-SPEC-MODEL, APP-CQL-AUTHORITATIVE). A define row is
// a `SpecEntry` — the same object the Outline lists and the page renders, so a row and a section are
// one object — and the edges are `SpecModel.graph`, the compiler's ELM expression trees.
// A value set is NOT a `SpecEntry` and is not made to look like one: it is declared, not defined, and
// has no section of its own, so it is a distinct kind of subject rather than a fabricated entry.

import type { LogicGraph } from "../authoring/logicGraph.ts";
import type { SpecEntry, SpecModel } from "./specModel.ts";
import type { SpecRetrieve } from "./specRetrieves.ts";

/** What a row IS. Two kinds, because the model genuinely holds two kinds of thing: a DECLARATION it
 *  carries (a population or a define, each with a section of its own), and a READ (`SpecRetrieve`) —
 *  derived from the same ELM but at a different grain, with no section of its own. Synthesising a
 *  `SpecEntry` for a read would put a membership decision here, which `APP-SPEC-MODEL` reserves for
 *  `specModel.ts`. Functions are neither — they are walked through, never drawn (`directChildren`).
 *
 *  THE GRAIN IS THE RETRIEVE, NOT THE RESOURCE TYPE. The graph's own retrieve nodes are keyed by type
 *  alone, so every define that touches a Condition would draw one identical `Condition` row; the
 *  `SpecRetrieve` model already keys on type + filter (`SPEC-DATA-REQUIREMENTS`), which is the grain
 *  that distinguishes "Condition filtered by Exclusion A" from "Condition filtered by Exclusion B" — two
 *  different bindings, and the difference a reviewer is reading the branch to find. */
export type CompositionSubject =
  | { kind: "entry"; entry: SpecEntry }
  | { kind: "retrieve"; retrieve: SpecRetrieve };

/** One row of the composition tree. */
export interface CompositionNode {
  subject: CompositionSubject;
  /** What this row directly reaches — defines and value sets; helpers are walked through. */
  children: CompositionNode[];
  /** Already expanded elsewhere under this same population, so it renders as a leaf reference rather
   *  than repeating its subtree. */
  repeat: boolean;
  /** Unique within the tree — the same subject appears at several sites, so its name is not a key. */
  path: string;
}

/** One population and everything it reaches. */
export interface CompositionRoot {
  population: SpecEntry;
  children: CompositionNode[];
  /** Distinct subjects reached, at any depth — what the population's subtree is worth reading for. */
  reached: number;
}

const nodeIdOf = (e: SpecEntry) => (e.kind === "function" ? `fn:${e.name}` : `def:${e.name}`);

/** The name a subject is identified by — for repeat detection and for the text filter. A read has no
 *  name of its own, so it is identified by what distinguishes it: its resource and its filter. */
export const subjectName = (s: CompositionSubject): string =>
  s.kind === "entry" ? s.entry.name : retrieveLabel(s.retrieve);

/** `Condition · Exclusion A`, or `Condition · every record` for an unfiltered read. */
export const retrieveLabel = (r: SpecRetrieve): string =>
  `${r.type} · ${r.valueSet ?? r.code ?? "every record"}`;

/** SPEC-NAV-COMPOSITION — build the population-rooted composition of a specification.
 *
 *  The model arrives already ordered and already filtered for eliminated defines (`sortSpecModel`,
 *  `filterSpecModel`), and both are honoured rather than re-decided here: siblings render in the
 *  catalogue's order, so picking a Sort moves this tree with the Outline and the page
 *  (SPEC-SHARED-ORDER), and an entry the model does not carry is not reachable through this tree
 *  either. */
export function buildComposition(
  model: SpecModel,
  /** SPEC-DATA-REQUIREMENTS — the reads each define ISSUES, inverted from `SpecRetrieve.usedBy` so the
   *  tree and the Data requirements section state one set of reads at one grain. Absent = no reads
   *  drawn, which is what a library with no compiled data requirements should show. */
  retrievesByDefine: Map<string, SpecRetrieve[]> = new Map(),
): CompositionRoot[] {
  // Entries by name, plus the catalogue's own position, so siblings can be ordered by it.
  const byName = new Map<string, SpecEntry>();
  const rank = new Map<string, number>();
  let n = 0;
  for (const p of model.populations) { byName.set(p.name, p); rank.set(p.name, n++); }
  for (const lib of model.libraries) {
    for (const e of lib.entries) {
      // First declaration wins: two libraries may declare one name, and the graph's edges are by name.
      if (!byName.has(e.name)) { byName.set(e.name, e); rank.set(e.name, n++); }
    }
  }

  return model.populations.map((population) => {
    // Expanded ONCE PER POPULATION. Collapsing across populations would make a subtree's presence
    // depend on which population was rendered first, and the whole point of the breakdown is that each
    // population states its own dependencies in full.
    const seen = new Set<string>([population.name]);
    const reached = new Set<string>();

    const expand = (id: string, path: string): CompositionNode[] =>
      directChildren(model.graph, id, byName, rank, retrievesByDefine)
        .map((subject) => {
          const name = subjectName(subject);
          const repeat = seen.has(name);
          if (!repeat) seen.add(name);
          reached.add(name);
          const childPath = `${path}/${subject.kind}:${name}`;
          return {
            subject,
            // A read is where the tree bottoms out — there is nothing under it to expand.
            children: repeat || subject.kind === "retrieve"
              ? [] : expand(nodeIdOf(subject.entry), childPath),
            repeat,
            path: childPath,
          };
        });

    return {
      population,
      children: expand(nodeIdOf(population), population.key),
      reached: reached.size,
    };
  });
}

/** What a node directly reaches: the DEFINES the model declares, and the READS it issues.
 *
 *  FUNCTIONS ARE TRANSPARENT — walked THROUGH, never drawn. A call is not a clinical dependency: what
 *  `FHIRHelpers.ToString` or `encounterFor` contributes to a measure is the defines and the data it
 *  reaches, and drawing the call as well puts a row of plumbing between a criterion and the read that
 *  actually decides it. So a helper vanishes and WHAT IT REACHES BECOMES THE CALLER'S — including its
 *  READS, which is the part that is easy to lose: `buildRetrieves` attributes a retrieve to the
 *  statement that issues it, so a read inside `encounterFor` belongs to `encounterFor`, and cutting the
 *  helper without carrying its reads up would delete data requirements from the tree that the Data
 *  requirements section still lists. A clinical define invoked as `Exclusions."Has Excluded Care"`
 *  surfaces by the same walk (an opaque cut would silently drop the exclusions it carries — the failure
 *  `SPEC-RULES` records for the same rule).
 *
 *  Ordering is the catalogue's for defines, then reads by label. A read is a different KIND of thing
 *  from a define, so interleaving the two by graph order would make a node's children read as one list
 *  of unlike things; grouped, the eye takes "what it depends on" and then "what it goes and gets".
 *
 *  A name the graph knows and the model does not — an eliminated define, or a declaration in a library
 *  that failed to compile — is WALKED THROUGH for the same reason, so hiding one define cannot silently
 *  truncate everything below it. */
function directChildren(
  graph: LogicGraph, id: string, byName: Map<string, SpecEntry>, rank: Map<string, number>,
  retrievesByDefine: Map<string, SpecRetrieve[]>,
): CompositionSubject[] {
  const entries: SpecEntry[] = [];
  const reads = new Map<string, SpecRetrieve>();
  const emitted = new Set<string>();
  const walked = new Set<string>([id]);

  /** The reads a node issues in its own right, claimed by whoever is standing in for it. */
  const claimReads = (label: string): void => {
    for (const r of retrievesByDefine.get(label) ?? []) if (!reads.has(r.key)) reads.set(r.key, r);
  };
  claimReads(graph.byId[id]?.label ?? "");

  const visit = (nodeId: string): void => {
    const node = graph.byId[nodeId];
    if (!node) return;
    for (const depId of node.deps) {
      if (walked.has(depId)) continue;
      walked.add(depId);
      const dep = graph.byId[depId];
      // The graph's own retrieve and value-set leaves are not rows: reads come from `SpecRetrieve` at a
      // finer grain, and a value set is stated ON the read it filters.
      if (!dep || dep.type === "retrieve" || dep.type === "valueset") continue;
      const entry = byName.get(dep.label);
      if (!entry || entry.kind === "function") {
        // Transparent: this node is not drawn, so its reads become ours and its own deps are walked.
        claimReads(dep.label);
        visit(depId);
        continue;
      }
      if (emitted.has(entry.name)) continue;
      emitted.add(entry.name);
      entries.push(entry);
    }
  };
  visit(id);

  entries.sort((a, b) => (rank.get(a.name) ?? 0) - (rank.get(b.name) ?? 0));
  const retrieves = [...reads.values()].sort((a, b) => retrieveLabel(a).localeCompare(retrieveLabel(b)));
  return [
    ...entries.map((entry): CompositionSubject => ({ kind: "entry", entry })),
    ...retrieves.map((retrieve): CompositionSubject => ({ kind: "retrieve", retrieve })),
  ];
}

/** Narrow a composition to the subjects a predicate keeps, LIFTING the survivors.
 *
 *  A node the predicate drops is replaced by its children, so a filtered tree still reaches everything
 *  that matches. The consequence is deliberate and has to be stated: **the depth shown is then not the
 *  real depth** — a survivor re-hangs under its nearest surviving ancestor, not its true parent. That
 *  is the cost of applying a flat-list filter to a tree, and it is why the tree marks itself as
 *  filtered rather than letting the reader take the shape at face value.
 *
 *  A dropped node's READS go with it rather than being lifted: a read means nothing detached from the
 *  logic that issues it, and re-hanging one under a grandparent would assert a data requirement that
 *  define does not have.
 *
 *  POPULATION ROOTS ARE NOT SUBJECT TO IT. They are the view's AXIS, not its content — the same way
 *  the Outline's "Definitions" and "Terminology" headings are not filtered away by the Type facet. A
 *  facet that could delete the roots would empty the whole view rather than narrow it. */
export function pruneComposition(
  roots: CompositionRoot[], keep: (subject: CompositionSubject) => boolean,
): CompositionRoot[] {
  const prune = (nodes: CompositionNode[]): CompositionNode[] =>
    nodes.flatMap((node) => {
      if (keep(node.subject)) return [{ ...node, children: prune(node.children) }];
      return node.subject.kind === "retrieve" ? [] : prune(node.children);
    });
  return roots.map((root) => ({ ...root, children: prune(root.children) }));
}
