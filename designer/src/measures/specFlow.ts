// APP-COHORT-ALGEBRA / APP-DECISIVE-COUNT — the client half of the measure flow's rail: what to
// ask the warehouse, and how to read the answer back.
//
// THIS MODULE IS THE "RE-READ, NEVER RE-FORM" RULE, in code. `specDocument.ts` already decides what a
// population is made of — its depth-1 Concepts, and their Rules, at their lowest common dominator
// (`SPEC-CONCEPTS` / `SPEC-RULES` / `SPEC-PLACEMENT`). The flow states figures ABOUT that decomposition
// and must never form a second one. So the gates are built here, from `SpecDocument`, and posted; the
// server has no ELM graph and adds only counts (`insights/flow.py`). A server-side re-derivation would
// be the second opinion `APP-SPEC-MODEL` exists to prevent, and this is the same argument that makes
// `/metrics` a POST.
//
// WHAT A GATE IS. One population, the cohort entering it, and the sibling criteria a subject has to get
// past. Nothing here decides whether a population IS a conjunction — the ELM operator that would settle
// that is not derived anywhere (`APP-FLOW-SPINE`, deferred), so the polarity comes from the
// population's ROLE, which the scoring type already tells us: an exclusion population is the
// disjunction, everything else on the chain is the conjunction. That is a narrower claim than reading
// the operator and it is one we can actually support.
//
// A GATE IS ONLY ASKED FOR WHERE IT CAN BE ANSWERED. A population with no Concepts is one unfactored
// boolean, and there is nothing to attribute a departure to — asking would get an omission back, so it
// is not asked. That is not a defect being hidden: it is a true report about an under-factored measure,
// and the pane says so where the rail would have been.

import { POPULATIONS } from "./model.ts";
import { parseScoring, populationChain, removesFrom, type ScoringKind } from "./specScoring.ts";
import type { SpecDocument, SpecScope } from "./specDocument.ts";

/** `all-of` — an entry conjunction, where a subject fails a criterion by NOT meeting it.
 *  `any-of` — the exclusion disjunction, where they fail it by meeting it, and "decisive" reads as
 *  *excluded by exactly one reason*. The one thing that differs between the two shapes. */
export type GatePolarity = "all-of" | "any-of";

export interface FlowGateRequest {
  stage: string;
  stageLibrary: string;
  base?: string;
  baseLibrary?: string;
  polarity: GatePolarity;
  criteria: { name: string; library: string }[];
}

/** The wire payload — mirrors `insights/flow.py`'s dataclasses. */
export interface CriterionDropDto {
  library: string;
  define: string;
  /** Subjects for whom flipping THIS criterion alone would have changed the outcome. The figure the
   *  rail leads with, and the only one safe to add up. */
  decisive: number;
  /** Everyone who failed it at all. These overlap across criteria and overshoot the cohort, so they
   *  are shown beside the decisive count and never instead of it. */
  anyCause: number;
}

export interface FlowGateDto {
  stage: string;
  library: string;
  base?: string;
  polarity: GatePolarity;
  entered: number;
  reached: number;
  passed: number;
  /** Failed two or more of the sibling criteria — the remainder that completes the partition. */
  multiple: number;
  criteria: CriterionDropDto[];
}

export interface CohortFlowDto {
  library: string;
  version: string;
  provenance: "run";
  generated: string;
  gates: FlowGateDto[];
  /** Stages that could not be decomposed. Named, so the pane can say which rather than go quiet. */
  omitted?: string[];
  caveat?: string;
}

/** The gates to ask about, read off the document's own decomposition.
 *
 *  Returns an empty list where there is nothing answerable — no declared scoring (so no chain, and no
 *  way to know which population removes from which), or no population with Concepts. */
export function buildFlowRequest(
  doc: SpecDocument | null, scoring: string | null | undefined,
): FlowGateRequest[] {
  const kind = parseScoring(scoring);
  const chain = populationChain(kind);
  if (!doc || !kind || !chain) return [];

  const scopeByName = new Map<string, SpecScope>();
  for (const scope of doc.populations) {
    if (scope.population) scopeByName.set(scope.name, scope);
  }

  const gates: FlowGateRequest[] = [];
  for (const scope of doc.populations) {
    const entry = scope.population;
    if (!entry || scope.concepts.length === 0) continue;
    const base = baseOf(scope.name, kind, chain, scopeByName);
    gates.push({
      stage: entry.name,
      stageLibrary: entry.library,
      ...(base ? { base: base.name, baseLibrary: base.library } : {}),
      polarity: removesFrom(kind, scope.name) !== null ? "any-of" : "all-of",
      criteria: scope.concepts.map((c) => ({ name: c.define, library: c.library })),
    });
  }
  return gates;
}

/** The population a gate's subjects ENTER FROM.
 *
 *  A removal population is entered from what it removes from — `Denominator Exclusions` narrows the
 *  people already in the `Denominator`, not the whole cohort. Everything else enters from the nearest
 *  KEPT stage above it on the chain, skipping removals for the same reason `specMetrics.baseOf` does:
 *  taking `Denominator Exclusions` as the Numerator's base reads as though the exclusions were the
 *  people who passed.
 *
 *  Undefined for the first stage on the chain, and the server reads that as the evaluated cohort —
 *  there is no population above the Initial Population to have departed from. */
function baseOf(
  name: string, kind: ScoringKind, chain: readonly string[], scopes: Map<string, SpecScope>,
): { name: string; library: string } | undefined {
  const removes = removesFrom(kind, name);
  const target = removes ?? nearestKeptAbove(name, kind, chain);
  if (!target) return undefined;
  const entry = scopes.get(target)?.population;
  return entry ? { name: entry.name, library: entry.library } : undefined;
}

function nearestKeptAbove(
  name: string, kind: ScoringKind, chain: readonly string[],
): string | undefined {
  const at = chain.indexOf(name);
  for (let i = at - 1; i >= 0; i--) {
    const above = chain[i]!;
    if (removesFrom(kind, above) === null) return above;
  }
  return undefined;
}

/** One gate's figures, indexed by stage name — what the rail renders. */
export type FlowByStage = Map<string, FlowGateDto>;

export function buildFlow(dto: CohortFlowDto | null): FlowByStage {
  const out: FlowByStage = new Map();
  for (const gate of dto?.gates ?? []) out.set(gate.stage, gate);
  return out;
}

/** The criteria of one gate, ordered by the figure the rail leads with.
 *
 *  DECISIVE DESCENDING, because the question the rail answers is "what is deciding most of this" —
 *  `APP-DECISIVE-COUNT`'s "one criterion decides 71% of your failures" is a ranking, and a rail in
 *  declaration order buries it. Ties fall back to any-cause so the order is stable across runs. */
export function rankedCriteria(gate: FlowGateDto): CriterionDropDto[] {
  return [...gate.criteria].sort((a, b) => b.decisive - a.decisive || b.anyCause - a.anyCause);
}

/** Whether a gate's parts add up to its whole, which is the property that makes them safe to sum.
 *
 *  Exported so a surface can CHECK rather than assume: the partition is guaranteed by the SQL's
 *  construction, and a renderer that sums the parts into a total should still verify it rather than
 *  print a figure that silently stopped adding up. */
export function partitions(gate: FlowGateDto): boolean {
  const parts = gate.passed + gate.multiple
    + gate.criteria.reduce((sum, c) => sum + c.decisive, 0);
  return parts === gate.entered;
}

/** SPEC-METRIC — the populations a flow could ever cover, for a caller sizing its own request. */
export const FLOW_POPULATIONS: readonly string[] = POPULATIONS;
