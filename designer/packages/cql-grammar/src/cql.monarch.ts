// PHASE-S0 / APP-GRAMMAR (designer/README.md).
//
// A Monaco Monarch tokenizer for CQL — client-side syntax highlighting only (keystroke-live, no
// round-trip). This is deliberately NOT a parser: real diagnostics/validation come from the
// sidecar's `translate` op (APP-DIAGNOSTICS). Seeded here so the S1 editor has highlighting on
// day one; the keyword set is widened as the editor matures.
//
// Typed loosely (no Monaco dependency in S0). Shape matches monaco.languages.IMonarchLanguage.

export const cqlLanguageId = "cql";

export const cqlMonarchLanguage: Record<string, unknown> = {
  defaultToken: "",
  ignoreCase: true,

  keywords: [
    "library", "using", "include", "called", "version", "public", "private",
    "define", "context", "function", "parameter", "valueset", "codesystem",
    "code", "concept", "return", "sort", "by", "asc", "ascending", "desc", "descending",
    "where", "with", "without", "such", "that", "from", "let", "aggregate", "starting",
    "if", "then", "else", "case", "when", "exists", "not", "and", "or", "xor", "implies",
    "in", "contains", "properly", "during", "includes", "before", "after", "meets",
    "overlaps", "starts", "ends", "within", "same", "as", "cast", "is", "null", "true",
    "false", "distinct", "flatten", "expand", "collapse", "union", "intersect", "except",
    "singleton", "convert", "to", "interval", "list", "tuple", "day", "days", "month",
    "months", "year", "years", "hour", "hours", "minute", "minutes", "second", "seconds",
    "week", "weeks", "of", "all", "between", "duration", "difference",
  ],

  operators: ["<", "<=", ">", ">=", "=", "!=", "~", "!~", "+", "-", "*", "/", "^", "&"],

  tokenizer: {
    root: [
      [/\/\/.*$/, "comment"],
      [/\/\*/, "comment", "@comment"],
      [/"([^"\\]|\\.)*"/, "identifier.quoted"],
      [/'([^'\\]|\\.)*'/, "string"],
      [/`([^`\\]|\\.)*`/, "identifier.quoted"],
      [/@\d{4}(-\d{2}(-\d{2}(T[\d:.]*)?)?)?/, "number.date"],
      [/\d+(\.\d+)?/, "number"],
      [
        /[a-zA-Z_][\w]*/,
        { cases: { "@keywords": "keyword", "@default": "identifier" } },
      ],
      [/[<>=!~+\-*/^&]+/, { cases: { "@operators": "operator", "@default": "" } }],
      [/[{}()[\]]/, "@brackets"],
    ],
    comment: [
      [/[^/*]+/, "comment"],
      [/\*\//, "comment", "@pop"],
      [/[/*]/, "comment"],
    ],
  },
};
