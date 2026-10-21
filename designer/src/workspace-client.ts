// APP-TOUR — the SPA's one read of the DEPLOYMENT's environment: which Databricks workspace this
// App belongs to, so the product can link to objects that live there (the published example notebooks,
// SVC-EXAMPLE-NOTEBOOKS). A Databricks App is served from `*.databricksapps.com` — a different origin
// from the workspace — so the browser cannot work this out and the BFF has to say it.

/** The workspace's base URL, e.g. `https://acme.cloud.databricks.com` (no trailing slash).
 *  Rejects when the deployment has no workspace to link against (local dev without a Databricks
 *  profile): the caller states the workspace PATH instead, which is true either way. */
export async function fetchWorkspaceHost(): Promise<string> {
  const res = await fetch("/api/workspace-host");
  if (!res.ok) throw new Error(`workspace host failed: ${res.status}`);
  return ((await res.json()) as { host: string }).host;
}
