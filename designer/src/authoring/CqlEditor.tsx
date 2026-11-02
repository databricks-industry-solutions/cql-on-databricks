// DOC-CQL-EDIT — the library's CQL, editable, in Monaco with the CQL grammar the read-only highlighter shares.
//
// MONACO IS BUNDLED, NOT FETCHED. `@monaco-editor/react` loads Monaco from a CDN unless it is handed an instance;
// a Databricks App serves the page from its own origin, and a clinical tool whose editor depends on a third-party
// CDN being reachable from the reader's network is an editor that sometimes does not appear. So the loader is
// configured with the `monaco-editor` package, and its one worker (the editor's own; CQL has no language worker)
// is built by Vite.
//
// The editor owns no state. The workspace holds the draft, compiles it, and saves it; this reports each change.

import { useRef } from "react";
import Editor, { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import { cqlLanguageId, cqlMonarchLanguage } from "../../packages/cql-grammar/src/cql.monarch.ts";

let configured = false;

function configure(): void {
  if (configured) return;
  configured = true;
  (self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = { getWorker: () => new EditorWorker() };
  loader.config({ monaco });
  monaco.languages.register({ id: cqlLanguageId });
  monaco.languages.setMonarchTokensProvider(cqlLanguageId,
    cqlMonarchLanguage as unknown as monaco.languages.IMonarchLanguage);
}

/** An inline editor's line height, fixed so its height can be sized to the text it holds. */
const INLINE_LINE = 19;
/** The tallest an inline editor grows, in lines, before it scrolls. */
const INLINE_MAX_LINES = 30;

export function CqlEditor({ value, onChange, readOnly = false, theme, inline, onEscape }: {
  value: string;
  onChange: (next: string) => void;
  readOnly?: boolean;
  /** The app's resolved appearance, so the editor matches the page. */
  theme: "light" | "dark";
  /** DOC-CQL-EDIT — one define edited in place: sized to its text, numbered by the library's own lines
   *  (`firstLine` is the line the text starts on), and focused on open. */
  inline?: { firstLine: number };
  /** Escape, when no suggestion or find widget is open to take it first. */
  onEscape?: () => void;
}) {
  configure();
  const escape = useRef(onEscape);
  escape.current = onEscape;
  const lines = value.split("\n").length;
  const height = inline ? Math.min(lines, INLINE_MAX_LINES) * INLINE_LINE + 12 : "100%";
  const firstLine = inline?.firstLine ?? 1;
  // An inline editor is UNCONTROLLED: it starts from `value` and only reports changes. Controlled, each
  // keystroke re-renders the whole document around it, and Monaco's sync then compares the render's value,
  // already one keystroke old, with its model and writes it back, dropping every other character typed.
  // It mounts afresh for each define opened, so it never needs a new value from outside.
  const text = inline ? { defaultValue: value } : { value };
  return (
    <Editor height={height} language={cqlLanguageId} {...text} theme={theme === "dark" ? "vs-dark" : "vs"}
      onChange={(next) => onChange(next ?? "")}
      onMount={(editor) => {
        if (inline) editor.focus();
        // Bound only when asked for: a bound Escape overrides Monaco's own use of Escape (multi-cursor, selections).
        if (escape.current) editor.addCommand(monaco.KeyCode.Escape, () => escape.current?.(),
          "!suggestWidgetVisible && !findWidgetVisible && !parameterHintsVisible");
      }}
      options={{ readOnly, minimap: { enabled: false }, fontSize: 13, wordWrap: "on", scrollBeyondLastLine: false,
                 automaticLayout: true, tabSize: 2,
                 ...(inline ? {
                   lineHeight: INLINE_LINE, padding: { top: 6, bottom: 6 }, overviewRulerLanes: 0,
                   lineNumbers: (n: number) => String(n + firstLine - 1),
                   scrollbar: { alwaysConsumeMouseWheel: false },
                 } : {}) }} />
  );
}
