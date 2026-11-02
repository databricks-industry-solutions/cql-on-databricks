/** PERF-CLIENT-REVISION — the SPA's data cache, busted by the server's own revision.
 *
 *  WHAT IT FIXES. Without it, every read is reissued on every mount, so clicking away from the inspector and back
 *  blanks the pane and refetches. For the five GET reads that is cheap on the server — the browser revalidates and
 *  `PERF-CACHE-REVISION` answers 304 before the handler runs — but a round trip is a round trip and the pane still
 *  flashes. For the three POST reads (`/metrics`, `/cohort-flow`, `/read-metrics`) there is no caching at all, at any
 *  layer: the middleware early-returns on non-GET, so coming back re-executes several SQL warehouse statements to
 *  recompute figures that have not changed.
 *
 *  WHY HTTP CACHING CANNOT DO THIS. `Cache-Control: private, no-cache` means "cache this, and
 *  revalidate before reuse" — the browser is FORBIDDEN from answering without asking the server. That
 *  is the right contract for correctness and it structurally cannot give a synchronous render. The
 *  ETag is also addressed to the browser's cache rather than to us; nothing in the SPA can read it.
 *
 *  THIS IS NOT A SECOND FRESHNESS AUTHORITY, and that distinction is the whole design. It never
 *  decides that data is still good. It replays the last server-adjudicated answer for exactly as long
 *  as the SERVER's own revision says that answer stands, and drops everything the moment the revision
 *  moves. The rule is one comparison, not a policy:
 *
 *      revision unchanged -> serve from memory, no request
 *      revision moved     -> clear EVERYTHING, refetch
 *
 *  WHOLESALE, NEVER PER ENTRY. The revision is global (`cache_stamp.py`), so one value answers "is
 *  anything I hold still valid" for every entry at once. That is what a per-resource ETag could not
 *  do, and it is why there is no per-endpoint TTL, no dependency graph between queries, and nothing
 *  for a future reader to tune. The common SPA alternative — invalidating per query from the mutation
 *  that caused it — requires remembering which write affects which read, and forgetting is silent.
 *
 *  THE `max-age=60` OBJECTION, ANSWERED. A client cache would reintroduce the defect that
 *  `PERF-CACHE-REVISION` rules out: "a define's profile stayed stale for a minute AFTER the run that
 *  changed it, with nothing able to invalidate it". That defect is TIME-BASED expiry with no
 *  invalidation path. This cache has no clock. It cannot outlive the revision, because
 *  the revision is the only thing that keeps an entry alive.
 */

/** The header every `/api` response carries (`cache_stamp.REVISION_HEADER`). */
export const REVISION_HEADER = "X-CqlDatabricks-Revision";

/** The revision the server last reported, or `null` before the first response.
 *
 *  `null` is "unknown", never "stale": until a response has been seen there is nothing cached to be
 *  wrong about, and treating unknown as a mismatch would clear an empty map.
 */
let revision: string | null = null;

/** `key -> resolved body`. Cleared WHOLESALE when the revision moves. */
const entries = new Map<string, unknown>();

/** In-flight requests, so two components mounting at once make ONE request.
 *
 *  Separate from `entries` on purpose: an entry is an answer, this is a promise of one. Collapsing
 *  them would either cache a rejection or leave a failed read poisoning the key.
 */
const inflight = new Map<string, Promise<unknown>>();

/** Callbacks fired after the cache is cleared, so live views can refetch (`PERF-CLIENT-REVISION`). */
const listeners = new Set<() => void>();

/** Record the revision from a response, clearing the cache if it moved.
 *
 *  Called on EVERY response, including errors and 304s: the header reports the server's revision,
 *  which is a fact about the server rather than about the body, so a 500 carries it as truthfully as
 *  a 200. A response WITHOUT the header (a dev proxy, a non-`/api` route, an older server) is
 *  ignored rather than treated as a change — an absent header is no information, and reading it as
 *  "moved" would clear the cache on every such response and defeat the whole mechanism.
 */
export function noteRevision(headers: Headers | undefined | null): void {
  // LEARNING THE REVISION MUST NEVER FAIL THE READ IT RODE ALONG WITH, and that is a property rather
  // than a defensive habit. This runs on the success path of every request, so a throw here would turn
  // "we could not read a response header" into "the read failed" — and the caller would then report a
  // failure for a request that actually succeeded. Not learning the revision is already handled: the cache keeps what it has
  // and the next response corrects it.
  const seen = headers?.get?.(REVISION_HEADER);
  if (!seen) return;

  // ORDER IS LOAD-BEARING: adopt the new revision, clear, THEN notify. A subscriber's whole job is to
  // react to the current state, so it must observe the new revision and a cold cache — notifying first
  // handed it the PREVIOUS value and a map it was about to lose, which made `currentRevision()` inside a
  // listener a report of the state that had just ended.
  const previous = revision;
  revision = seen;
  if (previous !== null && seen !== previous) {
    entries.clear();
    // In-flight requests are deliberately NOT cancelled. A request issued against the old revision is
    // still answered from current state, so its result is newer than what was just
    // dropped. Cancelling would turn a revision change into a visible failure.
    for (const notify of listeners) notify();
  }
}

/** Subscribe to cache clears. Returns an unsubscribe. */
export function onRevisionChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** What the cache holds for `key`, or `undefined`. SYNCHRONOUS — this is what removes the flash.
 *
 *  A hook seeds its initial state from this, so the first render already has the data and there is no
 *  frame in which the pane is blank. Returning `undefined` for a miss rather than `null` matters: a
 *  cached body may legitimately BE `null` (a profile this evaluation never materialized), and the two
 *  must not collapse or an absent answer would be refetched forever.
 */
export function peek<T>(key: string): T | undefined {
  return entries.has(key) ? (entries.get(key) as T) : undefined;
}

/** The current revision, for diagnostics. */
export function currentRevision(): string | null {
  return revision;
}

/** Clear everything and notify. For tests and for an explicit user-driven refresh. */
export function clear(): void {
  entries.clear();
  revision = null;
  for (const notify of listeners) notify();
}

/** Ask the server for its revision and nothing else (`GET /api/cache/revision`).
 *
 *  THE ONE CASE THE HEADER CANNOT SERVE. Every response carries the revision, so ordinary work keeps
 *  the cache honest for free. But a client that wants to know whether its cache is valid while making
 *  no other request has nothing to read — which is exactly the refocus case this exists for. One
 *  cheap `no-store` GET replaces reissuing every expensive read to discover nothing changed.
 *
 *  Failures are swallowed: not learning the revision leaves the cache as it was, which is the same
 *  position as before the call. The next real read carries the header anyway.
 */
export async function refreshRevision(): Promise<void> {
  try {
    const res = await fetch("/api/cache/revision", { headers: { accept: "application/json" } });
    noteRevision(res.headers);
  } catch {
    // Offline or mid-deploy. The cache keeps what it has and the next read corrects it.
  }
}

/** Read through the cache, keyed by `key`, fetching with `send` on a miss.
 *
 *  `send` returns the parsed body; it is given the raw `Response` so the caller keeps its own status
 *  handling (a 404 that means "no specification" is a value, not an error — `library-client.ts`).
 *
 *  A REJECTED read is not cached, and its in-flight entry is dropped, so a transient failure retries
 *  on the next mount instead of being remembered as an answer.
 */
export async function through<T>(key: string, send: () => Promise<T>): Promise<T> {
  if (entries.has(key)) return entries.get(key) as T;

  const existing = inflight.get(key);
  if (existing) return existing as Promise<T>;

  const promise = send()
    .then((value) => {
      // Re-check the map rather than writing blind: `noteRevision` may have cleared it while this was
      // in flight, and this value was fetched against the newer state, so it is the right answer to
      // keep. Writing it back is correct; what would be wrong is assuming the key is still absent.
      entries.set(key, value);
      return value;
    })
    .finally(() => { inflight.delete(key); });

  inflight.set(key, promise as Promise<unknown>);
  return promise;
}

/** A WRITE: fetch, learn the revision from the response, cache nothing.
 *
 *  **THE POINT IS THAT A WRITE INVALIDATES IMMEDIATELY AND FOR FREE.** A successful mutation moves the
 *  server's revision by construction — the Data Source and Knowledge Source registries are among the
 *  stores the stamp digests — and the mutation's OWN response already carries the new value. Reading it
 *  here clears the cache the instant the write lands, so the list a user just added to is refetched on
 *  the next read rather than waiting for a refocus.
 *
 *  A SEPARATE FUNCTION FROM `cachedGet`/`cachedPost` rather than a flag on them, because "cache this"
 *  and "this changes things" are opposite intents and a boolean parameter reads the same at both call
 *  sites. Every write goes through this name, so noting the revision is not something a call site can
 *  forget — which is exactly how the cached Data Source list went stale after a create.
 *
 *  Returns the raw `Response`: callers own their own error text and status handling.
 */
export async function mutate(url: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(url, init);
  noteRevision(res.headers);
  return res;
}

/** Re-check the revision whenever the user comes BACK to the tab (`PERF-CLIENT-REVISION`).
 *
 *  Refocus is the moment that matters, and it is the moment the header cannot cover: returning to the
 *  tab makes no request, so without this the SPA would either serve data it has not validated since it
 *  lost focus, or reissue every read to find out nothing changed. One `no-store` GET answers for the
 *  whole cache.
 *
 *  `visibilitychange` rather than `focus`: a background tab that becomes visible is the case (switching
 *  tabs, unlocking, returning from another app), and `focus` misses a visible-but-unfocused window
 *  while firing for every click into the page. Installed once from the entry point rather than from a
 *  component, because the cache is app-lifetime and a component's mount is not.
 *
 *  Returns a teardown so a test can install it deterministically instead of inheriting a module-level
 *  side effect on import.
 */
export function installRevisionRefresh(): () => void {
  const onVisible = () => { if (document.visibilityState === "visible") void refreshRevision(); };
  document.addEventListener("visibilitychange", onVisible);
  return () => document.removeEventListener("visibilitychange", onVisible);
}

/** A stable cache key for a GET: the URL is already the whole question. */
export function getKey(url: string): string {
  return `GET ${url}`;
}

/** A cached GET. `parse` keeps the caller's own status handling — a 404 that means "no specification
 *  published" is a VALUE worth caching, while a 500 should throw and not be remembered, and only the
 *  caller knows which of its statuses is which. */
export async function cachedGet<T>(url: string, parse: (res: Response) => Promise<T>): Promise<T> {
  return through(getKey(url), async () => {
    const res = await fetch(url);
    noteRevision(res.headers);
    return parse(res);
  });
}

/** A cached POST read — the three expensive ones, which no HTTP cache can touch.
 *
 *  The body is serialized ONCE and used both as the key and as the request payload, so the key cannot
 *  describe a different question from the one asked.
 */
export async function cachedPost<T>(
  url: string, body: unknown, parse: (res: Response) => Promise<T>,
): Promise<T> {
  const payload = JSON.stringify(body);
  return through(postKey(url, payload), async () => {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
    });
    noteRevision(res.headers);
    return parse(res);
  });
}

/** A stable cache key for a POST read, where the BODY is the question (`/metrics`, `/cohort-flow`).
 *
 *  Serialized rather than hashed: these bodies are small (a define list, a gate set) and a hash would
 *  add a collision mode to save nothing. Keys are compared, never transmitted.
 *
 *  `JSON.stringify` is used on the body the caller is about to SEND, so the key and the request cannot
 *  describe different questions — deriving the key from the arguments instead is how two callers with
 *  the same arguments and different serialization order would collide on one entry.
 */
export function postKey(url: string, body: string): string {
  return `POST ${url} ${body}`;
}
