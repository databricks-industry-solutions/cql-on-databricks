/** PERF-CLIENT-REVISION — the React half: a mounted view notices the revision moving.
 *
 *  `data-cache.ts` clears itself, but a mounted component holds its own state and would keep showing
 *  what it fetched. This is the seam that closes it, and the tests below pin the two properties that
 *  make it safe: it does NOT fire on the first revision (nothing is cached to be wrong about), and it
 *  DOES fire on every subsequent change.
 */
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { REVISION_HEADER, clear, noteRevision } from "./data-cache.ts";
import { useRevision } from "./use-revision.ts";

/** A response arriving with `revision`. Wrapped in `act` because the revision reaches React from OUTSIDE
 *  its event system — a `fetch` completing, not a click — which is exactly how it happens in the app. */
const seen = (revision: string) =>
  act(() => { noteRevision(new Headers({ [REVISION_HEADER]: revision })); });

describe("useRevision", () => {
  it("starts unknown and stays put on the FIRST revision it learns", () => {
    clear();
    const { result } = renderHook(() => useRevision());
    expect(result.current).toBeNull();

    // The first response teaches the cache its revision WITHOUT notifying: nothing was cached yet, and
    // firing here would refetch everything the page had just loaded.
    seen("rev-1");
    expect(result.current).toBeNull();
  });

  it("re-renders when the revision moves", () => {
    clear();
    const { result } = renderHook(() => useRevision());
    seen("rev-1");
    seen("rev-2");
    expect(result.current).toBe("rev-2");
  });

  it("keeps following subsequent moves", () => {
    clear();
    const { result } = renderHook(() => useRevision());
    seen("rev-1");
    seen("rev-2");
    seen("rev-3");
    expect(result.current).toBe("rev-3");
  });

  it("unsubscribes on unmount", () => {
    clear();
    const { result, unmount } = renderHook(() => useRevision());
    seen("rev-1");
    seen("rev-2");
    expect(result.current).toBe("rev-2");

    unmount();
    // No throw and no update after teardown — a listener left behind would set state on an unmounted
    // component for the life of the tab.
    expect(() => seen("rev-3")).not.toThrow();
    expect(result.current).toBe("rev-2");
  });
});
