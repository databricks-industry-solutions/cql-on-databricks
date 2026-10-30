// APP-ERROR-DISMISS — the one error banner the workspace shows, so every failure it reports can be closed.
//
// The close button is not optional: a banner the reader cannot close sits over the document until the next
// success, which for a read that keeps failing is never. The caller decides what closing means (clear the
// error, or remember which failure was closed); this only reports the press.

import { useState } from "react";
import { ChevronDown, ChevronUp, X } from "../icons.tsx";

export function ErrorBanner({ message, detail, collapsible = false, onDismiss }: {
  message: string;
  /** Text under the message: diagnostics, or a full error whose first line is the message. */
  detail?: string | null;
  /** Hide the detail behind View Details, rather than showing it outright. */
  collapsible?: boolean;
  onDismiss: () => void;
}) {
  const [open, setOpen] = useState(false);
  const shown = !!detail && (!collapsible || open);
  return (
    <div className="mw-error" role="alert">
      <div className="mw-error-summary">
        <span className="mw-error-msg">{message}</span>
        {collapsible && detail && (
          <button className="mw-error-toggle" onClick={() => setOpen((o) => !o)}>
            {open ? "Hide" : "View Details"}{" "}
            {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
        )}
        <button onClick={onDismiss} aria-label="Dismiss"><X size={14} /></button>
      </div>
      {shown && <pre className="mw-error-detail">{detail}</pre>}
    </div>
  );
}
