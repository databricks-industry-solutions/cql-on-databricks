// APP-SPEC-MODEL / APP-DEP-COUNTS — the dependency graph, derived from the COMPILER'S ELM.
//
// This replaces the source scan (`logicGraph.ts`) for the Define lens, and closes the goal that module
// records: the DAG comes from the compiler, not from a regex over CQL text. The ELM is a typed tree, so
// every edge here is exact — including CROSS-LIBRARY edges, which a per-library text scan structurally
// cannot see (`Exclusions."Has Excluded Care"` resolves to a define the scanning library never declares).
//
// What the tree gives us, by node `type`:
//   ExpressionRef / FunctionRef   → an edge to another define, with `libraryName` set when it crosses
//                                   a library boundary (the alias, resolved back to the library here)
//   Retrieve                      → a data read; `dataType` is the qualified FHIR type, `codes` its
//                                   value-set filter
//   ValueSetRef                   → a value-set reference
//   ParameterRef / OperandRef / … → not edges; ignored
//
// Nodes are keyed the same way `logicGraph.ts` keys them (`def:`/`fn:`/`vs:`/`rt:`) so the nav's
// closure, dependents and layer helpers are shared rather than duplicated.

import type { ElmLibraryInfo } from "./elmDefines.ts";
import type { LogicGraph, LogicNode, LogicNodeType } from "./logicGraph.ts";
import { computeLayers } from "./logicGraph.ts";

/** One library's compiled form: its parsed catalog plus the raw ELM the expression trees live in. */
export interface ElmLibrarySource {
  info: ElmLibraryInfo;
  /** The parsed ELM document (`JSON.parse` of the translate result). */
  elm: unknown;
}

const defId = (name: string) => `def:${name}`;
const fnId = (name: string) => `fn:${name}`;
const vsId = (name: string) => `vs:${name}`;
const rtId = (type: string) => `rt:${type}`;

/** `{http://hl7.org/fhir}Observation` → `Observation`. */
function localName(raw: string): string {
  const close = raw.lastIndexOf("}");
  return close >= 0 ? raw.slice(close + 1) : raw;
}

/** Walk an ELM expression tree, invoking `visit` on every node object. */
function walkExpression(node: unknown, visit: (n: Record<string, unknown>) => void): void {
  if (Array.isArray(node)) {
    for (const child of node) walkExpression(child, visit);
    return;
  }
  if (typeof node !== "object" || node === null) return;
  const obj = node as Record<string, unknown>;
  if (typeof obj["type"] === "string") visit(obj);
  for (const key of Object.keys(obj)) {
    // `signature` and `resultTypeSpecifier` describe TYPES, not referenced expressions — a
    // NamedTypeSpecifier is not a dependency, and descending into them invents edges.
    if (key === "signature" || key === "resultTypeSpecifier" || key === "operandTypeSpecifier") continue;
    walkExpression(obj[key], visit);
  }
}

/** The statement definitions of an ELM document. */
function statementsOf(elm: unknown): Record<string, unknown>[] {
  const lib = (elm as Record<string, unknown> | null)?.["library"] as Record<string, unknown> | undefined;
  const stmts = (lib?.["statements"] as Record<string, unknown[]> | undefined)?.["def"] ?? [];
  return stmts as Record<string, unknown>[];
}

/** Build the dependency graph for a set of compiled libraries (APP-SPEC-MODEL).
 *
 *  Defines are keyed by NAME across the whole set, matching how the compiler's `describe` reports them
 *  and how selection works across the lenses. A name declared in two libraries is therefore one node —
 *  the same collapsing the spec model applies. */
export function buildElmGraph(libraries: ElmLibrarySource[], populationNames: ReadonlySet<string>): LogicGraph {
  const nodes = new Map<string, LogicNode>();
  const ensure = (node: LogicNode) => {
    const existing = nodes.get(node.id);
    if (existing) return existing;
    nodes.set(node.id, node);
    return node;
  };

  // Kind lookup by name, so a reference resolves to `fn:` or `def:` correctly wherever it was declared.
  const kindByName = new Map<string, "define" | "function">();
  for (const lib of libraries) for (const d of lib.info.defines) kindByName.set(d.name, d.kind);
  const idOf = (name: string) => (kindByName.get(name) === "function" ? fnId(name) : defId(name));

  const typeOf = (name: string): LogicNodeType =>
    populationNames.has(name) ? "population" : kindByName.get(name) === "function" ? "function" : "define";

  // 1) A node per define, from the compiler's catalog.
  for (const lib of libraries) {
    for (const d of lib.info.defines) {
      ensure({ id: idOf(d.name), label: d.name, type: typeOf(d.name), deps: [], dependents: [], cql: null, hint: "", layer: 0 });
    }
    // 2) Declared value sets.
    for (const vs of lib.info.valueSets) {
      ensure({ id: vsId(vs), label: vs, type: "valueset", deps: [], dependents: [], cql: null,
        hint: "Value set — the codes this logic looks for.", layer: 0 });
    }
  }

  // 3) Edges, read off each statement's expression tree.
  for (const lib of libraries) {
    for (const stmt of statementsOf(lib.elm)) {
      const name = stmt["name"] as string | undefined;
      if (!name) continue;
      const self = nodes.get(idOf(name));
      if (!self) continue;
      const addDep = (depId: string) => { if (depId !== self.id && !self.deps.includes(depId)) self.deps.push(depId); };

      walkExpression(stmt["expression"], (n) => {
        switch (n["type"]) {
          case "ExpressionRef":
          case "FunctionRef": {
            const ref = n["name"] as string | undefined;
            // A ref to a define this library set does not contain (a system function, or an include we
            // were not given) is left out rather than invented as a node.
            if (ref && kindByName.has(ref)) addDep(idOf(ref));
            break;
          }
          case "Retrieve": {
            const dataType = n["dataType"] as string | undefined;
            if (dataType) {
              const t = localName(dataType);
              ensure({ id: rtId(t), label: t, type: "retrieve", deps: [], dependents: [], cql: null,
                hint: `Retrieve — every ${t} resource for the patient.`, layer: 0 });
              addDep(rtId(t));
            }
            break;
          }
          case "ValueSetRef": {
            const ref = n["name"] as string | undefined;
            if (ref) {
              ensure({ id: vsId(ref), label: ref, type: "valueset", deps: [], dependents: [], cql: null,
                hint: "Value set — the codes this logic looks for.", layer: 0 });
              addDep(vsId(ref));
            }
            break;
          }
        }
      });
    }
  }

  // 4) Invert edges + compute layers.
  const list = [...nodes.values()];
  for (const n of list) for (const depId of n.deps) nodes.get(depId)?.dependents.push(n.id);
  computeLayers(nodes);

  const byId: Record<string, LogicNode> = {};
  for (const n of list) byId[n.id] = n;
  return { nodes: list, byId };
}
