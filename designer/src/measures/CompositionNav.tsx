// SPEC-NAV-COMPOSITION — the left pane's Composition tab: the measure as a tree, one branch per
// population, bottoming out at the reads. Shows every population's decomposition side by side, so a
// reader can see where a define is actually used and which populations share it. Uses the same
// `navRows.tsx` kit as the Outline, so a define looks and selects identically in both arrangements
// (`APP-SPEC-MODEL` — one row style, one model).
//
// ONE COLLAPSIBLE LEVEL, and only the population carries it. A population heads a branch a reader
// finishes with and wants out of the way; the defines under it are read down, not folded. A twisty on
// every row turns a tree you can put down into a tree of controls.

import { useState, type ReactNode } from "react";
import type { NavRowKit } from "./navRows.tsx";
import type { CompositionNode, CompositionRoot } from "./specComposition.ts";

export function CompositionNav({ roots, narrowed, rows }: {
  /** One tree per population, already built and pruned by `specComposition.ts`. */
  roots: CompositionRoot[];
  /** True when a filter is narrowing the tree — see `pruneComposition`: survivors re-hang under their
   *  nearest surviving ancestor, so the depth on screen is no longer the real depth and the pane has to
   *  say so rather than let the shape be read at face value. */
  narrowed: boolean;
  rows: NavRowKit;
}) {
  return (
    <>
      {narrowed && (
        <div className="mw-nav-note">
          Filtered — rows hang under the nearest match, not their real parent.
        </div>
      )}
      {roots.length === 0 && <div className="mw-nav-foot">No population criteria to trace from.</div>}
      {roots.map((root) => (
        <CompositionPopulation key={root.population.key} root={root} rows={rows} />
      ))}
    </>
  );
}

/** A POPULATION and its subtree — the one collapsible level. */
function CompositionPopulation({ root, rows }: { root: CompositionRoot; rows: NavRowKit }) {
  const [open, setOpen] = useState(true);
  return (
    <>
      {rows.renderCollapsible(root.population, open, () => setOpen((v) => !v), root.population.key)}
      {open && root.children.length > 0 && (
        <div className="mw-nav-nested">
          {root.children.map((child) => (
            <CompositionRow key={child.path} node={child} rows={rows} />
          ))}
        </div>
      )}
    </>
  );
}

/** One row of the tree, and its own children nested below it. */
function CompositionRow({ node, rows }: { node: CompositionNode; rows: NavRowKit }): ReactNode {
  return (
    <>
      {node.subject.kind === "entry"
        ? rows.renderEntry(node.subject.entry, { key: node.path, repeat: node.repeat })
        : rows.renderRetrieve(node.subject.retrieve, { key: node.path, repeat: node.repeat })}
      {node.children.length > 0 && (
        <div className="mw-nav-nested">
          {node.children.map((child) => (
            <CompositionRow key={child.path} node={child} rows={rows} />
          ))}
        </div>
      )}
    </>
  );
}
