// KNOWLEDGE-LAKE-DIRECT (PROC-TDD) — the SPA library client: record→Definition mapping + fetch.
import { describe, it, expect, vi, afterEach } from "vitest";
import { toDefinition, fetchDefinitions, fetchDefineProfile, peekDefineProfile } from "./library-client.ts";
import { fetchResults } from "./run-client.ts";
import type { ResultSetAddress } from "./wire.ts";

const measure = {
  id: { name: "ExampleMeasure", version: "1.0.0" },
  kind: "measure" as const,
  status: "published" as const,
  model: "FHIR",
  title: "Example Measure",
  metadata: { description: "Patients with an example condition whose reading is below threshold.", tags: ["ecqm"], author: "you" },
  updated: "2026-06-15T00:00:00.000Z",
};

describe("result request transport (RUN-CLIENT-REQUEST-REOPEN)", () => {
  afterEach(() => vi.unstubAllGlobals());

  const resultSet: ResultSetAddress = {
    destination: ["synthetic", "results"], publisher: "synthetic-owner", session_id: "session",
    selection_id: "generation-digest", request_hash: "request", purpose: "private",
    population: "synthetic", targets: ["Synthetic.Output"], computed_since: null,
    calculation: { data_source: "synthetic", runtime_catalog: "synthetic", runtime_schema: "runtime",
      cql: "library Synthetic version '1'\ndefine Output: 1", period: ["2026-01-01", "2026-12-31"],
      targets: ["Synthetic.Output"] },
  };

  it("sends profile context in a POST body and keys the cache by the whole request", async () => {
    const profile = { kind: "synthetic-profile" };
    const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify(profile)));
    vi.stubGlobal("fetch", fetcher);
    const scope = { dataSourceId: "synthetic", draft: "durable.session", resultSet };
    expect(await fetchDefineProfile("Synthetic", "1", "Output", "Synthetic", scope)).toEqual(profile);
    expect(peekDefineProfile("Synthetic", "1", "Output", "Synthetic", scope)).toEqual(profile);
    const changed = { ...scope, resultSet: { ...resultSet, selection_id: "new-generation" } };
    expect(peekDefineProfile("Synthetic", "1", "Output", "Synthetic", changed)).toBeUndefined();
    await fetchDefineProfile("Synthetic", "1", "Output", "Synthetic", scope);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/libraries/Synthetic/1/defines/Output/profile");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ defineLibrary: "Synthetic", ...scope });
  });

  it("sends result context without exposing draft CQL in the URL", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ defines: [] })));
    vi.stubGlobal("fetch", fetcher);
    await fetchResults("synthetic", "Synthetic", "1", "2026-01-01", "2026-12-31", "durable.session", resultSet);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/results");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body).resultSet).toEqual(resultSet);
  });
});

describe("toDefinition (record → Definition)", () => {
  it("maps a published measure with model from the discover DTO (PERF-DISCOVER-LIGHTWEIGHT)", () => {
    const d = toDefinition(measure);
    expect(d.id).toBe("ExampleMeasure@1.0.0");
    expect(d.title).toBe("Example Measure");
    expect(d.cat).toBe("ecqm");
    expect(d.status).toBe("pub");
    expect(d.mine).toBe(true);
    expect(d.source).toBeUndefined();
    expect(d.src).toContain("FHIR");
    expect(d.desc).toBe("Patients with an example condition whose reading is below threshold.");
  });

  it("maps a library to the lib category with a qualitative stat (no fabricated rate)", () => {
    const d = toDefinition({ ...measure, kind: "library", title: "", id: { name: "FHIRHelpers", version: "4.0.1" } });
    expect(d.cat).toBe("lib");
    expect(d.title).toBeUndefined();
    expect(d.stat).toBe("reusable");
    expect(d.statk).toBe("");
  });

  it("maps a draft measure's status", () => {
    expect(toDefinition({ ...measure, status: "draft" }).status).toBe("draft");
  });
});

describe("fetchDefinitions", () => {
  const realFetch = globalThis.fetch;
  // The data cache is reset for every test in `src/test/setup.ts` (PERF-CLIENT-REVISION), which is why
  // the error case below reaches its failing branch instead of reading the success case's cached index.
  afterEach(() => { globalThis.fetch = realFetch; vi.restoreAllMocks(); });

  it("fetches and maps the library index from UC", async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify([measure]), { status: 200 })) as typeof fetch;
    const defs = await fetchDefinitions();
    expect(defs).toHaveLength(1);
    expect(defs[0]!.name).toBe("ExampleMeasure");
  });

  it("throws on an HTTP error so the caller can fall back", async () => {
    globalThis.fetch = vi.fn(async () => new Response("nope", { status: 503 })) as typeof fetch;
    await expect(fetchDefinitions()).rejects.toThrow(/library discover failed: 503/);
  });
});
