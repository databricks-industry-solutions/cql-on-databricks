// PHASE-S1 / APP-HOME, APP-BROWSE — the Definitions catalog shape.
//
// UC-backed (KNOWLEDGE-LAKE-DIRECT): the SPA fetches definitions from /api/libraries/discover (which
// queries UC Knowledge Source tables directly) and maps them to this shape.

/** A card category / pill-filter bucket (APP-BROWSE). */
export type Category = "ecqm" | "lib";
/** Publication state of a definition. */
export type Status = "pub" | "draft" | "lic";

export interface Definition {
  id: string;
  name: string;
  /** Human-readable title (e.g. "Example Measure"); absent for shared libraries. */
  title?: string;
  /** Source line, e.g. "eCQM · v1.0.0" or "Shared library · v4.0.1". */
  src: string;
  cat: Category;
  status: Status;
  /** Headline stat shown on the card / detail (a rate, "Ready to run", "14 measures"). */
  stat: string;
  /** Unit/label for a numeric stat; empty when the stat is not a rate. */
  statk: string;
  /** True when this belongs to the signed-in user's workspace (the "Mine" filter). */
  mine: boolean;
  meta: string;
  desc: string;
  /** Four-digit measurement/publication year, when known (e.g. "2026"). */
  year?: string;
  /** The CQL source, when the definition is registry-backed (REG-*). */
  source?: string;
}

/** The label shown for a publication status. */
export const STATUS_LABEL: Record<Status, string> = { pub: "Published", draft: "Draft", lic: "Licensed" };

/** True for definitions whose headline stat is qualitative (draft/licensed/library), not a rate. */
export function isQualitativeStat(d: Definition): boolean {
  return d.status === "draft" || d.status === "lic" || d.cat === "lib";
}

/** Community feedback shown on a measure's operational detail (APP-COMMUNITY). */
export interface Comment {
  who: string;
  text: string;
  up: number;
  down: number;
  when: string;
}
export interface Community {
  rating: number;
  ratings: number;
  comments: Comment[];
  installs: number;
}
export const COMMUNITY: Community = {
  rating: 4.6,
  ratings: 28,
  installs: 41,
  comments: [
    { who: "Dr. A. Nkemelu · Quality Lead", text: "Works out of the box against our FHIR mapping. Watch the HPV value set — we had to map ours.", up: 9, down: 0, when: "2 weeks ago" },
    { who: "M. Petrov · Informaticist", text: "Denominator exclusions matched our chart review exactly.", up: 5, down: 1, when: "1 month ago" },
  ],
};
