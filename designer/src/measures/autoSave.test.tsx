/** DOC-AUTO-SAVE — every edit is written where the library was opened a beat after typing stops, compiled or not;
 *  one write at a time, the latest text; a changed copy stops the writes, stale copies wait for the author. */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { cqlSha256, type SaveBody, type SaveOutcome } from "../library-client.ts";
import { useAutoSave, writeLabel, type Opened } from "./autoSave.ts";

const OPENED: Opened = { key: "lib-1", location: "folder:/Workspace/Users/a@example.org/M", name: "M",
                         version: "1.0.0", cql: "library M version '1.0.0'\n" };
const ONE = OPENED.cql + "define X: 1\n";
const TWO = OPENED.cql + "define X: 2\n";

const saved = (sha: string, compiled = true): SaveOutcome => ({
  kind: "saved", location: OPENED.location, saved: [], deleted: [], sha256: sha, compiled,
  diagnostics: compiled ? [] : ["Syntax error at 2:9"] });

/** A save whose answers the test hands out one at a time, in the order the writes were sent. */
function server() {
  const sent: SaveBody[] = [];
  const answers: ((o: SaveOutcome) => void)[] = [];
  const save = vi.fn((body: SaveBody) => {
    sent.push(body);
    return new Promise<SaveOutcome>((resolve) => answers.push(resolve));
  });
  const answer = async (o: SaveOutcome) => { await act(async () => { answers.shift()!(o); }); };
  return { save, sent, answer };
}

function mount(save: (b: SaveBody) => Promise<SaveOutcome>, initial: { opened?: Opened | null; enabled?: boolean } = {}) {
  return renderHook((p: { opened: Opened | null; edited: string | null; enabled: boolean }) =>
    useAutoSave({ ...p, save, delayMs: 2000 }),
    { initialProps: { opened: initial.opened === undefined ? OPENED : initial.opened, edited: null as string | null,
                      enabled: initial.enabled ?? true } });
}

/** Let the hook's digest of the opened CQL land: WebCrypto answers from another thread, so wait out real turns of the
 *  event loop (`setImmediate` is not faked) as well as a digest started after it. */
const settle = () => act(async () => {
  await cqlSha256("");
  for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
});
const wait = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

describe("useAutoSave", () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] }); });
  afterEach(() => { vi.useRealTimers(); });

  it("writes the latest text when typing stops, based on the CQL as opened", async () => {
    const s = server();
    const h = mount(s.save);
    await settle();
    h.rerender({ opened: OPENED, edited: ONE, enabled: true });
    await wait(1000);
    h.rerender({ opened: OPENED, edited: TWO, enabled: true });
    expect(h.result.current.state.kind).toBe("pending");
    await wait(1999);
    expect(s.sent).toHaveLength(0);
    await wait(1);
    expect(s.sent).toEqual([{ location: OPENED.location, name: "M", version: "1.0.0", cql: TWO,
                              baseSha256: await cqlSha256(OPENED.cql) }]);
    expect(h.result.current.state.kind).toBe("saving");
    await s.answer(saved("sha-two"));
    expect(h.result.current.state).toEqual({ kind: "saved", compiled: true, diagnostics: [] });
    expect(h.result.current.unwritten).toBe(false);
  });

  it("writes one at a time: an edit made while a write is out goes next, on the base that write left", async () => {
    const s = server();
    const h = mount(s.save);
    await settle();
    h.rerender({ opened: OPENED, edited: ONE, enabled: true });
    await wait(2000);
    h.rerender({ opened: OPENED, edited: TWO, enabled: true });
    await wait(5000);
    expect(s.sent).toHaveLength(1);
    expect(h.result.current.unwritten).toBe(true);
    await s.answer(saved("sha-one"));
    await wait(2000);
    expect(s.sent.map((b) => [b.cql, b.baseSha256])).toEqual([[ONE, await cqlSha256(OPENED.cql)], [TWO, "sha-one"]]);
  });

  it("says when CQL that does not compile was written with no ELM", async () => {
    const s = server();
    const h = mount(s.save);
    await settle();
    h.rerender({ opened: OPENED, edited: ONE, enabled: true });
    await wait(2000);
    await s.answer(saved("sha-one", false));
    expect(h.result.current.state).toEqual({ kind: "saved", compiled: false, diagnostics: ["Syntax error at 2:9"] });
    expect(writeLabel(h.result.current.state)).toBe("Saved, does not compile");
  });

  it("stops writing when the copy changed elsewhere", async () => {
    const s = server();
    const h = mount(s.save);
    await settle();
    h.rerender({ opened: OPENED, edited: ONE, enabled: true });
    await wait(2000);
    await s.answer({ kind: "conflict", error: "M changed since it was opened" });
    h.rerender({ opened: OPENED, edited: TWO, enabled: true });
    await wait(10000);
    act(() => h.result.current.flush());
    expect(s.sent).toHaveLength(1);
    expect(h.result.current.state.kind).toBe("conflict");
    expect(h.result.current.unwritten).toBe(true);
  });

  it("writes nothing over stale copies until the author agrees to delete them", async () => {
    const s = server();
    const h = mount(s.save);
    await settle();
    h.rerender({ opened: OPENED, edited: ONE, enabled: true });
    await wait(2000);
    await s.answer({ kind: "stale", paths: ["/x/library-M-1.0.0.xml"] });
    await wait(10000);
    expect(s.sent).toHaveLength(1);
    expect(h.result.current.state).toEqual({ kind: "stale", paths: ["/x/library-M-1.0.0.xml"] });
    act(() => h.result.current.agreeStale());
    expect(s.sent[1]).toMatchObject({ cql: ONE, deleteStale: ["/x/library-M-1.0.0.xml"] });
  });

  it("does not resend a refused text, and tries again on the next edit", async () => {
    const s = server();
    const h = mount(s.save);
    await settle();
    h.rerender({ opened: OPENED, edited: ONE, enabled: true });
    await wait(2000);
    await s.answer({ kind: "refused", error: "the CQL declares N 1.0.0, not M 1.0.0" });
    await wait(10000);
    expect(s.sent).toHaveLength(1);
    expect(h.result.current.state).toEqual({ kind: "refused", error: "the CQL declares N 1.0.0, not M 1.0.0" });
    h.rerender({ opened: OPENED, edited: TWO, enabled: true });
    await wait(2000);
    expect(s.sent.map((b) => b.cql)).toEqual([ONE, TWO]);
  });

  it("never writes a library it may not write", async () => {
    const s = server();
    const h = mount(s.save, { enabled: false });
    await settle();
    h.rerender({ opened: OPENED, edited: ONE, enabled: false });
    await wait(10000);
    act(() => h.result.current.flush());
    expect(s.sent).toHaveLength(0);
  });

  it("starts again for another library, and ignores a write still out for the one before", async () => {
    const s = server();
    const h = mount(s.save);
    await settle();
    h.rerender({ opened: OPENED, edited: ONE, enabled: true });
    await wait(2000);
    const other = { ...OPENED, key: "lib-2", name: "N", cql: "library N version '1.0.0'\n" };
    h.rerender({ opened: other, edited: null, enabled: true });
    await settle();
    await s.answer(saved("sha-one"));
    expect(h.result.current.state.kind).toBe("idle");
    h.rerender({ opened: other, edited: other.cql + "define Y: 1\n", enabled: true });
    await wait(2000);
    expect(s.sent[1]).toMatchObject({ name: "N", baseSha256: await cqlSha256(other.cql) });
  });
});
