// SPEC-SCORING-METHODOLOGY — how an eCQM is scored, stated ONCE by the document rather than narrated
// per measure.
//
// WHY THIS IS NOT PROSE. The performance-rate formula is published methodology: identical for every
// proportion measure, unchanged by anything clinical about either. Narrating it per
// measure spends a paragraph of a clinical document on arithmetic, and it invites getting the subtle part
// wrong — a denominator exception is removed only where the patient is NOT in the numerator, a rule the
// CQL does not contain and a reader therefore cannot check. So the document derives it, and
// `clinical-explain/SKILL.md` forbids writing about it.
//
// WHERE IT COMES FROM. The populations the ELM declares, and nothing else — `spec.populations`, which is
// already the one model both Define-lens surfaces render (`APP-SPEC-MODEL`). The formula operates on
// exactly those populations, so a measure that declares no `Denominator Exceptions` gets a formula with
// no exception term rather than a general one carrying a clause about something it does not have.
//
// THE SCORING TYPE IS DECLARED, NOT INFERRED. It comes from `Measure.scoring` on the published FHIR
// resource, falling back to `Identity/@scoring` on a Specification instance (`SPEC-XSD`) — the latter is
// a hand transcription of the former and only three libraries carry one. Inferring it from which
// populations exist was the first cut and it is the client-side approximation `coding-standards.md`
// forbids: the population names of a proportion and of a ratio overlap, so the inference cannot separate
// them, and the failure is silent — a ratio rendered with the proportion formula reads authoritative and
// is wrong.
//
// THE RULE. This module answers what scoring is in effect — proportion, ratio, continuous-variable,
// or null. Scoring is a first-class input. What has NOT changed is the refusal:
//
//   > An unsupported or undeclared scoring renders the SPINE and REFUSES the terminal. It never renders
//   > a terminal borrowed from another scoring type.
//
// WHAT IS DELIBERATELY NOT BUILT: the continuous-variable distribution terminal and the ratio's two
// spines (`APP-FLOW-GRAIN`). The CV terminal is blocked on more than a fixture — in real CV eCQMs the
// Measure Observation is a CQL FUNCTION, which `specDocument.ts` treats as transparent, the metrics
// request filters out, and the pipeline materializes no per-subject relation for. A distribution over it
// is a probe that pays for a run (`APP-PROBE`). Every function below therefore states the shape and
// returns null for what it cannot compute, rather than approximating it.

/** The four scoring types FHIR defines for `Measure.scoring`, which are also the XSD's `ScoringKind`. */
export type ScoringKind = "proportion" | "ratio" | "continuous-variable" | "cohort";

const SCORING_KINDS = new Set<string>(["proportion", "ratio", "continuous-variable", "cohort"]);

/** A declared scoring type, or null for absent AND for anything unrecognized — the two are the same
 *  answer downstream, because neither is a type this app can state a formula for. */
export function parseScoring(s: string | null | undefined): ScoringKind | null {
  return s != null && SCORING_KINDS.has(s) ? s as ScoringKind : null;
}

/** What the header prints beside the FHIR version. Null prints nothing rather than a guess. */
export function scoringLabel(s: string | null | undefined): string | null {
  const kind = parseScoring(s);
  return kind === "continuous-variable" ? "continuous variable" : kind;
}

/** The REPORTING CHAIN — which populations carry the score forward, in the order they narrow, for a
 *  declared scoring type. This is what the population list was doing implicitly for every measure: it was the
 *  proportion chain, and it was applied to ratio and continuous-variable measures too.
 *
 *  Null for an undeclared or unsupported scoring: with no chain there is no base for a figure to take a
 *  share of, and `specMetrics.baseOf` renders the figure without one rather than against a borrowed
 *  denominator. A cohort measure has no chain by definition — it reports one count and narrows nothing. */
export function populationChain(kind: ScoringKind | null): readonly string[] | null {
  switch (kind) {
    case "proportion":
      return [INITIAL, DENOMINATOR, EXCLUSIONS, EXCEPTIONS, NUMERATOR];
    case "ratio":
      // Numerator and denominator are not nested, so this is the order they READ in, not a funnel.
      return [INITIAL, DENOMINATOR, EXCLUSIONS, NUMERATOR, NUM_EXCLUSIONS];
    case "continuous-variable":
      return [INITIAL, MEASURE_POPULATION, MEASURE_POP_EXCLUSIONS];
    case "cohort":
      return [INITIAL];
    default:
      return null;
  }
}

/** Which population a removal population removes FROM, or null if it is not a removal at all.
 *
 *  `specMetrics.baseOf` hard-coded "Denominator" for every removal, which is right for a proportion and
 *  wrong twice over elsewhere: a ratio's `Numerator Exclusions` come off the NUMERATOR, and a continuous
 *  variable has no denominator to remove from. */
export function removesFrom(kind: ScoringKind | null, population: string): string | null {
  if (kind === null) return null;
  if (population === EXCLUSIONS || population === EXCEPTIONS) {
    return kind === "proportion" || kind === "ratio" ? DENOMINATOR : null;
  }
  if (population === NUM_EXCLUSIONS) return kind === "ratio" ? NUMERATOR : null;
  if (population === MEASURE_POP_EXCLUSIONS) {
    return kind === "continuous-variable" ? MEASURE_POPULATION : null;
  }
  return null;
}

/** What a measure REPORTS — the terminal, which varies with scoring while the spine does not
 *  (`APP-FLOW-GRAIN`).
 *
 *  `unsupported` is a first-class member and not an error: it is how a surface knows to draw the spine
 *  and say plainly that the terminal is not available, rather than fall through to a rate. */
export type Readout =
  | { kind: "rate"; value: number; numerator: number; base: number }
  | { kind: "ratio"; numerator: number; denominator: number }
  | { kind: "count"; population: string; subjects: number }
  | { kind: "unsupported"; scoring: ScoringKind; why: string }
  | null;

/** The reported figure for a declared scoring type over a run's counts, or null where the counts do not
 *  carry it. Null means "not computed"; `unsupported` means "computed is not a thing this shape has". */
export function readoutOf(
  kind: ScoringKind | null, counts: Record<string, number> | null | undefined,
): Readout {
  if (kind === null || !counts) return null;
  const at = (n: string): number | undefined => counts[n];

  if (kind === "proportion") {
    const num = at(NUMERATOR);
    const den = at(DENOMINATOR) ?? at(INITIAL);
    if (num === undefined || den === undefined) return null;
    // Only the removals this scoring actually removes from the denominator, and only those the measure
    // declares. The old rule subtracted anything whose name contained "Exclusions" or "Exceptions",
    // which for a ratio took Numerator Exclusions off the denominator.
    const removed = [EXCLUSIONS, EXCEPTIONS]
      .filter((p) => removesFrom(kind, p) === DENOMINATOR)
      .reduce((sum, p) => sum + (at(p) ?? 0), 0);
    const base = den - removed;
    return base > 0 ? { kind: "rate", value: num / base, numerator: num, base } : null;
  }

  if (kind === "ratio") {
    const num = at(NUMERATOR);
    const den = at(DENOMINATOR);
    if (num === undefined || den === undefined) return null;
    return { kind: "ratio", numerator: num, denominator: den };
  }

  if (kind === "cohort") {
    const subjects = at(INITIAL);
    return subjects === undefined ? null : { kind: "count", population: INITIAL, subjects };
  }

  // Continuous variable. The spine down to the Measure Population is the same story; the terminal is a
  // DISTRIBUTION of the Measure Observation, and we cannot compute one — the observation is a CQL
  // function, so the pipeline materializes no per-subject relation for it and there is nothing to
  // aggregate. Stated, not approximated with the population's own count, which would answer a different
  // question with the same confidence.
  return {
    kind: "unsupported",
    scoring: kind,
    why: "A continuous-variable measure reports a distribution of its Measure Observation. The"
      + " observation is a function rather than a materialized define, so the distribution needs a"
      + " probe run (APP-PROBE) and is not available here.",
  };
}

/** How a readout READS — the figure and the words beside it, resolved once so the document header, the
 *  Analyze lens and the operational view cannot each phrase the same result differently.
 *
 *  Returns null where there is nothing to state at all; a surface then renders no readout rather than a
 *  zero. `caption` is always a full clause, because the figure alone is ambiguous across scoring types:
 *  "56.7%" and "1.4" and "12,204" want different sentences. */
export function readoutText(r: Readout): { figure: string; caption: string } | null {
  if (r === null) return null;
  switch (r.kind) {
    case "rate":
      return {
        figure: `${(r.value * 100).toFixed(1)}%`,
        caption: `Performance rate — ${r.numerator.toLocaleString()} of ${r.base.toLocaleString()},`
          + " the denominator after removals.",
      };
    case "ratio":
      return {
        figure: r.denominator > 0 ? (r.numerator / r.denominator).toFixed(2) : "—",
        caption: `Ratio — ${r.numerator.toLocaleString()} to ${r.denominator.toLocaleString()}.`
          + " Not a rate: the numerator is not a subset of the denominator.",
      };
    case "count":
      return {
        figure: r.subjects.toLocaleString(),
        caption: `Cohort — subjects in the ${r.population}. A cohort measure reports a count, not a rate.`,
      };
    case "unsupported":
      // No figure at all, and the reason in place of one. The alternative — printing the population's
      // own count under a "performance rate" label — is the failure this whole module exists to stop.
      return { figure: "—", caption: r.why };
  }
}

/** One population's role in the formula, and what it does to the score. */
export interface ScoringRole {
  /** The define name as the measure declares it. */
  name: string;
  /** What the population IS, in one clause. */
  meaning: string;
  /** What it does to the reported rate. */
  effect: string;
  /** Which semantic role paints it — matches the funnel's own mapping. */
  tone: "base" | "keep" | "remove" | "met";
}

export interface ScoringMethodology {
  /** The rate, written with the terms this measure actually declares. */
  formula: string;
  roles: ScoringRole[];
  /** The one rule a reader cannot recover from the CQL, or null where it does not apply. */
  caveat: string | null;
}

const INITIAL = "Initial Population";
const DENOMINATOR = "Denominator";
/** Exported because the funnel labels this stage with the measure's SCORE rather than its share of the
 *  cohort (`SPEC-REPORTED-RESULT`), and a second spelling of the name in the caller is a place for the
 *  two to drift apart. */
export const NUMERATOR = "Numerator";
const EXCLUSIONS = "Denominator Exclusions";
const EXCEPTIONS = "Denominator Exceptions";
const NUM_EXCLUSIONS = "Numerator Exclusions";
const MEASURE_POPULATION = "Measure Population";
const MEASURE_POP_EXCLUSIONS = "Measure Population Exclusions";

/** Populations whose presence means this is NOT the shape below — a ratio or continuous-variable
 *  measure, scored by a different formula this module does not claim to know. */
const NOT_A_PROPORTION = new Set([
  "Numerator Exclusions", "Measure Population", "Measure Population Exclusions", "Measure Observation",
]);

/** The methodology for a measure DECLARED as a proportion, or null.
 *
 *  Null is a real answer and the caller renders nothing: a library, a measure whose instance declares no
 *  scoring, or any scoring this module does not state. */
export function scoringMethodology(
  scoring: string | null | undefined, populations: string[],
): ScoringMethodology | null {
  if (scoring !== "proportion") return null;
  const declared = new Set(populations);
  // A declared proportion that also declares a ratio's or a continuous variable's populations is a
  // CONTRADICTION, not a shape to render around: the instance and the logic disagree about what this
  // measure is, and that is a defect to see rather than paper over with a formula.
  if (populations.some((p) => NOT_A_PROPORTION.has(p))) return null;
  if (!declared.has(DENOMINATOR) || !declared.has(NUMERATOR)) return null;

  const hasExclusions = declared.has(EXCLUSIONS);
  const hasExceptions = declared.has(EXCEPTIONS);

  // The base, written with the terms this measure has. A formula naming an exception term for a measure
  // declaring none states a rule that cannot apply to it.
  const subtracted = [
    ...(hasExclusions ? ["Denominator Exclusions"] : []),
    ...(hasExceptions ? ["Denominator Exceptions"] : []),
  ];
  const base = subtracted.length ? `(Denominator − ${subtracted.join(" − ")})` : "Denominator";

  const roles: ScoringRole[] = [];
  if (declared.has(INITIAL)) {
    roles.push({
      name: INITIAL,
      meaning: "every patient the measure considers at all",
      effect: "sets the outer boundary; not the rate's base",
      tone: "base",
    });
  }
  roles.push({
    name: DENOMINATOR,
    meaning: "the patients the practice is answerable for",
    effect: "the rate's starting base",
    tone: "keep",
  });
  if (hasExclusions) {
    roles.push({
      name: EXCLUSIONS,
      meaning: "patients the measure should not judge at all",
      effect: "removed from the base; they count neither as met nor unmet",
      tone: "remove",
    });
  }
  if (hasExceptions) {
    roles.push({
      name: EXCEPTIONS,
      meaning: "patients with a valid clinical reason the target was not met",
      effect: "removed from the base ONLY where the patient is not in the Numerator",
      tone: "remove",
    });
  }
  roles.push({
    name: NUMERATOR,
    meaning: "the patients who met the target",
    effect: "the rate's top line",
    tone: "met",
  });

  return {
    formula: `Performance rate = Numerator ÷ ${base}`,
    roles,
    // The one asymmetry between exclusions and exceptions, and the only part of the formula a reader
    // cannot check against the CQL — because the CQL does not express it.
    caveat: hasExceptions
      ? "An exception only ever helps: a patient who met the target counts as met, and is not removed."
      : null,
  };
}
