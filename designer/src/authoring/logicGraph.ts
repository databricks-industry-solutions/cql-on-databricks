// APP-LOGIC-VIEW, APP-DEP-COUNTS — the dependency graph's shape, and the walks the views share.
//
// The Narrative view is a filterable list of every criterion (population / define / function / value set
// / retrieve) with, per node, its dependency counts in BOTH directions — "uses N" (downstream, what it
// depends on) and "used by N" (upstream dependents, APP-DEP-COUNTS) — and, on selection, the
// transitive closure of what it uses (APP-LOGIC-VIEW).
//
// The graph itself is built from the compiler's ELM in `elmGraph.ts`. This module owns the node types, the
// layer computation both builders share, and the closure and dependents walks.

export type LogicNodeType = "population" | "define" | "function" | "valueset" | "retrieve";

export interface LogicNode {
  /** Stable id, namespaced by kind so a value set and a define never collide: "def:Numerator". */
  id: string;
  label: string;
  type: LogicNodeType;
  /** Ids this node directly uses (downstream). */
  deps: string[];
  /** Ids that use this node (upstream dependents) — the "used by" direction (APP-DEP-COUNTS). */
  dependents: string[];
  /** The node's CQL slice (defines/functions); null for value-set / retrieve leaves. */
  cql: string | null;
  /** A deterministic, non-AI description for leaves; defines/functions read their name + CQL (the
   *  AI-generated plain-language summary is APP-NL, S3). */
  hint: string;
  /** Longest-path layer over `deps` (0 = leaf), for topological sort. */
  layer: number;
}

export interface LogicGraph {
  nodes: LogicNode[];
  byId: Record<string, LogicNode>;
}

/** Longest-path layer over `deps` (memoized, cycle-guarded) — a leaf is 0, a node is 1 + max(dep layers).
 *  Shared with the ELM-derived graph (`elmGraph.ts`), which keys nodes the same way. */
export function computeLayers(nodes: Map<string, LogicNode>): void {
  const memo = new Map<string, number>();
  const visiting = new Set<string>();
  const layer = (id: string): number => {
    const cached = memo.get(id);
    if (cached !== undefined) return cached;
    if (visiting.has(id)) return 0; // cycle guard
    const n = nodes.get(id);
    if (!n || n.deps.length === 0) { memo.set(id, 0); return 0; }
    visiting.add(id);
    let m = 0;
    for (const d of n.deps) if (nodes.has(d)) m = Math.max(m, layer(d) + 1);
    visiting.delete(id);
    memo.set(id, m);
    return m;
  };
  for (const n of nodes.values()) n.layer = layer(n.id);
}

/** The transitive set of ids `id` depends on (excludes `id` itself) — what it USES (APP-LOGIC-VIEW). */
export function closureOf(graph: LogicGraph, id: string): Set<string> {
  const seen = new Set<string>();
  const walk = (x: string) => {
    for (const d of graph.byId[x]?.deps ?? []) {
      if (!seen.has(d) && graph.byId[d]) { seen.add(d); walk(d); }
    }
  };
  walk(id);
  return seen;
}

/** The transitive set of ids that depend on `id` — what USES it (upstream dependents, APP-DEP-COUNTS).
 *  The mirror of closureOf over the `dependents` edges. */
export function dependentsOf(graph: LogicGraph, id: string): Set<string> {
  const seen = new Set<string>();
  const walk = (x: string) => {
    for (const d of graph.byId[x]?.dependents ?? []) {
      if (!seen.has(d) && graph.byId[d]) { seen.add(d); walk(d); }
    }
  };
  walk(id);
  return seen;
}
