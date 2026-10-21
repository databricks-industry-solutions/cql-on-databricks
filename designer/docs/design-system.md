# Design system

The Designer's tokens, type scale and component rules (`APP-DESIGN-SYSTEM`, `APP-STYLE-GUIDE`). Token values
live in `designer/src/theme.css`. The workspace's components and its `--t*` tokens live in
`designer/src/measures/measures.css`.

## Type

Three font stacks, one per role (`--font-spec`, `--font-ui`, `--font-code` in `theme.css`):

| Role | Stack starts with | Used for |
|---|---|---|
| spec (serif) | `ui-serif, "Iowan Old Style", Palatino` | headlines, titles, large numbers |
| ui (sans) | `"DM Sans", ui-sans-serif, system-ui` | all chrome: body, controls, labels, menus, buttons, tabs |
| code (mono) | `"DM Mono", "JetBrains Mono", ui-monospace` | CQL source, counts, OIDs, versions |

Use mono only for code or machine data. Words read as prose, such as buttons and field labels, are sans.
Uppercase micro-labels may use mono.

### Type scale

| Step | Size | Weight | Use |
|---|---|---|---|
| `display` | 30px | 700 sans / 400 serif | hero and spec titles, the large population number |
| `title` | 21px | 700 / 400 serif | module titles, `h2` |
| `name` | 19px | 600 serif | Specification headings: population, define, resource type |
| `subtitle` | 16px | 600 / 400 serif | `h3`, popover heads, section titles |
| `body` | 14px | 400 | default prose |
| `control` | 13px | 500–600 | buttons, tabs, list rows, menu items |
| `meta` | 12px | 500 | secondary meta, chips, table cells |
| `caption` | 11px | 500–600 | counts, fine print, badges |
| `micro` | 9.5px | 700 mono | eyebrows, section labels, uppercase and letter-spaced |

New styles pick the nearest step. The current CSS also uses some in-between sizes, such as 10px, 11.5px
and 12.5px, and the spec title is 31px. Line height is 1.5 for chrome and about 1.6 for reading prose. Use
`font-variant-numeric: tabular-nums` for numbers (`.tnum`) and `text-wrap: balance` for headings.

## Color

Colors come from the Databricks palette in `theme.css`: `--lava #FF3621`, `--navy #1B3139`,
`--green #00875C`, `--blue #2272B4`, `--maroon #98102A`, plus navy and oat neutrals.

| Role | Light | Dark | Meaning |
|---|---|---|---|
| ground | `#F9F7F4` (oat-light) | navy-900 | app background |
| surface | `#FFFFFF` | navy | card and document surface |
| surface-2 | `#F1F0EC` | navy-700 | hover fill, insets |
| ink | navy `#1B3139` | oat-light | primary text |
| ink-2 | `#5A6F77` | navy-300 | secondary text |
| ink-3 | navy-400 | navy-400 | labels, placeholders |
| line | `#DCE0E2` | oat-light at 14% | borders |
| line-2 | `#EAE9E4` | oat-light at 8% | inner dividers |
| accent (`--tacc`) | lava `#FF3621` | `#FF5F46` | the one bold accent |
| good (`--tgood`) | green `#00875C` | `#42BA91` | numerator, met, pass |
| good-filled (`--tgood-f`) | green `#00875C` | green `#00875C` | solid green fill; does not change in dark |
| info (`--tinfo`) | blue `#2272B4` | `#4299E0` | denominator, informational |
| warn (`--twarn`) | `#7D5319` | `#FCBA33` | exceptions, caution |
| fail (`--tfail`) | maroon `#98102A` | `#BF7080` | exclusions, failure |
| excluded (`--texcl`) | `#5A6F77` | navy-400 | out of population |
| explain (`--texpl-w`) | ink at 3.5% | ink at 3.5% | Explain toggle on-state |

Most roles have a wash token (`-w`, for example `--tgood-w`) for chip and pill backgrounds.

- Spend the accent once per view: the one primary action, or the active nav element.
- Semantic colors encode state, never decoration.
- Use `info` as the second color for calm secondary information, not another accent.

## Dark and light

Both ship. Dark applies to the workspace (`.mw`) only. The OS preference is the default
(`@media (prefers-color-scheme: dark)`), and the theme toggle sets `data-mw-theme="light|dark"` on `.mw`.
Dark uses navy grounds, lighter navy surfaces, and its own semantic hues tuned for contrast.

## Spacing, radius, shadow

- Radius: 6–8px for small controls, 9px for inputs and toolbar controls, 10–12px for cards, 12px for
  popovers, `999px` for pills.
- Shadow: `--tshadow` for cards, `--pshadow` for popovers and menus.
- Spacing: flex or grid `gap`, in steps of 4, 6, 8, 12, 16 and 24px.
- Layout: white panels on the oat ground.
- Selecting or toggling must not shift siblings. Reserve space for the on-state. Don't change weight or
  size.

## Icons

Icons come from `lucide-react`, re-exported under semantic names in `designer/src/icons.tsx`. Don't use
Unicode glyphs or ad-hoc SVG. Add a new icon there.

- Sizes: 14px inline with text, 15–16px standalone, 18–22px feature glyph.
- Dismiss: `X`. Disclosure: `ChevronDown`, rotated when open. Confirmation: `Check`.
- `Sparkle` marks what you can talk to (the Assistant).
- Explain: `Explain` (`MessageCircle`) on, `ExplainOff` (`MessageCircleDashed`) off.

Icon-only buttons get a `--tline` hairline and a 9px radius at rest, and `--tline-2` on hover. The only
fill is the icon itself, in the accent when active.

## Controls

### Buttons

| Variant | Look | When |
|---|---|---|
| primary | accent fill, white text | the one primary action per view |
| secondary | surface fill, hairline border | ordinary actions |
| ghost | no fill until hover | toolbar and chrome, icon-only controls |

Buttons use the `control` step, weight 600 and an 8px radius. Disabled is `opacity: .45`.

### Links

Links use the accent color, underlined on hover, and are for navigation. Links that leave the app are
anchors with `target="_blank" rel="noreferrer noopener"`. Never render an in-app primary action as accent
text. Run and Save are buttons.

### Tabs

- Lens bar: switches views within an artifact. The active tab has an accent underline only, at constant
  weight, so switching doesn't reflow.
- Pane tabs: switch the arrangement within a pane. Same style as the lens bar.

### Toolbar

Toolbar controls are outlined, not raised: no fill at rest, a `--tline` hairline, a 9px radius and a 30px
height. On hover, `--tline-2`. A two-state control that is on uses the `--texpl-w` wash.

- The compute pill shows the execution parameters.
- Parameter steppers step the measurement period, warehouse and data source.
- The Explain toggle shows or hides narrated prose.
- The top bar holds the theme, help and tour toggles.

### Menus and popovers

A titled head (`subtitle`) and rows (optional glyph, name, trailing tag), on the `--panel` surface with
`--pshadow`. Row hover fills with `--panel-2`. The active row uses the accent wash only.

## Building the HTML page

`npm run doc:design` (in `designer/`) builds `designer/docs/design-system.html` from this file and
`design-system.tmpl.html` (`designer/scripts/build-design-doc.mjs`). The output is not gitignored.
