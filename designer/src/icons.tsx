// APP-DESIGN-SYSTEM — icons come from lucide-react (the standard React icon set): crisp SVGs with a
// consistent stroke and a single `size` prop. Never Unicode glyphs, never inline ad-hoc SVG.
// Re-exported here under semantic names so call sites are stable and the icon choice lives in one place.


export {
  Play,
  Copy,
  Maximize2 as Focus,
  Sparkles as Sparkle,
  // SPEC-EXPLAIN-VIEW — the Explain view's two faces. ONE SHAPE, TWO TREATMENTS: a speech bubble is
  // commentary — someone talking about the logic — and a DASHED bubble is that same commentary withdrawn.
  // This is not the two-glyphs-for-one-control mistake `PanelWidth` avoids below: those would have been
  // two different pictures a reader has to tell apart, where these are one picture drawn solid or dotted,
  // which reads as a state without being learned. Each names the state it IS in, not the one a press moves
  // to — the rule the appearance cycle's three faces already follow.
  MessageCircle as Explain,
  MessageCircleDashed as ExplainOff,
  Trash2 as Trash,
  X,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Plus,
  // APP-VALUE-READABLE — `Plus`'s other half, for a row that expands in place. The pair is the
  // expand/collapse idiom a table row uses; a chevron would be the tree idiom, and these rows are not a
  // tree. Never an × — that means dismiss, and collapsing a value does not discard it.
  Minus,
  Search,
  Filter,
  Check,
  ArrowRight,
  ArrowUp,
  ArrowDown,
  // SPEC-REF-LENS — the reference lens's two verbs. `ArrowUpRight` is the standard "go there" glyph, so
  // Open reads as leaving; `Pin` is literal — the lens's subject is pinned to the inspector pane.
  ArrowUpRight as OpenRef,
  Pin,
  AlertTriangle,
  List,
  Users,
  FileText,
  Database,
  Contrast,
  Braces,
  Wrench,
  // ── chrome, menus, module glyphs ──
  Share2,
  ArrowLeftRight,
  Clock,
  ShieldCheck,
  Download,
  Upload,
  Globe,
  Library,
  Zap,
  // ── APP-WORKBENCH — the right pane's tab glyphs + the artifact/pin mark ──
  GitBranch,
  Package,
  // `Command` and `CornerDownLeft` are GONE with the ⌘K palette they belonged to
  // (DESIGNER-COMMAND-PALETTE, retired 2026-08-07) — nothing else drew either.
  // ── SPEC-NAV-COMPOSITION — the nav's tabs, and its "expanded above" reference mark. `Waypoints` is
  // the Composition tab's glyph: a spine with branches off it, which is the shape the tree draws.
  Compass,
  CornerUpLeft,
  ListTree,
  Waypoints,
  // ── APP-INSPECTOR — the right pane's data views. Each names WHAT IT SHOWS rather than an idea
  // about it: rows of returned values, a demographic cut, a code table, a spread over time.
  TableProperties,
  ChartColumn,
  Hash,
  CalendarRange,
  // ── APP-PANE-STATE — the side panes' size control. ONE glyph for all three states, rotated by
  // CSS: two icons that both mean "resize this pane" make a reader work out which is which every time.
  PanelLeft as PanelWidth,
} from "lucide-react";

// ── module icon map (replaces Unicode glyphs in the module switcher + placeholders) ──

import {
  CircleHelp,
  // DESIGNER-TOP-BAR — the appearance button's three faces. Each names the STATE it is in, not the one a
  // press would move to: a sun is light, a moon is dark, and SYSTEM IS `SunMoon` — the two of them in one
  // mark, which is exactly what "follow the OS" means here: whichever of the other two faces this machine
  // is on. Building the third glyph out of the other two is what makes the cycle legible as a cycle.
  // NOT `Monitor`: on a control whose faces are pictures of LIGHT LEVELS, a monitor changes subject
  // by being a picture of a DEVICE, and it reads "display" before "appearance".
  Sun, Moon, SunMoon,
} from "lucide-react";

export { CircleHelp, Sun, Moon, SunMoon };

