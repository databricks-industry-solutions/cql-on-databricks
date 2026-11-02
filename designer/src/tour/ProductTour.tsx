// APP-TOUR — the in-app Product Tour: a vertically-scrolled overlay of the key product features
// styled per APP-DESIGN-SYSTEM. Sections are generated from TOUR_FEATURES (kept updated — add a
// feature there and it appears in the tour). Each feature's "Show me" deep-links into the workspace
// opened on the default example (APP-DEFAULT-EXAMPLE) at the feature's lens/mode.
//
// Opened via the chrome-bar help button (CircleHelp icon) and shown as a first-run overlay.

import { useEffect, useRef, useState } from "react";
import { X } from "../icons.tsx";
import { fetchWorkspaceHost } from "../workspace-client.ts";
import {
  TOUR_FEATURES, workspaceObjectUrl, type ExampleTarget, type TopModuleId,
} from "../measures/model.ts";

export function ProductTour({ onSwitchModule, onOpenExample, onClose }: {
  onSwitchModule: (id: TopModuleId) => void;
  onOpenExample: (t: ExampleTarget) => void;
  onClose?: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // The workspace this App was deployed into, for the features whose destination is a workspace OBJECT
  // rather than a screen (the published notebooks). Null until it answers, and null forever when the
  // deployment has none to name — the feature states its workspace PATH either way, so the card is
  // never empty and nothing here invents a host.
  const [wsHost, setWsHost] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    fetchWorkspaceHost()
      .then((h) => { if (live) setWsHost(h); })
      .catch((e) => console.warn("no workspace to link into:", e));
    return () => { live = false; };
  }, []);

  // Reveal-on-scroll (APP-DESIGN-SYSTEM), reduced-motion aware.
  useEffect(() => {
    const root = scrollRef.current;
    if (!root) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const els = Array.from(root.querySelectorAll<HTMLElement>(".mw-tour-reveal"));
    if (reduce) { els.forEach((el) => el.classList.add("in")); return; }
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }, { root, threshold: 0.12 });
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  // Escape closes the overlay.
  useEffect(() => {
    if (!onClose) return;
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onEsc);
    return () => document.removeEventListener("keydown", onEsc);
  }, [onClose]);

  return (
    <div className="mw mw-tour mw-tour-overlay" role="dialog" aria-label="Product tour" aria-modal="true">
      <button className="mw-tour-close" onClick={onClose} aria-label="Close tour"><X size={20} /></button>
      <div className="mw-tour-scroll" ref={scrollRef}>
        <header className="mw-tour-mast">
          <div className="mw-tour-page">
            <span className="mw-tour-wordmark">CQL on Databricks<span className="dot">.</span></span>
            <span className="mw-tour-tag">Product tour</span>
            <span className="mw-tour-spacer" />
            {/* The throughline, in the page's most prominent micro-slot. Not a pulsing "Glass-box"
                chip: a live dot beside a word that names our design stance rather than the reader's
                outcome — and "glass-box" is a metaphor that has to be explained before it says anything.
                "for everyone" rather than "for the rest of us", which reads as rebellion against an
                establishment; the claim here is reach, not insurgency. */}
            <span className="mw-tour-tagline">CQL for everyone</span>
          </div>
        </header>

        <main className="mw-tour-page">
          {/* hero */}
          <section className="mw-tour-hero">
            {/* Not the masthead's line repeated — the two sit within a hundred pixels of each other, and an
                echo reads as a mistake. The chip is the throughline; this names the subject. */}
            <div className="mw-tour-kicker"><span className="eyebrow">Computable clinical knowledge</span><span className="r" /></div>
            <h1>The world runs on Spark. <em>Now so does CQL.</em></h1>
            <p className="dek">Clinical quality logic has lived in specialized engines: few people can read it,
              fewer can run it, and every integration is built from scratch. CQL was meant to be computable
              clinical knowledge — CQL on Databricks makes it exactly that, compiled to run natively on Spark, on the platform,
              the governance and the skills your organization already has. The measure you read is the measure
              that runs. Scroll on — where a section has a <b>Show me</b>, it opens the bundled measure, no setup
              and no data connection required.</p>
            <div className="mw-tour-hero-cta">
              <button className="mw-tour-primary" onClick={() => onOpenExample({ lens: "define", mode: "static" })}>Open the example →</button>
              {onClose && <button className="mw-tour-ghost" onClick={onClose}>Skip the tour</button>}
            </div>
          </section>

          {/* features */}
          {TOUR_FEATURES.map((f) => (
            <section className="mw-tour-feature mw-tour-reveal" key={f.id}>
              <div className="mw-tour-feature-head">
                <span className="eyebrow muted">{f.eyebrow}</span>
                <h2>{f.title}</h2>
                <p className="dek">{f.dek}</p>
                {f.applications && (
                  <ul className="mw-tour-apps">
                    {f.applications.map((a) => <li key={a.title}><b>{a.title}</b> {a.body}</li>)}
                  </ul>
                )}
                {f.chips && <div className="mw-tour-chips">{f.chips.map((c) => <span className="mw-tour-chip" key={c}>{c}</span>)}</div>}
                {/* ONE AFFORDANCE PER SECTION. The workspace path rides the button's `title`, not a grey mono
                    line above it: a muted address beside an accent button would give this section two things to
                    look at where every other section offers one. The line renders ONLY when there is no button to
                    carry it: a deployment
                    whose host does not resolve still says where the notebooks are. */}
                {f.workspacePath && !wsHost && <p className="mw-tour-path">{f.workspacePath}</p>}
                {/* A SECTION WITH NOWHERE TO GO CARRIES NO ACTION AT ALL — not a disabled one, not a
                    "coming soon" one. Half the pillars (sharing, governance, the knowledge ontology, the
                    analytical surface) are where the product is going, or a property of how it runs,
                    rather than a screen you can open today — and a dead button is the exact defect
                    APP-TOUR was already
                    fixed for once. The row is not rendered either, so the section closes on its chips. */}
                {(f.example || f.module || (f.workspacePath && wsHost)) && (
                  <div className="mw-tour-actions">
                    {f.example ? (
                      <button className="mw-tour-primary" onClick={() => onOpenExample(f.example!)}>Show me →</button>
                    ) : f.module ? (
                      <button className="mw-tour-primary" onClick={() => onSwitchModule(f.module!)}>Take me there →</button>
                    ) : (
                      <a className="mw-tour-primary" href={workspaceObjectUrl(wsHost!, f.workspacePath!)}
                         title={f.workspacePath} target="_blank" rel="noreferrer noopener">Open the notebooks →</a>
                    )}
                  </div>
                )}
              </div>
              <div className="mw-tour-feature-art" aria-hidden="true">
                <TourGlyph id={f.id} />
              </div>
            </section>
          ))}

          <footer className="mw-tour-foot">
            <span>CQL on Databricks — clinical knowledge that computes</span>
            <span className="mw-tour-spacer" />
            <button className="mw-tour-ghost" onClick={onClose}>Close</button>
          </footer>
        </main>
      </div>
    </div>
  );
}

/** The pillar's mark. `g-<id>` is LOAD-BEARING: `tour.css` draws a different composition per pillar
 *  from that class — a funnel narrowing to one member for trust, one definition with three consumers
 *  for the ontology — so adding a section without adding its rule leaves an empty stage rather than
 *  a wrong picture.
 *
 *  These are deliberately NOT the module switcher's icons. Most pillars have no module at all,
 *  and a switcher mark names somewhere you can go, which those five are not (`APP-TOUR`). Tokens
 *  only, no external assets (Artifact CSP parity); `aria-hidden` upstream, because the substance is
 *  the copy. */
function TourGlyph({ id }: { id: string }) {
  return <div className={"mw-tour-glyph g-" + id}><span /></div>;
}
