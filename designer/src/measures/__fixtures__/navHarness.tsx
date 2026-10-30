// Test harness for the left pane (SPEC-NAV-COMPOSITION). Not a test file — the vitest include glob is
// `src/**/*.test.tsx`, so this is imported, never collected.
//
// It mounts the REAL `NavPaneBody`, not a stand-in, because the things worth pinning about the pane are
// the ones that only exist when its parts are wired together: that both arrangements render through one
// row kit, that the filter narrows both and survives a tab switch, and that the tab strip is on the
// first paint. A harness that hand-built those relationships would pass while the pane was broken.
//
// The ELM is real, from the same Compiler Service fixture `specModel.test.tsx` uses — a fixture that
// agrees with a guess about the ELM's shape would pass while the product failed.

import { useState } from "react";
import { render } from "@testing-library/react";
import { parseElmDefines } from "../../authoring/elmDefines.ts";
import type { AppendixSections } from "../DefineNav.tsx";
import { NavPaneBody } from "../NavPane.tsx";
import { selectionToken, type NavTab, type PaneSize, type Selection } from "../model.ts";
import { buildSpecDocument } from "../specDocument.ts";
import { buildMetricSet, type MetricSet } from "../specMetrics.ts";
import { buildSpecModel, sortSpecModel, type SpecLibraryInput, type SpecModel, type SpecSortKey } from "../specModel.ts";
import { buildRetrieves } from "../specRetrieves.ts";
import fixtures from "./spec-model-elm.json";
import refsets from "./spec-model-refset.json";
import type { ReferenceSet } from "../specRetrieves.ts";

export function libraryInput(key: "demo" | "helpers"): SpecLibraryInput {
  const f = fixtures[key];
  const info = parseElmDefines(f.elm);
  if (!info) throw new Error(`fixture ${key} did not parse`);
  return { info, elm: JSON.parse(f.elm), source: f.source };
}

export interface HarnessOptions {
  metrics?: MetricSet | null;
  /** The value the CONTROL shows. */
  sort?: SpecSortKey;
  /** The order the model actually arrives in. Passing it APART from `sort` is how a test proves the nav
   *  does not sort for itself (SPEC-SHARED-ORDER). */
  modelSort?: SpecSortKey;
  tab?: NavTab;
  includes?: ("helpers")[];
  showEliminated?: boolean;
  selected?: Selection | null;
  /** SPEC-DERIVED-APPENDIX — render the reference sections, whose counts the workspace derives from the
   *  document the pane is already holding. Passing `{}` takes the derivation below as-is; a field
   *  overrides one count, which is how a test states a parameter the fixture's ELM does not declare
   *  without hand-shaping the rest of the object. Absent = the pane is handed nothing, the state every
   *  other test in this file exercises. */
  appendix?: Partial<AppendixSections>;
}

/** What a test can inspect after rendering: the container, plus what the pane reported back. */
export interface Harness {
  container: HTMLElement;
  /** Every `onSelect` the pane made, as URL tokens. */
  picked: string[];
  /** Every document anchor the pane asked to land on. */
  anchored: string[];
}

export function renderNavPane(opts: HarnessOptions = {}): Harness {
  const { metrics = null, sort = "declared", modelSort = sort, tab = "outline",
    includes = ["helpers"], showEliminated = false, selected = null, appendix } = opts;

  const root = libraryInput("demo");
  const libs = includes.map(libraryInput);
  const spec: SpecModel = buildSpecModel({ root, includes: libs });
  const elmByLibrary = new Map<string, unknown>([[root.info.name, root.elm]]);
  for (const l of libs) elmByLibrary.set(l.info.name, l.elm);

  const picked: string[] = [];
  const anchored: string[] = [];

  // SPEC-SHARED-ORDER — the pane is handed an ALREADY-ORDERED model, exactly as the workspace hands it
  // one, so the test exercises the real contract: the pane lists what it is given, in that order.
  const visible = sortSpecModel(spec, modelSort);
  const doc = buildSpecDocument(spec, elmByLibrary);
  // SPEC-EXPLAIN-INPUTS — reads come from the server-derived reference set now, generated from the
  // very ELM beside it, so the harness exercises the real consumption path rather than a stand-in.
  const retrieves = buildRetrieves(
    spec, Object.values(refsets) as unknown as ReferenceSet[], new Map());

  // SPEC-DERIVED-APPENDIX — derived from the SAME document and reads the pane is handed, the way the
  // workspace derives it, so the counts a row carries are the ones the page's own headings would carry.
  const appendixSections: AppendixSections | undefined = appendix && {
    dataRequirements: [...retrieves.reduce((m, r) => m.set(r.type, (m.get(r.type) ?? 0) + 1),
      new Map<string, number>())].map(([type, count]) => ({ type, count })),
    valuesets: doc.appendix.valuesets.length,
    codes: doc.appendix.codes.length,
    parameters: doc.appendix.parameters.length,
    notices: [],
    ...appendix,
  };

  function Harnessed() {
    const [navTab, setNavTab] = useState<NavTab>(tab);
    const [size, setSize] = useState<PaneSize>("normal");
    const scrollerRef = { current: null } as React.RefObject<HTMLDivElement | null>;
    return (
      <NavPaneBody
        source={root.source}
        specModel={spec} visibleSpec={visible} specDoc={doc}
        selected={selected}
        onSelectDefine={(s) => picked.push(selectionToken(s))}
        onAnchor={(id) => anchored.push(id)}
        retrieves={retrieves} metrics={metrics}
        overviewSections={{}} appendixSections={appendixSections}
        showEliminated={showEliminated} onShowEliminatedChange={() => {}}
        defineSort={sort} onDefineSortChange={() => {}}
        navTab={navTab} onNavTab={setNavTab}
        size={size} onSize={setSize} scrollerRef={scrollerRef} />
    );
  }

  const { container } = render(<Harnessed />);
  return { container, picked, anchored };
}

/** A coherent chain over the fixture's own population names, as the metrics endpoint would serve it.
 *  DECLARED A PROPORTION — the base every row's share is taken against is a function of the scoring
 *  type, and with none declared a row carries its count and no bar (SPEC-SCORING-METHODOLOGY). */
export function metricSet(): MetricSet {
  return buildMetricSet({
    library: "Demo", version: "1.0.0", provenance: "sample", generated: "2026-08-02T00:00:00Z",
    subjects: 1000,
    // SPEC-POPULATION-CASCADE — counted equals met: the fixture's populations already nest.
    defines: [
      { define: "Initial Population", library: "Demo", subjects: 800, counted: 800, cohortSubjects: 1000 },
      { define: "Denominator", library: "Demo", subjects: 800, counted: 800, cohortSubjects: 800 },
      { define: "Numerator", library: "Demo", subjects: 560, counted: 560, cohortSubjects: 800 },
      { define: "Has Screening", library: "Demo", subjects: 600, counted: 600, cohortSubjects: 1000,
        elements: 1500 },
    ],
  }, "proportion", new Map())!;
}

/** Labels of the rows in whichever arrangement is showing, in render order. */
export const rowLabels = (c: HTMLElement) =>
  [...c.querySelectorAll(".mw-node .lab")].map((e) => e.textContent);
