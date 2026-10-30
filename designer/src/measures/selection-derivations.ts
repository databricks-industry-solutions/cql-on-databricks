// RUN-CLIENT-RESULT-SETS — publish the document's derived figures together for one exact selection.
import { useEffect, useState } from "react";
import { fetchCohortFlow, fetchMeasureMetrics, fetchReadMetrics, type ResultReadScope } from "../library-client.ts";

export interface SelectionDerivationRequest {
  library: string;
  version: string;
  scope: ResultReadScope;
  defines: Parameters<typeof fetchMeasureMetrics>[2];
  reads: Parameters<typeof fetchReadMetrics>[2];
  gates: Parameters<typeof fetchCohortFlow>[2];
  /** RUN-CLIENT-MEASURE-SCORE — the measure's declared scoring, for the server's score. */
  scoring: string | null;
}

interface Derivations {
  metrics: Awaited<ReturnType<typeof fetchMeasureMetrics>>;
  reads: Awaited<ReturnType<typeof fetchReadMetrics>>;
  flow: Awaited<ReturnType<typeof fetchCohortFlow>>;
  error: string | null;
}
const EMPTY: Derivations = { metrics: null, reads: null, flow: null, error: null };

/** RUN-CLIENT-RESULT-SETS — neither late responses nor independently settled readers can mix pins. */
export function useSelectionDerivations(request: SelectionDerivationRequest | null): Derivations {
  const key = JSON.stringify(request);
  const [completed, setCompleted] = useState<{ key: string; value: Derivations } | null>(null);
  useEffect(() => {
    const input = JSON.parse(key) as SelectionDerivationRequest | null;
    if (!input) return;
    let current = true;
    const { library, version, scope, defines, reads, gates, scoring } = input;
    Promise.all([
      defines.length ? fetchMeasureMetrics(library, version, defines, scope, scoring) : null,
      reads.length ? fetchReadMetrics(library, version, reads, scope) : null,
      gates.length ? fetchCohortFlow(library, version, gates, scope) : null,
    ]).then(([metrics, readMetrics, flow]) => {
      // A null is the server's 409: no figures for this selection. The whole set is absent, never partial.
      const absent = (defines.length && !metrics) || (reads.length && !readMetrics) || (gates.length && !flow);
      if (current) setCompleted({ key, value: absent ? EMPTY : { metrics, reads: readMetrics, flow, error: null } });
    }).catch((error: unknown) => {
      if (current) setCompleted({ key, value: { ...EMPTY,
        error: error instanceof Error ? error.message : "Could not read the selected calculation." } });
    });
    return () => { current = false; };
  }, [key]);
  return completed?.key === key ? completed.value : EMPTY;
}
