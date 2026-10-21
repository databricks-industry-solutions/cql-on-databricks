/** PERF-CLIENT-REVISION — the React half of the data cache: re-render when the revision moves.
 *
 *  `data-cache.ts` clears itself when the server's revision changes, but a MOUNTED view holds its own
 *  state and would keep showing what it fetched until something remounted it. That gap only shows for
 *  work done somewhere this tab cannot see — a scheduled run finishing (`PERF-CACHE-REVISION`'s `runs`
 *  term), a second tab editing the registry, an operator calling `POST /api/cache/bust`. The local run
 *  path is already covered, because the SSE stream sets state directly.
 *
 *  **THE HOOK IS A DEPENDENCY, NOT A REFETCHER.** It returns the current revision and re-renders on a
 *  change; a view opts in by naming it in the dependency array of the effect it already has. The effect
 *  then reruns, the cache misses (it was just cleared), and the read happens through the ordinary path.
 *  No second fetch mechanism, no per-view subscription bookkeeping, and nothing that can fetch in a
 *  different way from the mount path it has to agree with.
 *
 *  Deliberately in its own module rather than in `data-cache.ts`: the data layer is framework-free and
 *  worth keeping that way — it is imported by plain clients (`library-client.ts`, `run-client.ts`) that
 *  have no business pulling in React.
 */
import { useEffect, useState } from "react";

import { currentRevision, onRevisionChange } from "./data-cache.ts";

/** The server's current revision, re-rendering the caller whenever it moves.
 *
 *  `null` until the first response has been seen. That is "unknown", not "stale" — see `noteRevision`,
 *  which deliberately does NOT notify on the first revision it learns: nothing is cached yet to be
 *  wrong about, and firing would refetch everything the page had just loaded.
 */
export function useRevision(): string | null {
  const [revision, setRevision] = useState<string | null>(currentRevision);
  useEffect(() => onRevisionChange(() => setRevision(currentRevision())), []);
  return revision;
}
