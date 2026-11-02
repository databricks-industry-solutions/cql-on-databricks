// DOC-PUBLISH — publish the open folder library into the knowledge store of the environment the Designer runs in,
// as the signed-in user, through the client's publish (CLIENT-PUBLISH, DOC-ENVIRONMENT-PICKER). The library's
// includes in the folder go with it, and only what changed is written; the answer lists each library as published,
// unchanged, refused or not written.

import { useState } from "react";
import type { Definition } from "../catalog.ts";
import { currentEnvironment } from "../environment.ts";
import { publishLibrary, type PublishSummary } from "../library-client.ts";

export function PublishForm({ definition, environmentName, onClose }: {
  definition: Definition;
  /** The picked environment's name, as the picker shows it; null until the environments are read. */
  environmentName?: string | null;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [summary, setSummary] = useState<PublishSummary | null>(null);

  async function publish() {
    setBusy(true);
    setProblem(null);
    setSummary(null);
    try {
      const out = await publishLibrary({ location: definition.location, name: definition.name,
                                         version: definition.version });
      setSummary(out.summary);
      if (out.kind !== "published") {
        setProblem(out.kind === "refused" ? `Nothing was published: ${out.error}`
          : `Publishing stopped: ${out.error}. The libraries marked published were written.`);
      }
    } catch (e) {
      setProblem(`Not published: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const title = `Publish ${definition.title ?? definition.name}`;
  const into = environmentName ?? currentEnvironment() ?? "the default environment";
  return (
    <form className="mw-save-stale mw-newlib" role="dialog" aria-label={title}
      onSubmit={(e) => { e.preventDefault(); if (!busy) void publish(); }}>
      <p><b>{title}</b> — with the libraries it includes from this folder, into <b>{into}</b>. Only what changed is
        written, and a published version is replaced.</p>
      {problem && <div className="mw-newlib-problem" role="alert">{problem}</div>}
      {summary && (
        <ul className="mw-publish-result" aria-label="What was published">
          {summary.libraries.map((lib) => (
            <li key={`${lib.name}@${lib.version}`} className={lib.outcome.replace(" ", "-")}>
              <code>{lib.name} {lib.version}</code> <span>{lib.outcome}</span>
              {lib.outcome === "refused" && lib.detail.length > 0 && <pre>{lib.detail.join("\n")}</pre>}
            </li>
          ))}
        </ul>
      )}
      <div className="ac">
        <button type="button" onClick={onClose}>{summary && !problem ? "Done" : "Cancel"}</button>
        <button type="submit" className="go" disabled={busy}>{busy ? "Publishing…" : "Publish"}</button>
      </div>
    </form>
  );
}
