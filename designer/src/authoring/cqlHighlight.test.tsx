// APP-GRAMMAR — static CQL highlighting for the Define lens's code disclosures.
//
// What matters here is precedence, not colour. A tokenizer that lets a keyword inside a string or a
// quoted define name win is worse than none: it tells the reader the logic turns somewhere it does
// not.

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CqlCode, highlightCql } from "./cqlHighlight.tsx";

/** The class applied to each token, in order — the assertion that reads like the grammar. */
function classes(source: string): [string, string][] {
  const { container } = render(<CqlCode source={source} />);
  return [...container.querySelectorAll("span")].map((el) => [el.className, el.textContent ?? ""]);
}

describe("highlightCql (APP-GRAMMAR)", () => {
  it("takes its keywords from the grammar the editor uses", () => {
    // One vocabulary: `packages/cql-grammar` is registered with Monaco and imported here, so a
    // keyword cannot be a keyword in the editor and an identifier in the document.
    expect(classes("exists X").filter(([c]) => c === "cql-kw").map(([, t]) => t)).toEqual(["exists"]);
  });

  it("matches keywords case-insensitively, as CQL requires", () => {
    expect(classes("Exists").map(([c]) => c)).toEqual(["cql-kw"]);
  });

  it("never colours a keyword inside a quoted identifier", () => {
    // `define "Not Done"` — `Not` is a keyword everywhere except in a name, and colouring it there
    // would suggest a negation the logic does not contain.
    const out = classes('define "Not Done":');
    expect(out).toEqual([["cql-kw", "define"], ["cql-qid", '"Not Done"']]);
  });

  it("never colours a keyword inside a string or a comment", () => {
    expect(classes("'exists'")).toEqual([["cql-str", "'exists'"]]);
    expect(classes("// exists and not")).toEqual([["cql-com", "// exists and not"]]);
  });

  it("closes an unterminated block comment at end of input", () => {
    // A source slice cut at a locator can end mid-comment; the lexer must not drop the rest.
    expect(classes("/* open")).toEqual([["cql-com", "/* open"]]);
  });

  it("leaves identifiers uncoloured so keywords have something to stand out against", () => {
    expect(classes("ExampleCondition").length).toBe(0);
  });

  it("colours quantities and dates as literals", () => {
    expect(classes("< 100 'mm[Hg]'").map(([c]) => c)).toEqual(["cql-op", "cql-num", "cql-str"]);
    expect(classes("@2026-01-01").map(([c]) => c)).toEqual(["cql-num"]);
  });

  it("preserves the source exactly", () => {
    // Highlighting must be lossless: this is the code the reader is checking the prose against.
    const src = 'define "X":\n  exists "Y" and not "Z" // why\n';
    const { container } = render(<CqlCode source={src} />);
    expect(container.textContent).toBe(src);
    expect(highlightCql(src).length).toBeGreaterThan(1);
  });
});

describe("linked references (SPEC-CQL-LINKS)", () => {
  const link = (name: string) =>
    name === "Known" ? <button data-testid="link">{name}</button> : null;

  it("makes a resolvable quoted name followable", () => {
    const { getByTestId } = render(<CqlCode source={'exists "Known"'} link={link} />);
    expect(getByTestId("link").textContent).toBe("Known");
  });

  it("leaves an unresolved name as plain syntax rather than a link to nowhere", () => {
    const { container } = render(<CqlCode source={'exists "Unknown"'} link={link} />);
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector(".cql-qid")?.textContent).toBe('"Unknown"');
  });

  it("never links inside a string or a comment", () => {
    // The linker only ever sees quoted IDENTIFIERS; a name mentioned in prose or in a string is not
    // a reference and must not become one.
    const { container } = render(<CqlCode source={"// see \"Known\"\n'\"Known\"'"} link={link} />);
    expect(container.querySelector("button")).toBeNull();
  });
});

describe("cited lines (SPEC-NARRATIVE-DEVICES)", () => {
  const src = 'define "X":\n  exists "A"\n    and exists "B"\n';

  it("marks exactly the lines the claim cites", () => {
    const { container } = render(<CqlCode source={src} highlight="2" />);
    const cited = [...container.querySelectorAll(".cql-cited")].map((el) => el.textContent).join("");
    expect(cited).toContain('exists "A"');
    expect(cited).not.toContain('exists "B"');
  });

  it("marks a range inclusively at both ends", () => {
    const { container } = render(<CqlCode source={src} highlight="2-3" />);
    const cited = [...container.querySelectorAll(".cql-cited")].map((el) => el.textContent).join("");
    expect(cited).toContain('exists "A"');
    expect(cited).toContain('exists "B"');
  });

  it("keeps a multi-line block comment intact while marking", () => {
    // The reason marking is by character offset and not by splitting the source into lines: a
    // per-line lexer reopens a block comment on every line it spans.
    const withComment = '/* a\n   b */\ndefine "X": true';
    const { container } = render(<CqlCode source={withComment} highlight="3" />);
    const comments = [...container.querySelectorAll(".cql-com")];
    expect(comments).toHaveLength(1);
    expect(comments[0]!.textContent).toBe("/* a\n   b */");
  });

  it("highlights nothing for a malformed or out-of-range span", () => {
    for (const span of ["nonsense", "0", "99", "5-2"]) {
      const { container } = render(<CqlCode source={src} highlight={span} />);
      expect(container.querySelectorAll(".cql-cited")).toHaveLength(0);
    }
  });

  it("preserves the source exactly while marking", () => {
    const { container } = render(<CqlCode source={src} highlight="2-3" />);
    expect(container.textContent).toBe(src);
  });
});
