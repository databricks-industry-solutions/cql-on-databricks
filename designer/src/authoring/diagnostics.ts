// PHASE-S2 / APP-DIAGNOSTICS — turn the compiler's raw diagnostics into clinical, plain-language
// messages, and run the live validation loop against the engine.
//
// APP-DIAGNOSTICS: validation is on-demand via the Compiler Service (browser → BFF /api/translate →
// CQL Compiler Service), surfaced as PLAIN-LANGUAGE messages in a panel — explicitly NOT a language
// server, and NOT raw compiler markers in the gutter. Fail-loud is preserved (CORE-NO-SILENT-MISLOWER):
// `ok:false` always carries at least one message. Full natural-language explanation is S3
// (APP-NL/APP-AI-CACHE); this is a deterministic, no-token humanization so S2 already reads
// clearly for a clinical author.

import type { TranslateResult } from "../wire.ts";

export interface ClinicalDiagnostic {
  /** The plain-language message shown to the author. */
  summary: string;
  /** The original compiler text, available on demand (kept, never discarded). */
  raw: string;
}

// Best-effort cleanups: strip a leading location/severity token; humanize the few most common
// cqframework phrasings. Deterministic (no AI) — S3 enriches these.
const LOCATION_RE = /^\s*(?:\[[0-9]+:[0-9]+(?:-[0-9]+:[0-9]+)?\]|[0-9]+:[0-9]+)\s*[:-]?\s*/;
const SEVERITY_RE = /^\s*(?:ERROR|WARNING|INFO)\s*[:-]?\s*/i;

/** One raw compiler diagnostic → a clinical message (deterministic, no tokens). */
export function humanize(raw: string): ClinicalDiagnostic {
  let s = raw.replace(SEVERITY_RE, "").replace(LOCATION_RE, "").trim();

  const unresolved = /Could not resolve (?:identifier|reference)\s+(.+?)\s+in/i.exec(s);
  if (unresolved) {
    s = `“${unresolved[1]}” isn’t defined here — check the spelling, or add a define/include for it.`;
  } else if (/^Could not (?:validate|resolve)/i.test(s)) {
    s = s.replace(/^Could not/i, "Couldn’t");
  } else if (s) {
    s = s.charAt(0).toUpperCase() + s.slice(1);
  }
  return { summary: s || raw.trim(), raw: raw.trim() };
}

/** All diagnostics from a translate result, humanized in order. */
export function humanizeAll(result: TranslateResult): ClinicalDiagnostic[] {
  return result.diagnostics.map(humanize);
}

/** POST the CQL to the BFF's /api/translate (which proxies the Compiler Service). Throws only on a
 *  transport/HTTP failure; a translate that FAILS to compile is a normal 200 with `ok:false`.
 *
 *  `includeElm` additionally compiles the include closure and returns each library's ELM + source
 *  (APP-SPEC-MODEL) — the Define lens derives its entire model from ELM, so it needs the includes
 *  compiled. It costs one translate per include, so callers that only want diagnostics leave it off. */
export async function translate(
  cql: string,
  includes: string[] = [],
  opts: { includeElm?: boolean; location?: string | null } = {},
): Promise<TranslateResult> {
  const res = await fetch("/api/translate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    // RUN-ADDRESS-LOCATION — includes resolve at the open library's location first.
    body: JSON.stringify({ cql, includes, include_elm: opts.includeElm ?? false, location: opts.location ?? null }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`validation service error (${res.status})${detail ? `: ${detail}` : ""}`);
  }
  return (await res.json()) as TranslateResult;
}
