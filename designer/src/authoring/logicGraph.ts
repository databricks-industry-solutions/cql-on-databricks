// APP-LOGIC-VIEW, APP-DEP-COUNTS — derive a dependency graph from CQL source.
//
// The Narrative view is a filterable list of every criterion (population / define / function / value set
// / retrieve) with, per node, its dependency counts in BOTH directions — "uses N" (downstream, what it
// depends on) and "used by N" (upstream dependents, APP-DEP-COUNTS) — and, on selection, the
// transitive closure of what it uses (APP-LOGIC-VIEW).
//
// This is a LIGHT SOURCE SCAN (not a parser) — it resolves references by matching quoted/bare names
// against the library's own declared nodes, mirroring logic.ts. Unknown references (system functions,
// included-library defines) are left out rather than guessed.
//
// GOAL / TODO (once CQL on Databricks is wired to the CQL client): the dependency DAG AND each criterion's RESULT
// TYPE should be retrieved from the client (`cql-databricks` / the engine's `describe`) rather than
// derived here — the client sees the real, typed ELM, so the edges are exact and result types become
// available (unlocking the deferred "result type" filter, distinct from the source-derived "data type"
// filter). This likely requires EXTENDING the client/`describe` surface: the current `LibraryGraph`
// (SVC-PROTOCOL) carries `dependencies` (define→[deps]) but NOT per-define result types or the
// value-set/retrieve leaf nodes this view shows. When that lands, this module becomes a fallback used
// only offline / before a translate, and the Narrative consumes the client's graph as the source of
// truth. See designer/README.md and APP-DEP-COUNTS.

import { parseDefinitions } from "./logic.ts";
import type { DescribeResult } from "../wire.ts";
import { POPULATIONS } from "../measures/model.ts";

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

// eCQM population definitions (normalized), tagged as `population` so they can head the list + be
// filtered. DERIVED from the one list rather than restated: this was a third hand-maintained copy and
// it had already drifted — it carried the continuous-variable names while the model's list did not, so a
// `Measure Population` was a population to the graph and not to the model that renders it.
const POPULATION_NAMES = new Set<string>(POPULATIONS.map((n) => n.toLowerCase()));

const VALUESET_RE = /\bvalueset\s+"([^"]+)"\s*:/g;
const RETRIEVE_RE = /\[\s*([A-Z][A-Za-z0-9]*)\b/g;
const QUOTED_RE = /"([^"]+)"/g;
const CALL_RE = /\b([A-Za-z_]\w*)\s*\(/g;

const defId = (name: string) => `def:${name}`;
const fnId = (name: string) => `fn:${name}`;
const vsId = (name: string) => `vs:${name}`;
const rtId = (type: string) => `rt:${type}`;

function isPopulation(name: string): boolean {
  return POPULATION_NAMES.has(name.trim().toLowerCase());
}

/** Build the dependency graph from CQL source (APP-LOGIC-VIEW). Nodes in a stable order (defines in
 *  source order, then value sets, then retrieves); the view sorts them per the chosen order. */
export function buildLogicGraph(cql: string): LogicGraph {
  const defs = parseDefinitions(cql);

  // Declared name -> node id, so references resolve to the right kind (a "..." is a value set if declared
  // as one, else a define/function of that name).
  const valuesetNames = new Set<string>();
  for (let m = VALUESET_RE.exec(cql); m !== null; m = VALUESET_RE.exec(cql)) {
    if (m[1]) valuesetNames.add(m[1]);
  }
  const defineByName = new Map<string, (typeof defs)[number]>();
  for (const d of defs) defineByName.set(d.name, d);
  const functionNames = new Set(defs.filter((d) => d.kind === "function").map((d) => d.name));

  const nodes = new Map<string, LogicNode>();
  const ensure = (node: LogicNode) => { if (!nodes.has(node.id)) nodes.set(node.id, node); return nodes.get(node.id)!; };

  // 1) Define / function / population nodes.
  for (const d of defs) {
    const type: LogicNodeType = d.kind === "function" ? "function" : isPopulation(d.name) ? "population" : "define";
    const id = d.kind === "function" ? fnId(d.name) : defId(d.name);
    ensure({ id, label: d.name, type, deps: [], dependents: [], cql: d.cql, hint: "", layer: 0 });
  }

  // 2) Value-set nodes (declared).
  for (const name of valuesetNames) {
    ensure({ id: vsId(name), label: name, type: "valueset", deps: [], dependents: [], cql: null,
      hint: "Value set — the codes this logic looks for.", layer: 0 });
  }

  // 3) Edges + retrieve nodes, scanning each define/function body.
  for (const d of defs) {
    const selfId = d.kind === "function" ? fnId(d.name) : defId(d.name);
    const self = nodes.get(selfId)!;
    // Strip the `define … :` header so the node's own name isn't read as a self-reference.
    const body = d.cql.replace(/^\s*define\s+(?:public\s+|private\s+)?(?:fluent\s+)?(?:function\s+)?(?:"[^"]+"|[A-Za-z_]\w*)[^:]*:/, "");
    const addDep = (depId: string) => { if (depId !== selfId && !self.deps.includes(depId)) self.deps.push(depId); };

    // Retrieves: [Type] / [Type: "VS"] → a retrieve leaf (+ the value set is caught by the quoted scan).
    for (let m = RETRIEVE_RE.exec(body); m !== null; m = RETRIEVE_RE.exec(body)) {
      const t = m[1]!;
      ensure({ id: rtId(t), label: t, type: "retrieve", deps: [], dependents: [], cql: null,
        hint: `Retrieve — every ${t} resource for the patient.`, layer: 0 });
      addDep(rtId(t));
    }
    // Quoted references: a declared value set, else a sibling define.
    for (let m = QUOTED_RE.exec(body); m !== null; m = QUOTED_RE.exec(body)) {
      const name = m[1]!;
      if (valuesetNames.has(name)) addDep(vsId(name));
      else if (defineByName.has(name)) {
        const dep = defineByName.get(name)!;
        addDep(dep.kind === "function" ? fnId(name) : defId(name));
      }
    }
    // Function calls by bare name (only ones declared in this library).
    for (let m = CALL_RE.exec(body); m !== null; m = CALL_RE.exec(body)) {
      const name = m[1]!;
      if (functionNames.has(name)) addDep(fnId(name));
    }
  }

  // 4) Dependents (invert deps) + layers.
  const list = [...nodes.values()];
  for (const n of list) for (const depId of n.deps) nodes.get(depId)?.dependents.push(n.id);
  computeLayers(nodes);

  const byId: Record<string, LogicNode> = {};
  for (const n of list) byId[n.id] = n;
  return { nodes: list, byId };
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

// ── DESIGNER-SDP-NAV — hybrid graph merge ───────────────────────────────────────────────────────
//
// The engine's /describe (`DescribeResult`) provides exact cross-library define-to-define edges but
// lacks node classification (population vs define vs function) and leaf nodes (value sets, retrieves).
// The local CQL source scan (`buildLogicGraph`) provides node types and leaves but only intra-library
// edges. The hybrid merge takes the server's edges and the local scan's classification to produce a
// graph that has both exact cross-library edges AND the full node vocabulary the clinical UI needs.

/** Build a hybrid graph from a server-provided DescribeResult and the local CQL source(s).
 *
 *  Strategy:
 *  - Server defines + server dependency edges form the backbone (exact, cross-library).
 *  - Each server define is classified by matching against the local scan's node set: if the local
 *    scan has a node with the same label, adopt its type; otherwise default to "define".
 *  - Value-set and retrieve leaf nodes come exclusively from the local scan (the server doesn't
 *    carry them). They are attached to the defines that reference them using the local scan's edges.
 *  - Layers, dependents, and the byId index are recomputed over the merged result.
 */
export function mergeWithDescribe(
  describe: DescribeResult,
  localSources: { source: string; libraryName?: string }[],
): LogicGraph {
  // 1. Build local graphs for node classification and leaf extraction.
  const localGraphs = localSources.map((s) => buildLogicGraph(s.source));
  const localById = new Map<string, LogicNode>();
  for (const g of localGraphs) for (const n of g.nodes) localById.set(n.id, n);
  // Also build a label→node map for matching server defines against local classification.
  const localByLabel = new Map<string, LogicNode>();
  for (const g of localGraphs) for (const n of g.nodes) {
    if (!localByLabel.has(n.label)) localByLabel.set(n.label, n);
  }

  const nodes = new Map<string, LogicNode>();
  const ensure = (node: LogicNode) => { if (!nodes.has(node.id)) nodes.set(node.id, node); return nodes.get(node.id)!; };

  // 2. Create nodes for every server define, classified by the local scan.
  for (const name of describe.defines) {
    const localMatch = localByLabel.get(name);
    const type: LogicNodeType = localMatch?.type ?? (isPopulation(name) ? "population" : "define");
    const id = type === "function" ? fnId(name) : defId(name);
    ensure({
      id, label: name, type,
      deps: [], dependents: [],
      cql: localMatch?.cql ?? null,
      hint: "", layer: 0,
    });
  }

  // 3. Wire server dependency edges (the exact cross-library edges).
  for (const [name, deps] of Object.entries(describe.dependencies)) {
    const localMatch = localByLabel.get(name);
    const selfType: LogicNodeType = localMatch?.type ?? (isPopulation(name) ? "population" : "define");
    const selfId = selfType === "function" ? fnId(name) : defId(name);
    const self = nodes.get(selfId);
    if (!self) continue;
    for (const depName of deps) {
      const depLocal = localByLabel.get(depName);
      const depType: LogicNodeType = depLocal?.type ?? (isPopulation(depName) ? "population" : "define");
      const dId = depType === "function" ? fnId(depName) : defId(depName);
      // Ensure the dep node exists (it should from step 2, but guard).
      ensure({
        id: dId, label: depName, type: depType,
        deps: [], dependents: [],
        cql: depLocal?.cql ?? null,
        hint: "", layer: 0,
      });
      if (!self.deps.includes(dId)) self.deps.push(dId);
    }
  }

  // 4. Attach leaf nodes (value sets, retrieves) from the local scans.
  for (const g of localGraphs) {
    for (const n of g.nodes) {
      if (n.type === "valueset" || n.type === "retrieve") {
        ensure({ ...n, deps: [], dependents: [], layer: 0 });
      }
    }
    // Re-attach leaf edges: for each local define that references a leaf, add the edge
    // to the corresponding server-or-local node.
    for (const n of g.nodes) {
      if (n.type === "valueset" || n.type === "retrieve") continue;
      const serverId = n.type === "function" ? fnId(n.label) : defId(n.label);
      const serverNode = nodes.get(serverId);
      if (!serverNode) continue;
      for (const depId of n.deps) {
        const depNode = nodes.get(depId);
        if (depNode && (depNode.type === "valueset" || depNode.type === "retrieve")) {
          if (!serverNode.deps.includes(depId)) serverNode.deps.push(depId);
        }
      }
    }
  }

  // 5. Recompute dependents + layers over the merged graph.
  const list = [...nodes.values()];
  for (const n of list) n.dependents = []; // clear before recomputing
  for (const n of list) for (const depId of n.deps) nodes.get(depId)?.dependents.push(n.id);
  computeLayers(nodes);

  const byId: Record<string, LogicNode> = {};
  for (const n of list) byId[n.id] = n;
  return { nodes: list, byId };
}
