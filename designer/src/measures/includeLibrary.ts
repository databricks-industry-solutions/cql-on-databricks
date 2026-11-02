/** DOC-INCLUDE-NEW — where "Include a library…" puts the new library's `include` in the open library's CQL.
 *
 *  The line goes after the library's last `include`, or after its `using` lines when it has none. Where those are
 *  is the compiler's answer, not a scan of the text: each `using` and `include` in the ELM carries the `locator` of
 *  its declaration (`specModel.ts` `locatorSpan` reads the same). So the CQL must be the text that ELM was compiled
 *  from; the caller passes only a compile of exactly this source. */

/** The `include` line for `name` `version`, called by its own name. */
export function includeLine(name: string, version: string): string {
  return `include ${name} version '${version}' called ${name}`;
}

/** `source` with an `include` of `name` `version` added, or why it cannot be. `elm` is `source`, compiled. */
export function withInclude(source: string, elm: string, name: string, version: string):
    { cql: string } | { refused: string } {
  let library: { usings?: { def?: Decl[] }; includes?: { def?: Decl[] } };
  try {
    library = (JSON.parse(elm) as { library: typeof library }).library;
  } catch {
    return { refused: "The compiled ELM could not be read." };
  }
  const includes = library.includes?.def ?? [];
  if (includes.some((i) => i.path === name || i.localIdentifier === name)) {
    return { refused: `This library already includes ${name}.` };
  }
  // The System model has no declaration, so no locator: only `using` lines the author wrote count.
  const after = lastLine(includes) ?? lastLine(library.usings?.def ?? []);
  if (after === null) {
    return { refused: "The compiler gave no position for this library's include or using lines." };
  }
  const lines = source.split("\n");
  if (after > lines.length) return { refused: "The compiled ELM does not match this CQL." };
  lines.splice(after, 0, includeLine(name, version));
  return { cql: lines.join("\n") };
}

interface Decl { locator?: string; path?: string; localIdentifier?: string }

/** The last 1-based line any of `decls` ends on, or null when none carries a locator. */
function lastLine(decls: Decl[]): number | null {
  let last: number | null = null;
  for (const d of decls) {
    const end = d.locator?.split("-")[1]?.split(":")[0];
    const line = end === undefined ? NaN : Number(end);
    if (Number.isFinite(line) && (last === null || line > last)) last = line;
  }
  return last;
}
