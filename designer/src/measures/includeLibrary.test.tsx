/** DOC-INCLUDE-NEW — the new library's `include` goes after the last `include`, else after the `using` lines, at the
 *  positions the compiler's locators give. Every fixture is synthetic. */
import { describe, expect, it } from "vitest";

import { includeLine, withInclude } from "./includeLibrary.ts";

const elm = (usings: object[], includes: object[] = []) => JSON.stringify({ library: {
  usings: { def: [{ localIdentifier: "System", uri: "urn:hl7-org:elm-types:r1" }, ...usings] },
  ...(includes.length ? { includes: { def: includes } } : {}) } });

const SOURCE = [
  "library Screening version '0.1.0'",
  "",
  "using QICore version '6.0.0'",
  "",
  "include FHIRHelpers version '4.0.1' called FHIRHelpers",
  "include Common version '1.0.0'",
  "  called Common",
  "",
  "context Patient",
  "",
].join("\n");

describe("withInclude", () => {
  it("adds the include after the last include, by where the compiler says it ends", () => {
    const out = withInclude(SOURCE, elm([{ locator: "3:1-3:28", localIdentifier: "QICore" }], [
      { locator: "5:1-5:54", localIdentifier: "FHIRHelpers", path: "FHIRHelpers" },
      { locator: "6:1-7:16", localIdentifier: "Common", path: "Common" }]), "Shared", "0.1.0");
    expect(out).toEqual({ cql: SOURCE.replace("  called Common\n", "  called Common\ninclude Shared version '0.1.0' called Shared\n") });
  });

  it("adds it after the using lines when there is no include", () => {
    const source = "library Screening version '0.1.0'\n\nusing QICore version '6.0.0'\n\ncontext Patient\n";
    const out = withInclude(source, elm([{ locator: "3:1-3:28", localIdentifier: "QICore" }]), "Shared", "0.1.0");
    expect(out).toEqual({ cql: "library Screening version '0.1.0'\n\nusing QICore version '6.0.0'\n"
                              + "include Shared version '0.1.0' called Shared\n\ncontext Patient\n" });
  });

  it("refuses a library already included, and one the compiler gave no position for", () => {
    const included = elm([{ locator: "3:1-3:28" }], [{ locator: "5:1-5:54", localIdentifier: "Shared", path: "Shared" }]);
    expect(withInclude(SOURCE, included, "Shared", "0.1.0")).toEqual({ refused: "This library already includes Shared." });
    expect(withInclude(SOURCE, elm([]), "Shared", "0.1.0"))
      .toEqual({ refused: "The compiler gave no position for this library's include or using lines." });
  });

  it("names the library by itself", () => {
    expect(includeLine("Shared", "0.1.0")).toBe("include Shared version '0.1.0' called Shared");
  });
});
