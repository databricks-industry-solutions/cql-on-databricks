// DOC-ENVIRONMENT-PICKER — the picked environment, which every knowledge read and request names.

import { afterEach, describe, expect, it, vi } from "vitest";
import { currentEnvironment, setCurrentEnvironment, withEnvironment } from "./environment.ts";

afterEach(() => { setCurrentEnvironment(null); vi.unstubAllGlobals(); });

describe("DOC-ENVIRONMENT-PICKER environment", () => {
  it("leaves a URL alone for the default, and names a picked environment", () => {
    expect(withEnvironment("/api/libraries/discover")).toBe("/api/libraries/discover");
    setCurrentEnvironment("team a");
    expect(currentEnvironment()).toBe("team a");
    expect(withEnvironment("/api/libraries/discover")).toBe("/api/libraries/discover?environment=team%20a");
    expect(withEnvironment("/api/folders?path=x")).toBe("/api/folders?path=x&environment=team%20a");
  });

  it("keeps the pick across a reload", () => {
    // This jsdom setup ships `localStorage` with no Storage API, so the test supplies one (as model.test.tsx does).
    const held = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (k: string) => held.get(k) ?? null,
      setItem: (k: string, v: string) => { held.set(k, v); }, removeItem: (k: string) => { held.delete(k); } });
    setCurrentEnvironment("example");
    expect(localStorage.getItem("cql-databricks:environment")).toBe("example");
    setCurrentEnvironment(null);
    expect(localStorage.getItem("cql-databricks:environment")).toBeNull();
  });
});
