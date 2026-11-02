// DOC-ENVIRONMENT-PICKER — the environment the Designer runs in: the one picked in the compute pill, or the
// default until one is picked. Every knowledge read, compile, save, publish, run and assistant question names it,
// so the server reads that environment's content (`CLIENT-ENVIRONMENT`). Held here, outside any component, because
// the readers of knowledge are many and the picker is one; persisted so a reload reads the same environment.

const STORAGE_KEY = "cql-databricks:environment";

function stored(): string | null {
  try { return localStorage.getItem(STORAGE_KEY); } catch { return null; }
}

let picked: string | null = stored();

/** The picked environment's name; null is the default (`CLIENT-ENVIRONMENT`). */
export function currentEnvironment(): string | null {
  return picked;
}

/** DOC-ENVIRONMENT-PICKER — pick `name`; null is the default. */
export function setCurrentEnvironment(name: string | null): void {
  picked = name;
  try {
    if (name === null) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, name);
  } catch { /* quota / private mode */ }
}

/** `url` with the picked environment as its `environment` query parameter; unchanged for the default. */
export function withEnvironment(url: string): string {
  if (picked === null) return url;
  return `${url}${url.includes("?") ? "&" : "?"}environment=${encodeURIComponent(picked)}`;
}
