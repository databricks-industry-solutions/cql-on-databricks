// APP-CQL-AUTHORITATIVE — extract the authoritative define catalog from ELM JSON.
//
// The CQL-to-ELM translator (the compiler's /translate endpoint) produces a standard HL7 ELM JSON
// structure containing every define, function, and their metadata (name, context, access level,
// expression type). This is the COMPILER'S truth about what exists in the library — no regex parsing,
// no ambiguity. Both the DefineNav (left nav) and MeasureNarrative (Specification) derive their define
// lists from this.
//
// The HL7 ELM JSON shape (relevant subset):
//   { library: { identifier: { id, version },
//                usings: { def: [{ localIdentifier, uri, version }] },
//                includes: { def: [{ localIdentifier, path, version }] },
//                valueSets: { def: [{ name, id }] },
//                statements: { def: [{ name, context, accessLevel, type?, expression, ... }] } } }
//
// "type": "FunctionDef" distinguishes functions from expression defines (ExpressionDef is the default).

import { POPULATIONS } from "../measures/model.ts";

export interface ElmDefine {
  /** The define name as declared in CQL. */
  name: string;
  /** "define" or "function" — from the ELM statement type. */
  kind: "define" | "function";
  /** CQL context (Patient / Unfiltered / null for context-less defines). */
  context: string | null;
  /** Access level from the ELM (Public / Private). */
  access: string;
  /** True if this is a population define (in POPULATIONS). */
  population: boolean;
  /** APP-SPEC-MODEL — the declared parameter list ("value FHIR.AccountStatus"), from the ELM
   *  operands. Empty for expression defines and for zero-argument functions. This is what
   *  distinguishes one OVERLOAD from another: CQL permits many declarations of the same name
   *  differing only in operand types (FHIRHelpers declares `ToString` once per FHIR type). */
  params: string;
  /** The declared return type, from the ELM result type. Null when the translator did not annotate
   *  types (result-type annotations are optional in ELM output). */
  returns: string | null;
  /** APP-SPEC-MODEL — the statement's source span, "startLine:startCol-endLine:endCol" (1-based,
   *  end inclusive). The ELM carries an expression TREE, not source text; this is how the exact CQL
   *  slice is recovered for display, without parsing the source. Null if the ELM omits the locator. */
  locator: string | null;
}

/** An `include` declaration: the library it names, and the alias the CQL refers to it by. */
export interface ElmInclude {
  /** The declared library name (ELM `path`) — the identity the registry knows it by. */
  path: string;
  /** The alias used in this library's CQL (`called SDE`); equals `path` when no alias was given. */
  localIdentifier: string;
  version: string;
}

/** SPEC-DERIVED-APPENDIX — a `valueset` declaration: the name the CQL binds and the OID it binds to.
 *  The OID is the whole point of listing it, and it is right here in the ELM — hand-copying it into a
 *  narration file was how it went stale. */
export interface ElmValueSetDef {
  name: string;
  /** The canonical url or OID (`http://example.org/fhir/ValueSet/office-visit`, `urn:oid:2.25.1`). */
  id: string | null;
}

/** A direct-reference `code` declaration, with its code system already resolved from the library's
 *  `codeSystems` — the ELM records the system as a reference, and an unresolved reference is not
 *  something a reader can look up. */
export interface ElmCodeDef {
  name: string;
  code: string | null;
  display: string | null;
  /** The code system's declared name ("SNOMEDCT"). */
  system: string | null;
  /** That system's uri, when the library declares one. */
  systemUri: string | null;
}

/** A `parameter` declaration and its declared type. */
export interface ElmParameterDef {
  name: string;
  type: string | null;
}

export interface ElmLibraryInfo {
  /** Library name from the ELM identifier. */
  name: string;
  /** Library version. */
  version: string;
  /** FHIR model version (from `usings`), e.g. "4.0.1". */
  fhirVersion: string | null;
  /** The authoritative define list, in ELM declaration order. */
  defines: ElmDefine[];
  /** Value set names declared in the library. */
  valueSets: string[];
  /** SPEC-DERIVED-APPENDIX — the same declarations with their OIDs, for the Appendix. */
  valueSetDefs: ElmValueSetDef[];
  /** SPEC-DERIVED-APPENDIX — direct reference codes declared in the library. */
  codes: ElmCodeDef[];
  /** SPEC-DERIVED-APPENDIX — parameter declarations. */
  parameters: ElmParameterDef[];
  /** Include declarations, carrying both the real library name and the local alias. */
  includes: ElmInclude[];
}

const POP_SET = new Set<string>(POPULATIONS);

/** Render an ELM type specifier as the type name a reader recognises from the CQL.
 *
 *  ELM qualifies type names with their model namespace — `{http://hl7.org/fhir}AccountStatus` for the
 *  FHIR model, `{urn:hl7-org:elm-types:r1}String` for CQL system types. The CQL that declares them
 *  writes `FHIR.AccountStatus` and `String` respectively, so that is what we render. Structural
 *  specifiers (list, interval, choice) nest their element type the same way CQL writes them. */
function typeName(spec: unknown): string {
  if (typeof spec === "string") return qualifiedName(spec);
  if (typeof spec !== "object" || spec === null) return "";
  const s = spec as Record<string, unknown>;
  switch (s["type"]) {
    case "NamedTypeSpecifier":
      return qualifiedName((s["name"] as string) ?? "");
    case "ListTypeSpecifier":
      return `List<${typeName(s["elementType"])}>`;
    case "IntervalTypeSpecifier":
      return `Interval<${typeName(s["pointType"])}>`;
    case "ChoiceTypeSpecifier":
      return `Choice<${((s["choice"] as unknown[]) ?? []).map(typeName).join(", ")}>`;
    case "TupleTypeSpecifier":
      return "Tuple";
    default:
      return qualifiedName((s["name"] as string) ?? "");
  }
}

/** `{http://hl7.org/fhir}AccountStatus` → `FHIR.AccountStatus`; `{urn:…elm-types…}String` → `String`. */
function qualifiedName(raw: string): string {
  const m = /^\{([^}]*)\}(.*)$/.exec(raw);
  if (!m) return raw;
  const [, ns, local] = m as unknown as [string, string, string];
  return ns.includes("hl7.org/fhir") ? `FHIR.${local}` : local;
}

/** The declared parameter list of a FunctionDef, as CQL writes it: "name Type, name Type". */
function paramList(stmt: Record<string, unknown>): string {
  const operands = (stmt["operand"] as Record<string, unknown>[] | undefined) ?? [];
  return operands
    .map((o) => {
      const t = typeName(o["operandTypeSpecifier"] ?? o["operandType"]);
      const n = (o["name"] as string) ?? "";
      return t ? `${n} ${t}`.trim() : n;
    })
    .filter(Boolean)
    .join(", ");
}

/** SPEC-DERIVED-APPENDIX — a library's reference declarations, read off a PARSED ELM document.
 *
 *  Separate from `parseElmDefines` because the Specification's Appendix is built from the ELM objects
 *  the workspace already holds, not from the JSON string it parsed them out of. One extraction either
 *  way: `parseElmDefines` calls this too, so the value-set names in the define catalog and the
 *  name+OID pairs in the Appendix can never come from two different readings of the same ELM. */
export function elmReferenceData(elm: unknown): {
  valueSets: ElmValueSetDef[]; codes: ElmCodeDef[]; parameters: ElmParameterDef[];
} {
  const lib = (elm as Record<string, unknown> | null)?.["library"];
  const libObj = (typeof lib === "object" && lib !== null ? lib : {}) as Record<string, unknown>;

  const vsDef = (libObj["valueSets"] as Record<string, unknown[]> | undefined)?.["def"] ?? [];
  const valueSets: ElmValueSetDef[] = (vsDef as Record<string, string>[])
    .map((v) => ({ name: v["name"] ?? "", id: v["id"] ?? null }))
    .filter((v) => v.name);

  // Direct reference codes resolve their system through the library's own `codeSystems`: the ELM
  // records `codeSystem: { name }`, and a reference nobody resolved is not something a reader can
  // look up.
  const csDef = (libObj["codeSystems"] as Record<string, unknown[]> | undefined)?.["def"] ?? [];
  const systemUris = new Map<string, string>();
  for (const cs of csDef as Record<string, string>[]) {
    if (cs["name"]) systemUris.set(cs["name"], cs["id"] ?? "");
  }
  const codeDef = (libObj["codes"] as Record<string, unknown[]> | undefined)?.["def"] ?? [];
  const codes: ElmCodeDef[] = (codeDef as Record<string, unknown>[])
    .map((c) => {
      const system = (c["codeSystem"] as Record<string, string> | undefined)?.["name"] ?? null;
      return {
        name: (c["name"] as string) ?? "",
        code: (c["id"] as string) ?? null,
        display: (c["display"] as string) ?? null,
        system,
        systemUri: system ? systemUris.get(system) || null : null,
      };
    })
    .filter((c) => c.name);

  // `parameterTypeSpecifier` is the declaration; the translator's annotated result type says the same
  // thing about a parameter and stands in when the specifier is absent.
  const paramDef = (libObj["parameters"] as Record<string, unknown[]> | undefined)?.["def"] ?? [];
  const parameters: ElmParameterDef[] = (paramDef as Record<string, unknown>[])
    .map((p) => {
      const spec = p["parameterTypeSpecifier"] ?? p["resultTypeSpecifier"] ?? p["resultTypeName"];
      return { name: (p["name"] as string) ?? "", type: spec ? typeName(spec) || null : null };
    })
    .filter((p) => p.name);

  return { valueSets, codes, parameters };
}

/** Parse the ELM JSON string from a /translate result into the authoritative define catalog.
 *  Returns null if the ELM is missing, unparseable, or structurally unexpected. */
export function parseElmDefines(elmJson: string | null | undefined): ElmLibraryInfo | null {
  if (!elmJson) return null;
  let elm: unknown;
  try {
    elm = JSON.parse(elmJson);
  } catch {
    return null;
  }
  if (typeof elm !== "object" || elm === null) return null;
  const lib = (elm as Record<string, unknown>)["library"];
  if (typeof lib !== "object" || lib === null) return null;
  const libObj = lib as Record<string, unknown>;

  // Identifier.
  const id = libObj["identifier"] as Record<string, string> | undefined;
  const name = id?.["id"] ?? "";
  const version = id?.["version"] ?? "";

  // FHIR version from usings.
  let fhirVersion: string | null = null;
  const usings = libObj["usings"] as Record<string, unknown[]> | undefined;
  if (usings?.["def"]) {
    for (const u of usings["def"]) {
      const uObj = u as Record<string, string>;
      if (uObj["localIdentifier"] === "FHIR" || uObj["uri"]?.includes("fhir")) {
        fhirVersion = uObj["version"] ?? null;
      }
    }
  }

  // Includes. `path` is the declared library name (what the registry knows); `localIdentifier` is the
  // alias the CQL uses (`include SharedElements … called SE`). Matching a resolved library
  // to its include requires `path` — the alias alone does not identify it.
  const includesDef = (libObj["includes"] as Record<string, unknown[]> | undefined)?.["def"] ?? [];
  const includes: ElmInclude[] = (includesDef as Record<string, string>[])
    .map((i) => ({
      path: i["path"] ?? i["localIdentifier"] ?? "",
      localIdentifier: i["localIdentifier"] ?? i["path"] ?? "",
      version: i["version"] ?? "",
    }))
    .filter((i) => i.path);

  const { valueSets: valueSetDefs, codes, parameters } = elmReferenceData(elm);
  const valueSets = valueSetDefs.map((v) => v.name);

  // Statements (defines + functions).
  const stmtDef = (libObj["statements"] as Record<string, unknown[]> | undefined)?.["def"] ?? [];
  const defines: ElmDefine[] = [];
  for (const stmt of stmtDef as Record<string, unknown>[]) {
    const stmtName = (stmt["name"] as string) ?? "";
    if (!stmtName) continue;
    // Skip the implicit Patient context define that the translator inserts.
    if (stmtName === "Patient" && (stmt["context"] === "Patient" || !stmt["expression"])) continue;
    const isFn = stmt["type"] === "FunctionDef";
    const resultType = stmt["resultTypeSpecifier"] ?? stmt["resultTypeName"];
    defines.push({
      name: stmtName,
      kind: isFn ? "function" : "define",
      context: (stmt["context"] as string) ?? null,
      access: (stmt["accessLevel"] as string) ?? "Public",
      population: POP_SET.has(stmtName),
      params: isFn ? paramList(stmt) : "",
      returns: resultType ? typeName(resultType) || null : null,
      locator: (stmt["locator"] as string) ?? null,
    });
  }

  return { name, version, fhirVersion, defines, valueSets, valueSetDefs, codes, parameters, includes };
}
