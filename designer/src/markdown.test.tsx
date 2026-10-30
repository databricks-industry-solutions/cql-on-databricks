// SPEC-MEASURE-METADATA — the renderer that lets CQL on Databricks quote a steward verbatim.
//
// The claim under test is that quoting is FAITHFUL. A renderer that silently drops a paragraph break,
// swallows a bracket or turns a citation into plain text has edited someone else's published prose,
// which is the failure the whole quote-don't-paraphrase decision exists to avoid.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { blocks, Markdown, paragraphs } from "./markdown.tsx";

describe("Markdown (SPEC-MEASURE-METADATA)", () => {
  it("keeps the author's paragraphs and collapses only their line wrapping", () => {
    // A blank line is a paragraph the author wrote; a single newline is how the source file was
    // wrapped, which is a property of the file and not of the sentence.
    expect(paragraphs("Example reading\nis important.\n\nMonitoring it helps."))
      .toEqual(["Example reading is important.", "Monitoring it helps."]);
  });

  it("renders a citation as a link that leaves the document open", () => {
    render(<Markdown text="See the [2017 Guideline](https://example.org/g) for detail." />);
    const link = screen.getByRole("link", { name: "2017 Guideline" });
    expect(link).toHaveAttribute("href", "https://example.org/g");
    // A reader following a citation must not lose the measure they were reviewing.
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("refuses a non-http scheme and shows the author's characters instead", () => {
    // Markup-free rendering still leaves `href` as a way to execute something. No legitimate citation
    // needs another scheme, so the text renders literally rather than becoming a link.
    render(<Markdown text="[click](javascript:alert(1))" />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("[click](javascript:alert(1))")).toBeTruthy();
  });

  it("leaves unsupported markdown as the characters the author typed", () => {
    // The supported set is what the two corpora use. Anything else must degrade to plain text —
    // never to a broken tag and never to a swallowed line.
    render(<Markdown text={"# A heading\n\n> a quotation"} />);
    expect(screen.getByText("# A heading")).toBeTruthy();
    expect(screen.getByText("> a quotation")).toBeTruthy();
  });

  it("emphasises what the author emphasised", () => {
    render(<Markdown text="only **eligible** clinicians" />);
    expect(screen.getByText("eligible").tagName).toBe("B");
  });
});

describe("blocks — lists and tables (APP-ASSISTANT-MARKDOWN)", () => {
  // What these pin is the half of the contract the renderer owns: the assistant is TOLD this set
  // (`assistant/ask.py`, RENDERING_INSTRUCTIONS), so a construct advertised there and not parsed here
  // reaches a clinician as punctuation in the middle of a clinical answer.

  it("renders a bulleted list as a list, not as a line of hyphens", () => {
    render(<Markdown text={"It counts:\n\n- patients 20 to 90\n- with an example condition diagnosis"} />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((li) => li.textContent))
      .toEqual(["patients 20 to 90", "with an example condition diagnosis"]);
    expect(items[0]!.closest("ul")).toBeTruthy();
  });

  it("renders a numbered list as an ordered one, so the reader keeps the author's numbering", () => {
    render(<Markdown text={"1. Initial Population\n2. Denominator\n3. Numerator"} />);
    expect(screen.getAllByRole("listitem")[0]!.closest("ol")).toBeTruthy();
  });

  it("keeps one list through the blank lines a model puts between its bullets", () => {
    expect(blocks("- one\n\n- two")).toEqual([{ kind: "ul", items: ["one", "two"] }]);
  });

  it("flattens an indented sub-bullet rather than printing markup at a clinician", () => {
    // Losing one level of nesting is a smaller lie than a line of literal hyphens. The instructions
    // tell the model not to nest; this is what happens when it does anyway.
    expect(blocks("- patients\n  - aged 20 to 90"))
      .toEqual([{ kind: "ul", items: ["patients", "aged 20 to 90"] }]);
  });

  it("renders a pipe table as a table — the shape the drawer was placed to be able to show", () => {
    render(<Markdown text={[
      "| Population | Patients |",
      "| ---------- | -------- |",
      "| Denominator | 11,154 |",
      "| Numerator | 6,802 |",
    ].join("\n")} />);
    expect(screen.getByRole("columnheader", { name: "Population" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "11,154" })).toBeTruthy();
    expect(screen.getAllByRole("row")).toHaveLength(3);
  });

  it("does not turn a sentence containing a pipe into a table", () => {
    // The `---|---` rule is what distinguishes a table from prose. Without it there is no table, and
    // a sentence that mentions a pipe is a sentence.
    const parsed = blocks("The handle is cat.sch.denominator | the fingerprint is 081b9dc0.");
    expect(parsed.every((b) => b.kind === "p")).toBe(true);
  });

  it("renders a cell's markup, and cannot inject through one", () => {
    render(<Markdown text={"| Define | Note |\n| --- | --- |\n| `Denominator` | see [it](https://e.org/d) |"} />);
    expect(screen.getByText("Denominator").tagName).toBe("CODE");
    expect(screen.getByRole("link", { name: "it" })).toHaveAttribute("href", "https://e.org/d");
  });

  it("renders a fenced block verbatim, and stops parsing markdown inside it", () => {
    // The claim of a fence is that these are the exact characters. A `- ` inside a SQL snippet is a
    // minus sign, and a renderer that made it a bullet would be editing code while showing it as code.
    render(<Markdown text={"Run:\n\n```sql\nSELECT a - b\nFROM t\n```"} />);
    const code = document.querySelector("pre code");
    expect(code!.textContent).toBe("SELECT a - b\nFROM t");
    expect(screen.queryByRole("listitem")).toBeNull();
  });

  it("closes an unterminated fence at the end rather than falling back to prose", () => {
    expect(blocks("```\nSELECT 1")).toEqual([{ kind: "code", text: "SELECT 1", lang: "" }]);
  });

  it("carries the fence's info string, which is what names an artifact", () => {
    // APP-ASSISTANT-REFS — to this renderer it is only a label; to the `fence` slot it is the
    // difference between a code sample and a chart.
    expect(blocks("```cql-databricks-chart\n{}\n```"))
      .toEqual([{ kind: "code", text: "{}", lang: "cql-databricks-chart" }]);
  });

  it("keeps prose around a list as its own paragraphs", () => {
    expect(blocks("Two things.\n\n- one\n- two\n\nBoth hold.")).toEqual([
      { kind: "p", text: "Two things." },
      { kind: "ul", items: ["one", "two"] },
      { kind: "p", text: "Both hold." },
    ]);
  });
});

describe("HTML entities (SPEC-MEASURE-METADATA)", () => {
  it("decodes the entities published eCQM prose actually contains", () => {
    // Published guidance writes `&lt;100 mmHg`. React renders that as six literal characters,
    // which turns a clinical threshold into gibberish — the reason this decoding exists.
    render(<Markdown text="reading A &lt;100 mmHg and reading B &lt;60 mmHg" />);
    expect(screen.getByText("reading A <100 mmHg and reading B <60 mmHg")).toBeTruthy();
  });

  it("decodes numeric and hex references, and leaves an unknown name alone", () => {
    render(<Markdown text={"18&#8211;85 &#x2265; &notareal; years"} />);
    expect(screen.getByText("18–85 ≥ &notareal; years")).toBeTruthy();
  });

  it("decodes to TEXT, never to markup", () => {
    // The safety property that makes decoding fine: the result is still a React string.
    render(<Markdown text="&lt;script&gt;alert(1)&lt;/script&gt;" />);
    expect(screen.getByText("<script>alert(1)</script>")).toBeTruthy();
    expect(document.querySelector("script")).toBeNull();
  });
});
