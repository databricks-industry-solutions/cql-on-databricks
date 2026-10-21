// PHASE-S1 / APP-SHELL — React SPA root.
// APP-URL-ROUTING — BrowserRouter enables URL-driven navigation so refresh preserves state.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Root } from "./Root.tsx";
import { installRevisionRefresh } from "./data-cache.ts";
import "./theme.css";
import "./measures/measures.css";
import "./tour/tour.css";

// PERF-CLIENT-REVISION — re-check the server's revision when the tab becomes visible again, which is
// the one moment the revision header cannot cover (returning to a tab issues no request). Installed
// HERE because the data cache is app-lifetime: a component's mount is not, and installing per mount
// would add and drop the listener on every navigation.
installRevisionRefresh();

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");
createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <Root />
    </BrowserRouter>
  </StrictMode>,
);
