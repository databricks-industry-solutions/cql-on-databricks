/** DOC-AUTO-SAVE — a library's CQL is written where it was opened a beat after typing stops, whether or not it
 *  compiles. There is no Save button.
 *
 *  One write is in flight at a time and it writes the latest text; an edit made while it is out is written when it
 *  comes back. Each write sends, as its base, the digest of what the last one left there (or of the CQL as opened),
 *  so a copy changed by someone else is refused rather than overwritten (`DOC-OPEN-SAVE`). A refusal of that kind
 *  stops writing for the library until it is opened again; files the write would leave out of date wait for the
 *  author to agree to delete them; any other refusal is shown and the next edit tries again. */
import { useCallback, useEffect, useRef, useState } from "react";

import { cqlSha256, type SaveBody, type SaveOutcome } from "../library-client.ts";

/** Where the open library is written, and the CQL it was opened with. `key` changes when the library, or its
 *  reading, does: a reload of the same library is a new `key`. */
export interface Opened {
  key: string;
  location: string;
  name: string;
  version: string;
  cql: string;
}

export type WriteState =
  /** Nothing edited since it was opened. */
  | { kind: "idle" }
  /** Edited; waiting for typing to stop, or for the write before it. */
  | { kind: "pending" }
  | { kind: "saving" }
  /** The latest edit is written. `compiled` false: written with no ELM, for `diagnostics`. */
  | { kind: "saved"; compiled: boolean; diagnostics: string[] }
  /** Not written: it would leave `paths` out of date, and waits for the author to agree to delete them. */
  | { kind: "stale"; paths: string[] }
  /** Not written, and writing has stopped: the copy changed since it was opened or last written. */
  | { kind: "conflict"; error: string }
  /** The latest edit was not written; the next edit tries again. */
  | { kind: "refused"; error: string };

type Outcome =
  | { kind: "saved"; compiled: boolean; diagnostics: string[] }
  | { kind: "stale"; paths: string[] }
  | { kind: "conflict"; error: string }
  | { kind: "refused"; error: string; cql: string };

export interface AutoSave {
  state: WriteState;
  /** The latest edit is not yet written: pending, saving or refused. Leaving now loses it. */
  unwritten: boolean;
  /** Write now rather than when typing stops. */
  flush: () => void;
  /** Agree to delete the files a `stale` write named, and write. */
  agreeStale: () => void;
}

export function useAutoSave({ opened, edited, enabled, save, delayMs = 2000 }: {
  opened: Opened | null;
  /** The CQL as edited; null while it is as opened. */
  edited: string | null;
  /** False for a read-only library, and while it is not yet known whether it is one. */
  enabled: boolean;
  save: (body: SaveBody) => Promise<SaveOutcome>;
  delayMs?: number;
}): AutoSave {
  // What is there now, as far as this page knows: the CQL and its digest, the base the next write sends.
  const [written, setWritten] = useState<{ key: string; cql: string; sha: string } | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [saving, setSaving] = useState(false);
  const live = useRef({ opened, edited, written, outcome, enabled, saving });
  live.current = { opened, edited, written, outcome, enabled, saving };

  // A library opened, or opened again, starts from what it was opened with. A write still out for the one before
  // lands on its own library and is ignored here (`key`).
  useEffect(() => {
    setWritten(null);
    setOutcome(null);
    setSaving(false);
    if (!opened) return;
    let current = true;
    const { key, cql } = opened;
    void cqlSha256(cql).then((sha) => { if (current) setWritten({ key, cql, sha }); });
    return () => { current = false; };
  }, [opened?.key, opened?.cql]); // eslint-disable-line react-hooks/exhaustive-deps

  const write = useCallback(async (deleteStale?: string[]) => {
    const { opened, edited, written, outcome, enabled, saving } = live.current;
    if (!opened || !written || written.key !== opened.key || !enabled || saving) return;
    if (edited === null || edited === written.cql) return;
    if (outcome?.kind === "conflict") return;
    if (outcome?.kind === "stale" && !deleteStale) return;
    const cql = edited;
    const key = opened.key;
    setSaving(true);
    let next: Outcome;
    try {
      const out = await save({ location: opened.location, name: opened.name, version: opened.version, cql,
                               baseSha256: written.sha, ...(deleteStale ? { deleteStale } : {}) });
      if (out.kind === "saved") {
        next = { kind: "saved", compiled: out.compiled, diagnostics: out.diagnostics };
        if (live.current.opened?.key === key) setWritten({ key, cql, sha: out.sha256 });
      } else if (out.kind === "stale") next = { kind: "stale", paths: out.paths };
      else if (out.kind === "conflict") next = { kind: "conflict", error: out.error };
      else next = { kind: "refused", error: out.error, cql };
    } catch (e) {
      next = { kind: "refused", error: e instanceof Error ? e.message : String(e), cql };
    }
    if (live.current.opened?.key !== key) return;
    setOutcome(next);
    setSaving(false);
  }, [save]);

  // Typing stopped: write. Also after a write comes back, so an edit made while it was out is written too. A
  // refused text is not sent again until it changes.
  const due = enabled && !saving && !!opened && !!written && written.key === opened.key
    && edited !== null && edited !== written.cql
    && outcome?.kind !== "conflict" && outcome?.kind !== "stale"
    && !(outcome?.kind === "refused" && outcome.cql === edited);
  useEffect(() => {
    if (!due) return;
    const t = setTimeout(() => { void write(); }, delayMs);
    return () => clearTimeout(t);
  }, [due, edited, written, delayMs, write]);

  const unwritten = !!opened && edited !== null && edited !== (written?.key === opened.key ? written.cql : opened.cql);
  const state: WriteState =
    saving ? { kind: "saving" }
    : outcome?.kind === "conflict" ? { kind: "conflict", error: outcome.error }
    : outcome?.kind === "stale" ? { kind: "stale", paths: outcome.paths }
    : unwritten && outcome?.kind === "refused" && outcome.cql === edited ? { kind: "refused", error: outcome.error }
    : unwritten ? { kind: "pending" }
    : outcome?.kind === "saved" ? outcome
    : { kind: "idle" };
  return {
    state, unwritten,
    flush: useCallback(() => { void write(); }, [write]),
    agreeStale: useCallback(() => {
      const o = live.current.outcome;
      if (o?.kind === "stale") void write(o.paths);
    }, [write]),
  };
}

/** The status beside the library's name. */
export function writeLabel(state: WriteState): string {
  switch (state.kind) {
    case "idle": return "Saved";
    case "pending": return "Edited";
    case "saving": return "Saving…";
    case "saved": return state.compiled ? "Saved" : "Saved, does not compile";
    case "stale": return "Not saved: waiting for you";
    case "conflict": return "Not saved: changed elsewhere";
    case "refused": return "Not saved";
  }
}

/** The status's tooltip: where it is written, and what a write that did not go as edited left there. */
export function writeTitle(state: WriteState, location: string): string {
  const where = location.replace(/^(store|folder):/, "");
  switch (state.kind) {
    case "idle": return `As it is in ${where}`;
    case "pending": return `Saves to ${where} when typing stops (⌘S saves now)`;
    case "saving": return `Saving to ${where}`;
    case "saved": return state.compiled ? `Saved to ${where}`
      : `Saved to ${where} with no ELM, because the CQL does not compile:\n${state.diagnostics.join("\n")}`;
    case "stale": return "Agree to delete the out-of-date copies to save";
    case "conflict": return state.error;
    case "refused": return state.error;
  }
}
