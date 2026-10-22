# Requirements

Requirements for every feature except the compiler, which has its own registry in
[`compiler/requirements.md`](compiler/requirements.md). Each ID is stable and cited from code
(`PROC-CITE-REQ`). A requirement that stops being true moves to [Retired](#retired) with a one-line
reason, so old citations still resolve.

## Sharing

Sharing measures and results across organizations.

| ID | Requirement |
|---|---|
| `SHARE-BULK-FHIR` | Population FHIR data represented as Delta tables and shared over the open protocol survives intact for any recipient to run any measure. |
| `SHARE-CANONICAL-LAYOUT` | Publish the standard FHIR-in-Delta layout and value-set-as-table convention as the canonical contract; recipients on this layout get zero-config data binding. |
| `SHARE-ENLIST` | A data source is a configurable union of value-set views and logical mounts that decouples measures from physical shares; enlistment is explicit and UC-governed. |
| `SHARE-LICENSING` | Value-set licensing is resolved on the recipient side; shared FHIR data is governed by recipient controls and UC masking. |
| `SHARE-MEASURE-PACKAGE` | Cross-organization logic sharing ships the measure package (CQL + ELM + value set references); recipients compile locally against their own data. |
| `SHARE-OPENSHARING` | Share measures and libraries between organizations on Databricks over the open sharing protocol; recipients read them live, without copying artifacts. |
| `SHARE-RESULTS` | Measure results (population membership, measure reports, gap views) share as tables over the open protocol, distinct from logic and data modalities. |
| `SHARE-RUN-ANYWHERE` | A measure package published once runs in any recipient organization on Databricks, against its own data or a shared governed FHIR population, without reimplementation. |

## Product and Designer experience

What the product and the Designer do for a user.

| ID | Requirement |
|---|---|
| `APP-AI-CACHE` | Every AI-generated summary and explanation is cached and reused; edge-case narratives are generated only on explicit request. |
| `APP-ANALYTICS` | Analysis is a module, not a pane, answering what is true about the population and what to do — attribution, co-occurrence, stratified comparisons and trends scoped to the measure. |
| `APP-ASSISTANT` | One natural-language assistant routes three intents — Ask, Edit, Operate — to the right places: Ask as conversational prose, Edit as a document diff, Operate as a filled form. |
| `APP-ASSISTANT-CONTEXT` | The assistant knows the measure, data source, period, and selected define as context that prefills tool arguments and labels; context defaults, never forces. |
| `APP-ASSISTANT-MARKDOWN` | The assistant answers in markdown and is told which markdown; the SPA renders the parsed set (paragraphs, bold, code, links, lists, tables) and degrades unrecognized constructs to plain characters. |
| `APP-ASSISTANT-REFS` | Assistant answers cite measures by `cql-databricks:` links that render as the Specification does — the same controls and inks — so a reader can open what the assistant named. |
| `APP-AUDIENCE` | The primary user is a non-technical clinical user; the experience is low-code/no-code, never an IDE; engineer artifacts remain out of the clinical path. |
| `APP-AUTHOR-TABS` | The authoring workspace is multi-tab — multiple measures and libraries open simultaneously. |
| `APP-AUTHORING-SKILL` | A pluggable, attributed MCP skill capturing CQL authoring guidance — when to use fluent functions, reusable defines, temporal operators, value-set vs. direct-code membership, and population-define conventions. |
| `APP-AUTHORING-VS-RESULTS` | Authoring optimizes for future evaluations; Evaluation and Improvement optimize for actual calculations against real members; each module is specialized for its job. |
| `APP-BROWSE` | One reusable content-marketplace component (cards, pill filters, search) serves both the Measures library and the Authoring "Open" picker. |
| `APP-CARE-GAPS` | A care-gaps view over calculated results lists members who are eligible but not yet met, each with a plain-language reason. It is a set of derived views, not a separate gaps engine. |
| `APP-CLINICAL-BOUNDARY` | Databricks provides the mechanics of trust and radical transparency; clinical authority stays with standards bodies, customer staff and publishers. |
| `APP-COHORT-ALGEBRA` | Every figure on the flow is a co-membership query over per-define result tables in the warehouse using typed predicates (a boolean define's own value, any other's presence), never restating CQL semantics. |
| `APP-COMMODITIZE` | The competitor is the incumbent specialized stack, not another tool; the accelerator collapses it onto Databricks, and ergonomic sharing is the amplifier. |
| `APP-COMMUNITY` | Community feedback on measures and libraries: ratings, comments and usage signals, shown on the measure detail. |
| `APP-COMPUTE-ACTIVITY` | The compute pill reports when a run is happening and how far it has got via a stage badge on the popover's info line and a hairline progress bar on the collapsed trigger. |
| `APP-COMPUTE-TARGET` | The toolbar's compute pill lists SQL warehouses the signed-in user can see; the default is the deployment's warehouse, and SQL warehouses with unlimited queries are excluded. |
| `APP-CQL-AUTHORITATIVE` | CQL/ELM is the authoritative unambiguous source; every human-readable view is generated from it, never hand-maintained in parallel. |
| `APP-CREATE-NL` | Measure creation starts with a plain-English prompt or proven template; drafted CQL is always engine-validated. |
| `APP-DATA-PROFILE` | A selected definition's right-pane Data tab shows what it returns (from ELM), what it produced (top values, distributions, code hits), and how that distributed across subjects and elements. |
| `APP-DATA-REQS` | Generate per-measure data requirements (resources, attributes, value sets, timing) from the compiled logic for implementers. |
| `APP-DATASETS` | Evaluation runs against selectable datasets: provided sample/test sets, customer's bound data, or user-created sets. |
| `APP-DEBUG` | Plain-language outcome explanation in Evaluation shows which criteria decided each population membership for a chosen member, from real evaluated values. |
| `APP-DECISIVE-COUNT` | A criterion is decisive for a subject when flipping only that criterion changes their outcome; decisive counts lead on flow edges, with any-cause counts shown beside; together they partition the cohort exactly. |
| `APP-DECOUPLE` | Engine modules remain unmodified for this accelerator; the accelerator depends on the engine one-way with no Databricks-specific dependencies in the engine. |
| `APP-DEFAULT-EXAMPLE` | Every product feature ships a bundled example (the sample measure `HypertensionBPControl`) plus a preset lens/mode so new users see that feature working immediately, offline, with no backend or connected data. Amended 2026-10-21: the bundled example is `HypertensionBPControl`; `BasicEncounterAndCondition` is removed. |
| `APP-DEP-COUNTS` | In the Narrative view each criterion shows dependency counts in both directions and selecting a criterion highlights both directions of its transitive closure in the display. |
| `APP-DEP-NAV` | Opening an included library from the artifact selector navigates in place without repeating the parent label — a back affordance replaces the earlier redundant breadcrumb. |
| `APP-DERIVE-MEASURE` | A Derive action on external measures creates a new owned copy with lineage back to the original, recording source library and version, resetting its own version to draft, and carrying over any personal narrative overlay. |
| `APP-DESIGN-SYSTEM` | One visual language across the SPA: the Databricks brand palette plus three type roles and component patterns, implemented in theme.css and specified in design-system.md. |
| `APP-DIAGNOSTICS` | Validation via the Compiler Service is surfaced as plain-language clinical messages, preserving fail-loud semantics. |
| `APP-DISCUSSION` | Shared review threads anchored to Specification sections — the reviewer's channel back into the measure, keyed by library, version, and section anchor so they survive recompiles. |
| `APP-DQ-DEMO-EXCEPTION` | The Validate lens's readiness figures come from a bundled profile and are not marked as sample/synthetic — a narrow exception bounded to demo deployments, which replace the profile when observed data arrives. |
| `APP-EDITOR` | The frontend is a Vite + React SPA (TypeScript); the backend is Python/FastAPI running measures in-process via the OSS client; Monaco editor is guard-railed for clinical users. |
| `APP-ERGONOMICS` | The path from connected data to installed pack to computed result to stepping into a patient must be rapid, ergonomic, convenient and effective on owned compute. |
| `APP-EVALUATION` | The Evaluation module runs an authored measure or single definition against a dataset optimized for result inspection: population funnel, member list, plain-language per-member explanations. |
| `APP-EXEC-MODE` | Computation is a single dropdown colocated with the measurement period: Static, Test Deck, Live Sample, Live Population. |
| `APP-EXEC-PARAMS` | Execution parameters are measurement period and any parameters the measure declares, stepped via `‹ ›` arrows and dialled in the toolbar context-control bar. |
| `APP-EXECUTION-PILL` | The toolbar's one execution control with a status dot and caret, carrying data source, warehouse and update mode in its tooltip; its popover shows the run status and a Refresh button. |
| `APP-FLOW-GRAIN` | Where the unit of analysis changes the flow draws an explicit node naming the grain change and its multiplier; every scoring type (proportion, continuous variable, ratio) renders its terminal appropriate to that scoring. |
| `APP-FOCUS` | Execution of measures against customer data and mapping that data into pipelines are the primary value; measure authoring is secondary but a deliberate differentiator. |
| `APP-GRAMMAR` | Client-side CQL Monarch grammar drives keystroke-live highlighting and bracket matching. |
| `APP-HOME` | Landing page is a workspace dashboard over a searchable catalog of measures and libraries. |
| `APP-INSPECTOR` | Every tab in the right pane is always selectable; its body names what it has or, if empty, why (no run, no data source chosen, etc.); the layout does not reflow when tabs become available. |
| `APP-LIBRARY-VS-MEASURE` | A measure and a reusable library are distinct first-class artifacts with distinct create flows. |
| `APP-LIVING-CHARTS` | Contextual visualizations: inline contextual metrics appear in every lens when computed; consumption charts are the Analyze module's native content gated on run history. |
| `APP-LIVING-MEASURE` | The measure reads as a clinical specification in which every figure is computed and every concept is a link. Changing the measurement period recomputes the figures in place. |
| `APP-LOCAL-FIRST` | The Designer UI track builds and validates locally first; the compiler and notebook track deploy via Databricks since measuring against UC data requires the platform. |
| `APP-LOGIC-VIEW` | Measure logic shows in two views: Narrative (topologically-ordered definitions with plain language and on-demand CQL, filterable, highlighting transitive dependencies) and CQL (read-only whole library). |
| `APP-MAPPING` | The accelerator authors crosswalk/ConceptMap content and terminology suggestions scoped to measures; the customer wires the crosswalk into their retrieve view. |
| `APP-MEASURE-DETAIL` | Opening a measure shows an operational detail view with last run, schedule, result trend, composition, community feedback and actions. |
| `APP-MEASURE-DOC` | The central object is the measure as living document — one artifact that reads like a published spec and computes live numbers into itself, authoring and results two lenses over a shared narrative spine. |
| `APP-NARRATIVE-DOC` | The Specification renders logic as plain-language documentation — prose, bullets, tables — generated from authoritative CQL and hiding code behind a secondary view-source affordance. |
| `APP-NARRATIVE-EDIT` | Users can edit per-define narrative prose directly in the Define lens, an inline authoring surface for non-CQL-literate readers; edits persist in the overlay until Accepted. |
| `APP-NARRATIVE-OVERLAY` | AI-generated per-define narrative is stored as a per-user overlay, non-authoritative and visually distinguished from author text; accepting it writes the narrative back to CQL tags on owned measures. |
| `APP-NARRATIVE-PROVENANCE` | Every narrative element carries a visible, subtle provenance mark — authoritative (from CQL tags), generated (AI output), or edited (draft, not yet Accepted); marks transfer on Accept. |
| `APP-NARRATIVE-TAGS` | Authored measures store narrative metadata as CQL annotation tags (`@description`, `@rationale`, custom `@<identifier>`), the authoritative store for owned content; external measures are read-only. |
| `APP-NARRATIVE-VALIDATE` | When a user accepts or edits per-define narrative, the system verifies that the prose reflects the CQL it describes, flagging factual mismatches and omitted conditions before Accept completes. |
| `APP-NAV-DEPTH` | The Define nav is a tree with indentation per level, section headings at a distinct size and case, and a vertical guide rail connecting each heading to its rows. |
| `APP-NAV-FILTER` | The left pane filters definitions by type, population membership, and return type; a badge shows active filters and selecting a definition highlights its dependencies. |
| `APP-NAV-METRIC` | Every nav row carries the same figure as its section, resolved via the page's figure resolver. Share is a data bar right-aligned and growing leftward from the figure. Track width fixed. |
| `APP-NL` | Natural language is the main way clinical users author and question logic and results. Generated CQL is validated by the compiler, and explanations rest on real evaluated values. |
| `APP-NO-UI-MOCK` | The SPA carries no embedded mock or synthetic data; all data is API-served: live runs via the client's durable calculations, and synthetic examples via BFF-served deterministic fixtures. |
| `APP-ONE-SURFACE` | Designer is one surface; Validate contains readiness content and the document's four permanent sections, while the lens bar renders nothing. |
| `APP-PANE-STATE` | The left and right panes have a one-button size control cycling normal → expanded → collapsed → normal; when collapsed a pane shows a rail of its tab glyphs, and clicking one both expands and selects that tab. |
| `APP-PANE-TABS` | Tab strips in Define nav and workbench are one control. A tab is a button inside the strip, styled sans `meta`: sans weight constant so switching does not reflow. Resting: line only. Active: line + accent underline. |
| `APP-PATIENT-FILTER` | In Evaluation, filter to a specific member and see which populations they meet and which criteria decided each in plain language. |
| `APP-PATIENT-FOCUS` | Selecting a member re-centres the document on them: their value at every define, their timeline and the deciding criterion. It needs a per-member result feed, which the run path doesn't provide yet. |
| `APP-PER-FEATURE` | The Designer, agent, and compiler each have their own App; each feature App serves requests as the signed-in user with its own identity and persisted state, so they scale independently. |
| `APP-PILL-MENU` | The reusable selector-pill control carries a status dot and trailing chevron, opening a popover with independent single-select groups for an execution-context axis. |
| `APP-PLANES` | Three separated planes: Presentation (React/TypeScript SPA + Python/FastAPI backend-for-frontend), Compiler Service (JVM), and the OSS CQL engine in a sibling repo. |
| `APP-PLUGGABLE-CLINICAL` | Clinical judgment enters as pluggable, governed, versioned, attributed content authored by clinicians and publishers, including AI authoring instructions that steer NL guidance. |
| `APP-PREDICT-FOUNDATION` | The product provides foundational predictive modeling where factors are CQL defines, grounded in the customer's clinical logic and glass-box interpretable. The accelerator computes factors and hosts the module; the customer controls the model and threshold. |
| `APP-PREDICT-NL` | Users build predictions in natural language through the assistant, which translates intent to factor definitions in CQL, selects model classes, and wires MLflow automation. |
| `APP-PROBE` | Inspecting a sub-expression runs it in-process against the scoped cohort; the query and results are displayed. |
| `APP-PROFILE-STRATA` | Definitions are stratified by grain (subject or element) with labels, units, and notes stating the distinction; stratifications use the subject grain by default and never show element cuts for boolean defines. |
| `APP-RATE-AT-RISK` | The reported rate beside the band it could occupy once identified data gaps resolve, stating only failing checks widen it and only members who could cross a population boundary count. |
| `APP-RUN-LOG` | The Designer logs each run to deployment-owned Delta tables with user, measure, period, compute time, and status; no CQL or patient data. |
| `APP-SCENARIO` | A counterfactual is asked in natural language and answered as a side-by-side diff: the user describes a change to one definition, the assistant authors CQL against the live library, and the result renders beside the baseline. |
| `APP-SHARE` | Share measures and libraries with other organizations on Databricks over the open sharing protocol; recipients read them live, without copying artifacts. |
| `APP-SHELL` | Persistent three-region shell with top bar, left module navigation, and context-aware right AI agent; navigation and agent are independently collapsible. |
| `APP-SPEC-CELLS` | Each criterion section behaves like a notebook cell with a hover hairline margin bar and context actions (Run, Copy, Genie, Focus mode); a between-cell Add affordance invokes the assistant to author a new criterion. |
| `APP-SPEC-COLLAPSE` | Every top-level Specification section, library group, and definition is independently collapsible with a caret; an Expand all / Collapse all control drives whichever sections are present. |
| `APP-SPEC-MODEL` | Both the left nav and the Specification page render one model derived entirely from the compiled ELM, so the two surfaces cannot disagree about what they show. |
| `APP-STABLE-LAYOUT` | Selecting or toggling a control never shifts position or size of others unless movement is the point; controls appear always and disable when idle, reserving their space; active state uses color or underline, not weight or size. |
| `APP-STEP-INTO-PATIENT` | Any population count opens one member's story: a timeline of relevant events and which criterion placed them where, with a way back to the aggregate. |
| `APP-STYLE-GUIDE` | Enforceable style guide operationalizing the design system: one semantic color vocabulary, fixed type scale, and DM Mono reserved for code/data only. |
| `APP-TO-APP-OBO` | A user reached through one App and making requests to another is reached as themselves (on-behalf-of) through a shared OAuth identity, so inter-App calls preserve the caller's identity and permissions. |
| `APP-TOUR` | An in-app product tour shows the platform's pillars — analytics, living document, glass-box transparency, governance, knowledge architecture, the clinical decision layer and its applications — each with a Show me deep-link to a live example or the example notebooks. |
| `APP-TRAJECTORY` | A member's view can show a disease-state trajectory from a pluggable, attributed model. The visualization is ours; the model is content. |
| `APP-UPDATE-MODE` | Measures update automatically when logic changes, or only on explicit Refresh; the default is manual, and the mode is shown in the compute pill's status line with reasons why no results are displayed. |
| `APP-URL-ROUTING` | Workspace reading position lives in the URL (active lens as path, open artifact as query parameter, selected criterion as query parameter); navigation pushes history entries. |
| `APP-VALUE-READABLE` | Returned values render with a summary line, expandable field list in clinical order, and raw value in a tooltip; the block is labelled by grain (subjects at row level, elements at element level), and bars appear only over aggregated distributions. |
| `APP-VISION` | Two surfaces onto the engine in one workspace: runtime execution of measures on customer data, and authoring/discovery/editing/evaluation via natural language. |
| `APP-WORKBENCH` | The right pane holds data about the selected definition (return type, runtime profile, terminology bindings) in tabs; pane scope is resolved once per tab, never re-derived per-tab. |
| `APP-WORKSPACE-FOLDER` | The Designer loads CQL libraries from and saves them to a Databricks workspace folder as an alternative to tagged knowledge sources, read-write as the signed-in user, with collision detection on save; folder sources are draft only. |

## The Specification document

The measure document the Designer derives from ELM.

| ID | Requirement |
|---|---|
| `REND-BADGES` | Validation badge states compile diagnostics and Scorekey verdict (pass/findings/not run, sealed when pass). Mapping badge retired (coverage unbuilt). |
| `SPEC-ANCHOR` | Everything is linkable and shows its code. Anchors key off library-qualified define names, never display aliases. Source chips are real links. Every Concept and Rule discloses the CQL at its derive statement. |
| `SPEC-ATTRITION` | A criterion's inline figure states what it did: `10,318 → 8,204 patients · 79%` with a share bar. Base is the nearest KEPT stage above or the population it sits under. Delta is neutral ink. |
| `SPEC-CLAUSE-PROBE` | Rejected: clause-level probing for per-sub-expression counts. Define is the accountability boundary. A clause without a name cannot be narrated. If a clause deserves a figure, promote it to a define. |
| `SPEC-COMMENT-NARRATION` | Free-text comments above a define reach consumers: the engine preserves preceding comment blocks. Blank line discriminates declaration documentation from file-level notes; blank-separated comments are dropped. |
| `SPEC-CONCEPTS` | A Concept is a define reached at depth 1 from a population criterion in the ELM graph. Granularity is CQL structure, not heuristic. Titles are narrated; unglorified defines render under their name. |
| `SPEC-CQL-LINKS` | A quoted name in the CQL is followable to something the document renders. Defines get the lens; a value set jumps to the Appendix row. Unresolved name stays plain syntax. |
| `SPEC-CQL-VIEW` | CQL block is closed by default. A citation opens it. With Explain off and no `@comment`, it opens. Explain off with `@comment` keeps it closed. Reader's click overrides. |
| `SPEC-DATA-REQUIREMENTS` | Data requirements section derives from ELM Retrieves: one row per resource type, bound by profile, with eliminable reads included. Parameters table below when populations parameterize the logic. |
| `SPEC-DEFINE-REF` | Narration binds a phrase to another element: `<ConceptRef library="…" define="…">example condition</ConceptRef>`. Library-qualified. Unqualified is dropped. Unresolved renders as prose. Same gesture, mark, lens as defines in CQL. |
| `SPEC-DEFINE-SECTIONS` | Every define renders as a population criterion does: prose, its own CQL, and dependency index. No intermediate-vs-concept judgment; CQL structure decides clinical weight. |
| `SPEC-DEFINITIONS` | Definitions section is the root library's population criteria and declarations, followed by included library sections. Each section renders at verbose (full prose) or compact (rows) based on narration coverage. |
| `SPEC-DERIVED-APPENDIX` | Terminology lists every code and value set the visible logic names (OID stated once). Parameters table. No hand-copied OIDs; derivation is enforced by CI lint. |
| `SPEC-DOCUMENT` | The specification document named "Specification" derives from ELM and is the reader's primary artifact. Sections: Overview, Definitions, Common, Included definitions, Appendix, Terms. |
| `SPEC-ELEMENT-GLOSS` | A define's heading is its name. A one-line gloss (not paraphrase) states what the name doesn't carry, rendered in reading face. Gloss may be absent for self-describing names. Replaces narrated titles. |
| `SPEC-EXPLAIN-ACCOUNTING` | Linkable elements in narrated sections are accounted for: every name the renderer makes linkable is stated in the section's prose. Coverage is checked per (library, version) against committed reference sets. |
| `SPEC-EXPLAIN-AGAIN` | A section's narration can be re-generated via the Explain toggle's menu. The act aims at a criterion's inline EXPLAIN mark. Opening the assistant drawer is scoped to that define. |
| `SPEC-EXPLAIN-INPUTS` | The resolved reference set a narration is generated against is server-side derived from ELM, carried on `/api/translate`, one artifact per (library, version). Derivation is consumed, not re-derived client-side. |
| `SPEC-EXPLAIN-VIEW` | An Explain toggle filters prose by provenance: off shows only ELM structure, metrics, code, and authored prose; on adds narrated prose marked EXPLAIN. Filtering by provenance, not source. |
| `SPEC-EXPLAIN-VOICE` | Generated narration follows a runtime prose contract (`clinical-explain/SKILL.md`): a clinician persona, banned vocabularies (jargon, hyperbole), fixed forms (Include/Exclude blocks, value-set refs), symmetry rule, seven-point read-back. |
| `SPEC-INLINE-ANCHOR` | Every inline reference carries its own `sourceSpan`, so it anchors itself rather than leaning on the field's widened span. Enforced in XSD; tested against ELM. |
| `SPEC-LICENSE` | The steward's copyright notice and disclaimer are verbatim, displayed, never summarised. For a measure steward's content the notice states reproduction terms; dropping it makes the document non-redistributable. Amended 2026-10-20: steward names dropped; the rule applies to any steward's content a user loads. |
| `SPEC-LOGIC-PANES` | The Define lens discloses CQL behind every element. Syntax highlighting uses the same Monarch grammar as Monaco. Unhighlighted, complex `Query` with `without … such that` reads as undifferentiated wall. |
| `SPEC-MEASURE-METADATA` | The Overview is built from published FHIR Measure resource first, quoted verbatim: description → Description, purpose → Intent, rationale → ClinicalRationale, etc. Authored wins the slot; narrated text for that field is dropped. |
| `SPEC-METRIC` | Every element carries a metric: distinct subjects (cohort grain) and distinct elements (list grain). Form is typed dispatch off ELM result type (Boolean → count; List → cardinality histogram; Interval → omitted). Base is stated and never guessed. |
| `SPEC-METRIC-REF` | Narration cites a figure inline: `<MetricRef library="…" define="…" grain="subject|element" as="count|share"/>`. Narrator who may only cite cannot state wrong numbers. Unresolved refs render an absence mark. |
| `SPEC-NARRATIVE-DEVICES` | Narrated prose has a vocabulary of forms: polarity blocks, value-set names, code tables, anchored timings, required-vs-optional exclusions, numerator/denominator symmetry, definitions table, timeline, worked example. |
| `SPEC-NARRATIVE-TAGS` | Every spec prose has a named home in CQL: a tag. Rendered name, XSD element, and tag name are the same word (e.g., Intent). Root tags cover the measure; element tags are the same four for population/Concept/Rule. |
| `SPEC-NAV-COMPOSITION` | Define nav carries Outline (document table of contents in published order) and Composition (same entries arranged by reachability from populations). Composition is a global tree, rooted at populations. |
| `SPEC-NO-UI-PROSE` | Prose a clinician reads in the Specification has exactly two origins: measure CQL tags or a specification instance. Prose written into a component is forbidden. Test pins this via grep. |
| `SPEC-OVERVIEW-HEADINGS` | Overview has one heading per question at the same level. Intent, ClinicalRationale, and ClinicalRecommendation are separately authored fields answering different questions; nesting under "Intent" made two substantial pieces read as footnotes. |
| `SPEC-PARAMETER-REF` | Narration cites an execution parameter's value: `<ParameterRef name="Measurement Period"/>` renders as the dialed value (e.g., "2026"). Only citation that needs no run. Unresolved renders nothing (not em-dash). |
| `SPEC-PLACEMENT` | Every define renders exactly once, at the lowest common dominator of all that reach it. Reached from one Concept: a Rule under it. From multiple populations: hoisted to root (COMMON). |
| `SPEC-POLARITY` | A Rule's polarity (qualifying/disqualifying/computational) is derived: the negation parity of its ancestor chain in the ELM, plus exclusion-population membership. Recorded at the reference site. |
| `SPEC-PROVENANCE` | A Specification is an overlay on CQL. Shape is derived (faithful to ELM by construction); prose is narrated (reviewable). Two never share a provenance attribute; an element's structure is derived while its text is narrated independently. |
| `SPEC-READ-REF` | Narration binds a phrase to a read: `<Read profile="qicore-condition-encounter-diagnosis">encounter diagnosis</Read>`. Reader follows the binding to the ELM retrieve model. Instance carries binding and words, never a count. |
| `SPEC-REF-LENS` | Every reference (define, retrieve, prose) opens a 600×340px panel via press (not hover). Define lens: identity, Details/Demographics/Terminology tabs. Retrieve lens: call sites (read by). Terminology lens: Open button to Appendix. |
| `SPEC-REPORTED-RESULT` | The reported figure sits at the document head: performance rate, population legs (IPop, Denom, Exclusions, Numerator), and funnel as stacked bars. Removals tile the denominator's tail by declaration order. |
| `SPEC-RETRIEVE-LENS` | Every retrieve in rendered CQL shows a hover lens with resource type, value set and OID, plus subject/row counts. Derives from ELM; locator joins by line range to narrated claims. |
| `SPEC-RULES` | A Rule is a define in a Concept's transitive closure, rendered flat. Functions are transparent: never Concept or Rule themselves, but the walk passes through them. Rules order by graph layer then declaration order. |
| `SPEC-SAMPLE-METRIC` | A metric marked `provenance="sample"` is illustrative and says so. Never meets requirements asking for real figures. Three obligations: marked wherever it appears; stripped from non-demo lanes; never satisfies conformance. |
| `SPEC-SCORING-METHODOLOGY` | How an eCQM scores is stated once, derived, never narration. Rendered in Overview's "How to read", as formula with measure terms, role-by-role account, schematic and exception rule. Declared, not inferred. |
| `SPEC-SHARED-ORDER` | Section and define order is a specification property set once in a shared model, not per-surface. Sorts by declared order (ELM), topological, used-by, uses, or name (alphabetical). Populations never sort; they stay in eCQM reporting order. |
| `SPEC-STATIC-OVERLAY` | Narration for external measures is served as a static XSD-conformant Specification instance. Overlay carries prose only; placement and element set stay ELM-determined. Version is part of the key. SUPERSEDED 2026-10-20 by `APP-NARRATIVE-OVERLAY`: the static instances were removed with the official measure content; narration is generated per user. |
| `SPEC-TERMINOLOGY-EXTRACT` | Terminology tab reports coded values returned records carry, grouped by path (Diagnosis/Category/Status/Body site). Paths are curated, not discovered. Values derive from result relations, not re-filtered. |
| `SPEC-TERMINOLOGY-REF` | Narration binds a phrase to a value set or code: `<TerminologyRef library="…" name="Example Condition Diagnoses">…</TerminologyRef>`. Resolves against filtered terminology set. OID never in instance. Lens has Open button; no Pin. |
| `SPEC-XSD` | The specification element model is defined by XSD (designer/server/schema/specification.xsd), owned and published as the normative contract. Enforced via CI validation. |

## Designer integration

How the Designer works with the rest of the system.

| ID | Requirement |
|---|---|
| `DESIGNER-COMPUTE-UNCOMPUTED` | Run triggers pipeline update recomputing stale and uncomputed MVs. BFF deploys updated config and calls `start_update`; SSE stream reports per-define progress. Single-define runs stay interactive. |
| `DESIGNER-DEFINE-STATUS` | Per-define pipeline status badges: unknown (gray), stale (amber), computing (amber pulse), fresh (green check), error (red). Driven by pipeline event log via SSE, not a bespoke tracker. |
| `DESIGNER-SDP-NAV` | Define nav is a hybrid: merged graph from BFF (cross-library edges, deduplication); local CQL scan (node classification, leaves). Engine's `/describe` provides boundaries and provenance. Local scan provides leaf nodes. |
| `DESIGNER-TOP-BAR` | One bar with three zones: Left = breadcrumb (product glyph IS module switcher, artifact selector); Centre = compute context (Explain, Data Source, execution pill); Right = app toggles (tour, Appearance, Settings). One outline material throughout. |
| `DESIGNER-TRANSITIVE-STALE` | CQL edit marks define stale; transitively marks downstream dependents stale using merged DAG's `dependentsOf` edges. Stale shows amber dot (needs recompute); computing shows pulse. |

## Services, hosting and deploy

The compiler service, the Apps, the bundle and local runs.

| ID | Requirement |
|---|---|
| `BUILD-COMPILE-LINK` | Multi-library measures link dependencies at compile time by carrying their ELM as a `deps` array on the request; the accelerator resolves include closures from the registry and merges them into each compile body. |
| `DEPLOY-APP-GRANTS` | Each App receives its own service principal; Unity Catalog grants (USE_CATALOG, USE_SCHEMA, SELECT, CREATE) are applied imperatively by script. |
| `DEPLOY-LAKEBASE-SCHEMA` | Database DDL lives only in the deploy definition. Code assumes its tables exist and fails loud if they don't. Seed scripts issue DML only. No feature uses Lakebase today. |
| `DEPLOY-SEED-DATA-SOURCES` | The seed script populates the Test Deck synthetic data source with idempotent DML; the `data_sources` table itself is created by the deploy's schema definition. |
| `SVC-AGENTS` | Package CQL and measurement capabilities as composable agents for Genie, custom apps, and third-party systems. |
| `SVC-ANSWER-PROVENANCE` | Distinguish engine-computed answers from agent-derived answers via provenance metadata on each result. |
| `SVC-API-HOST` | The Compiler Service provides a network endpoint hosting the engine's compile APIs via HTTP. |
| `SVC-APP-TOPOLOGY` | Three independent apps: Compiler (JVM), Designer (Python/React), and agent (Python/MCP). |
| `SVC-BINDING-RUNTIME` | Data and terminology are bound at execution time in the client session, not at compile time. |
| `SVC-CATALOG-LAYOUT` | Unity Catalog structure: the configured product catalog (bundle variable `uc_catalog`, no default; set in `databricks.local.yml`) with `runtime` schema for UDFs, `artifacts` volume, and per-Data-Space schemas. No catalog name appears in source. |
| `SVC-CDS-HOOKS` | Emit CDS Hooks-compatible artifacts and provide the evaluation backend for hook-driven clinical workflows. |
| `SVC-COMPILE-BATCH` | One compile request per durable preparation, with dependency ELM retained and callers identified explicitly. |
| `SVC-COMPILE-CACHE` | Process-local LRU cache for successful compilation responses, keyed by build, operation, and request content. |
| `SVC-COMPILE-ENDPOINT` | One HTTP endpoint for both interactive authoring compiles (translate, validate, plan, explain) and runtime compiles. |
| `SVC-COMPILER-IDENTITY` | Version identifier for the deployed compiler, published to signal when recomputation is needed after compiler updates. |
| `SVC-COMPILER-M2M` | SUPERSEDED by `SVC-EXCHANGE-RETRY`. A Job reaches the Compiler App with its own identity through the token exchange, which retries the intermittent `invalid_grant` M2M was a way around. The deploy provisions no compiler caller, and the client rejects the retired `compilerAuth`, `compilerClientId`, `compilerSecretScope` and `compilerSecretKey` settings. The caller lookup also never matched: the workspace SCIM API does not keep `externalId`, so every deploy created another caller and OAuth secret. |
| `SVC-DEPLOY-BUNDLE` | One Asset Bundle declares every App and its permissions, and deploys them together. |
| `SVC-DEPLOY-ENV-LOCAL` | Workspace hosts, profiles, cluster IDs, and warehouse IDs are never committed; they live in gitignored local files. |
| `SVC-DEPLOY-TARGETS` | One codebase runs unchanged on Local and Databricks targets through a platform-provider abstraction. |
| `SVC-DISCOVERY-PLUGIN` | Databricks plugin that resolves the client's compiler discovery, using app naming convention and OAuth token exchange. |
| `SVC-EXAMPLE-INDEX` | Published examples start with a one-page documentation index cell with no computation. |
| `SVC-EXAMPLE-KNOWLEDGE` | Examples ship with supporting content: data sources, terminology, and reference libraries. |
| `SVC-EXAMPLE-NOTEBOOKS` | Runnable example notebooks are deployed as product artifacts, not repository samples. They are checked by the live test suite (`scripts/test.sh integration`) as one-time runs; the deploy creates no Job for them (amended 2026-10-11). |
| `SVC-EXCHANGE-RETRY` | The Databricks plugin's App token exchange retries `invalid_grant` with backoff for up to 3 minutes, logging one warning per retry with the error code only, then fails with the usual error. A workaround for an intermittent platform failure in Jobs; the generic exchange does not retry unless a plugin opts in. |
| `SVC-FHIR-ENDPOINTS` | Expose a conformant HL7 FHIR Clinical Reasoning API surface: Measure/$evaluate-measure, Library/$evaluate, etc. |
| `SVC-GENIE-ONTOLOGY` | Publish each measure's result surface as governed enterprise knowledge in the Genie ontology. |
| `SVC-LOCAL-CONFIG-BUNDLE` | All local settings have one home: workspace settings in gitignored local files, session configuration in code. |
| `SVC-MCP` | Expose CQL and measurement capabilities as MCP tools for consumption by Genie agents and custom apps. |
| `SVC-MCP-CONTRIBUTED-TOOLS` | Contributors add to the standing tool instructions via the tool registry. |
| `SVC-MCP-SKILLS` | Standing guidance shared by all MCP clients, defining tool and agent behavior on the same terms. |
| `SVC-MCP-TOOLS` | Standalone MCP app serves the agent's tools; registration contract allows multiple transports. |
| `SVC-MCP-TRANSPORT` | Transport is a registration contract, not a deployment detail; three properties determine client transport. |
| `SVC-MODEL-ENABLEMENT` | Enable the Predictive Foundation via platform ML: Feature Store, MLflow tracking and Model Registry, Model Serving, Lakehouse Monitoring, AutoML. |
| `SVC-MODULE-OPTIN` | Deploy-time module manifest gates which functional modules the Designer app harnesses and mounts. |
| `SVC-MODULE-SPLIT` | Optional promotion of a module from in-app surface to its own Databricks App with cross-app launcher. |
| `SVC-NOTEBOOK-CELL-LIBRARY` | A measure can be authored across notebook cells via a `%%cql` cell magic and mounted library directories. The declared cells belong to the Spark session (`CLIENT-SESSION-STATE`), not the Python process. Amended 2026-10-11. |
| `SVC-NOTEBOOK-CQL-MAGIC` | Cell magic `%%cql` registers with the Python extension and enables measure authoring in notebooks. |
| `SVC-NOTEBOOK-LIBRARY-FOLDER` | A notebook mounts one or more directories of .cql files as its library source via `connect(library_path=...)`. Library references resolve in one order: `%%cql` cells, then the mounted directories, then tagged knowledge in the session's catalog scopes, earliest scope first (`CLIENT-KNOWLEDGE-INCLUDES`). A library from tagged knowledge resolves its includes in tagged knowledge only. |
| `SVC-NOTEBOOK-RUNTIME-BIND` | Runtime bindings: discover UDF schema from the registry, rewrite serverless barriers, no `%scala` cell needed. |
| `SVC-PACKAGING` | Small jars ship as app source; oversized runtime artifacts (JRE, three public jars) are hosted in a UC Volume with public-URL fallback. |
| `SVC-PLATFORM-API` | Three-level composable API: App-to-App resource, published SDK, and MCP/agents. |
| `SVC-PROTOCOL` | Browser-to-BFF and BFF-to-Compiler over REST/JSON HTTPS with bearer tokens and OAuth. |
| `SVC-PY311-SYNTAX` | Deployed backend code must parse under Python 3.11; staging validates all source with `python3.11 -m compileall` to catch 3.12-only syntax before App startup. |
| `SVC-REF-PROVIDERS` | Supply reference-only DataProvider and TerminologyProvider to the compile service. |
| `SVC-RESOURCE-SCHEMA` | Compiler exposes POST /resource-schema to query the modeled structure of FHIR types. |
| `SVC-RUN-DBCONNECT` | In-process execution via Spark Connect to serverless or all-purpose clusters. |
| `SVC-RUN-EVAL` | Measure execution in the Designer instance's Spark session via the OSS cql() client. |
| `SVC-RUN-SERVERLESS-BARRIER` | On serverless, rewrite cache table statements to temporary views before execution. |
| `SVC-RUN-STAGE` | Current execution stage name and progress indicator displayed on the compute pill. |
| `SVC-RUNTIME-UDF` | Deploy cql_* UC catalog and register on the runtime cluster as cluster-scoped libraries. |
| `SVC-RUNTIME-UDF-IDENTITY` | Tracks which build of the engine's `cql_*` UC functions is registered, triggering full refresh when UDFs are re-registered despite unchanged compilation output. |
| `SVC-RUNTIME-UDF-UC` | CQL operators register as UC Scala functions on serverless, SQL warehouses, and all compute types via manifest-derived DDL and dependencies staged in a runtime volume. |
| `SVC-SESSION-INSTANCE` | Each Designer instance owns its Spark Connect session; concurrent instances do not share temp views. |
| `SVC-SPLIT` | Authoring compile is Spark-free and interactive; runtime compile is Spark-free and per-pipeline-update. |
| `SVC-STAGE-LOCAL-JARS` | Source the three public Maven jars from installDist output instead of re-fetching from Maven Central. |
| `SVC-STATE-BOUNDARY` | Knowledge sources are UC-based; per-instance state, terminology, and UDFs are stored separately. |
| `SVC-STATELESS` | The Compiler Service is a pure function: no session, no user data, no state held between requests. |
| `SVC-UPDATE-MECHANISM` | Each accelerator component carries a version and supports upgrade-in-place with migration paths. |
| `SVC-WHEEL-VERSION` | Wheel filenames encode version derived from pyproject.toml; substituted at staging with a guard to prevent drift. The client wheel's version is `<pyproject version>+src<content hash>`, so a client whose contents changed always installs over an App's kept venv, with no manual bump. |
| `SVC-ZERO-CONFIG-NOTEBOOK` | Open a notebook and run cql("...") with no configuration when a single deployment matches the default naming. |

## Data sources

How data sources are found, bound and observed.

| ID | Requirement |
|---|---|
| `CLIENT-EXEC-DISCOVERY` | `exec()` and `materialize()` read data the way a durable calculation does: the schema tagged `cql_databricks_data_source`, in the caller's catalog scopes, sent to the compiler as its routines (`SVC-COMPILE-BATCH`), with value sets from the runtime `valuesets` view. The source is the one named by `data_source=` or `spark.cqldatabricks.dataSource`, else the only one in scope; several in scope fail, naming them. CQL that retrieves nothing resolves nothing (`EMIT-DESCRIBE-RETRIEVE-TYPES`), and a source missing a retrieved type fails before the run. An explicit `source_binding` or `data_catalog` is used instead. Amended 2026-10-11 by `RUN-CLIENT-RESULT-IDENTITY`: `materialize()` refuses an explicit `source_binding` or `data_catalog` when the CQL retrieves data, because a kept result is identified by a tagged source's address. |
| `CLIENT-RESOURCE-SCHEMA` | The client exposes the compiler's FHIR model: `CqlClient.resource_schema(fhir_type=None)` returns each modeled type's `__resource` struct as a PySpark `StructType` and DDL, and whether it is in the Patient compartment. It reads them from `/resource-schema` (`SVC-RESOURCE-SCHEMA`) once per client and never derives a schema itself. An unmodeled type raises, naming it. |
| `CLIENT-SESSION-MOUNT` | `mount(spark, fhir, valuesets=None)` puts FHIR data and value sets on a Spark session with no catalog writes. `fhir` is a folder of `<Type>.ndjson`, a `{type: DataFrame or table}` dict, a `FhirJson` relation of mixed types, or a list of these; a string is a path only if it contains `/`, and a table name must be `catalog.schema.table`. Each type is checked (`BIND-SCHEMA-CHECK`), reshaped (`BIND-RESHAPE`) and registered as a `TEMPORARY` function named `retrieve_<Type>__cqlmount`. Every client on the session reads the mount at each compile, and it replaces discovery; an explicit `data_source=` or `source_binding` beats it. A retrieve of an unmounted type fails, naming the mounted types. Mounting again replaces it, and a compile cached against the old mount is not reused. A durable calculation refuses it. |
| `CLIENT-SESSION-RESET` | `reset(spark)` drops the mount, its temp functions and views, and the libraries `%%cql` cells declared, then the session record. Safe to call twice. Define result views are left alone. |
| `CLIENT-SESSION-STATE` | The `%%cql` cells and the mount are rows of one temp view, `__cqldatabricks_session`, in the Spark session, so they end together and never disagree. Reading with no Spark session finds nothing; declaring a cell with none raises. The client writes no session conf. |
| `CONFORMANCE-SCHEMA` | Conformance data source is provisioned by the deploy and provides fixed test data for validation of library evaluation logic. Amended 2026-10-11 by `EMIT-ROOT-INDEPENDENT`: one conformance data source per measure the corpus covers (`DATA-CONFORMANCE-MEASURE-SELECT`). SUPERSEDED 2026-10-21 by `CONTENT-SAMPLE-PRELOAD` and `DATA-SAMPLE-SOURCE`: the per-measure conformance sources were built from the official test cases fetched from GitHub, which are removed. The deploy now lands the sample patients as one data source, and the compiler's conformance corpus stays internal to `compiler/`. |
| `DATA-BIND-PROGRESS` | Knowledge views are bound step by step; each step's outcome is streamed to the caller. |
| `DATA-BINDINGS` | The knowledge views `{catalog}.runtime.measures`, `.libraries` and `.valuesets` union the layout tables of every schema tagged `cql_databricks_knowledge` or `cql_databricks_terminology`. The deploy creates them, and only their owner can rebind them. |
| `DATA-CHANGE-LOG` | A data source declares a `change_data_log` relation with per-FHIR-type change timestamps. The compiler reads it once per run to compute a data fingerprint for every barrier statement, detecting when data moved and triggering recompute. |
| `DATA-CHANGE-LOG-PERIODS` | A data source may declare a `change_log(parameters, changed_at)` relation to signal which evaluation periods changed. The compiler uses it to refresh only those periods; without it, every open period refreshes every run. |
| `DATA-CONFORMANCE-CORPUS` | Conformance data is a shared, consolidated corpus in one `conformance` schema with one mount table per FHIR type, shared terminology, and golden results. A deck is a `(deck, version)` row slice; install is `MERGE`, uninstall is `DELETE WHERE deck=…`. SUPERSEDED 2026-10-21 by `DATA-SAMPLE-SOURCE`: the deck corpus existed only for published test content, which is removed. |
| `DATA-CONFORMANCE-MEASURE-SELECT` | Measure selection lives in the retrieve UDTF via the root library id passed as a named parameter, not in the source or bind step. One measure-agnostic Conformance source filters the shared corpus by measure. Amended 2026-10-11 by `EMIT-ROOT-INDEPENDENT`: the first sentence no longer holds. Measure selection is the data-source binding: the deploy provisions one conformance source per covered measure, schema `conformance_<measure>` tagged `conformance-<measure>`, whose UDTFs fix the measure filter in their bodies over the shared corpus tables, and retires the single `conformance` source. The emitted SQL names no library. SUPERSEDED 2026-10-21 by `DATA-SAMPLE-SOURCE`: there is no per-measure conformance source left to select. The sample patients are one source every sample measure runs over. |
| `DATA-CONTRACT` | The customer data layout contract: canonical FHIR-in-Delta tables with `(__subject_id, __resource)`, `retrieve_<Type>` views with `cql.*` properties, a `valuesets` table, and optional `ConceptMap`. The compiler owns none of this; the customer or their Databricks product implements it. |
| `DATA-CROSSWALK` | The compiler owns versioned, shareable `ConceptMap` / crosswalk artifacts (code-to-code mapping, many-to-one, conditional `dependsOn`) that the customer's retrieve view applies. A governed content piece paired with the customer's view implementation. |
| `DATA-DEMO-POPULATION` | A demo data source with a fabricated hypertensive population at realistic control rates, for showing plausible measure results. Kept separate from conformance to avoid desynchronizing the answer key. Data source `synthetic-population-small`, schema `synthetic_population_small`. SUPERSEDED 2026-10-21: the demo population and its generator were built on an official measure's shape and are removed. Synthetic populations are re-synthesized from the repository's own measures (`DATA-SYNTH-LARGE`), and the sample patients are `DATA-SAMPLE-SOURCE`. |
| `DATA-SAMPLE-SOURCE` | The sample patients in `examples/data` are one data source, `sample-patients` (schema `sample_patients`), landed by the deploy through the shared population lander (`content_ingest.population`): one mount and one retrieve UDTF per bound FHIR type, empty where the samples have no rows, with its change logs and data-source tag. Fabricated, and its description says so. Added 2026-10-21. |
| `DATA-INSIGHT-EXTRACTION` | A curated declaration of `(data model, resource type) -> elements worth surfacing`. Queries generated from catalog, feeding Terminology sections and Demographics cuts. Model is part of key; uncurated pairs yield nothing. |
| `DATA-OBSERVABILITY` | The compiler observes the view boundary, UC lineage, and direct queries to diagnose coverage, quality, and provenance. It describes, never remaps or prescribes structure. |
| `DATA-PROVENANCE` | For any result value, the compiler traces retrieve view to source tables via UC lineage and optional LLM narration. Diagnostic only; describes and audits, never remaps. |
| `DATA-PUSHDOWN-DIAG` | A diagnostic to verify binding pushdown: call the mount twice (hints populated vs. empty) and report the row-count delta. Secondary tool for implementers; not required by the engine's own gates. |
| `DATA-REGISTRY` | The Settings module's first page shows three sections: Knowledge Sources (logic and terminology, global), Test Decks (conformance fixtures), and Data Sources (per-type retrieve bindings). All are read-only; a reload button searches for tagged schemas. |
| `DATA-RESOLUTION` | Retrieve-to-source resolution is engine-owned. The compiler reads the catalog metadata for observability but neither implements nor overrides the engine's best-match algorithm over `cql.*` properties. |
| `DATA-SOURCE-AUTORESOLVE` | The source editor offers a convenience: pick a catalog and schema, and the BFF introspects it to prefill type mappings by matching the `retrieve_<Type>` convention and `(__subject_id, __resource)` columns. The user reviews and overrides. |
| `DATA-SOURCE-BIND` | One compiled plan binds to any target with no recompile. Customer sources are UC-precreated functions in a tagged schema (`CLIENT-EXEC-DISCOVERY`), or a schema named outright with `connect(data_catalog="cat.schema")`; conformance uses session-scoped temporary views. Both produce the same fixed `retrieve_<Type>` names for the engine. Amended 2026-10-11: the `USE CATALOG`/`USE SCHEMA` route is the explicit `data_catalog=`. |
| `DATA-SOURCE-BINDING-MODE` | Each FHIR data binding operates in UDTF mode (passthrough wrapper) or Relation mode (auto-filtering); both resolve through a unified `retrieve_<Type>` interface. |
| `DATA-SOURCE-DESCRIPTION` | Each data source has an optional one-line description (the schema's `COMMENT`) shown in the compute pill. Absence is stated, not filled. Replaces assembled metadata that described the wiring rather than the data. |
| `DATA-SOURCE-MODE` | The run's binding target is selected from discovered data sources: Test Deck (bundled synthetic offline), conformance (golden fixtures), or live customer sources. The UI discovers them from UC tags; live samples vs populations are separate registered sources. |
| `DATA-SOURCE-PROFILE` | Each data source's binding profile maps modeled FHIR types to retrieve UDTFs and a valuesets location. The profile resolves from the tagged schema's functions and is mapped back by the layout check. Sampling is implemented entirely inside the customer's UDTFs. |
| `DATA-SOURCE-REQUIRED` | Governance classes on data sources: Conformance is required and selectable in every environment to stamp a certification receipt. Test Deck is default and non-removable. Only user-defined live sources are removable. |
| `DATA-SOURCE-SCOPE` | A data source binds only the data plane. Logic (Measure and Libraries) and terminology (value-set expansions) are global, resolved outside the source. This keeps authoring separate from execution. |
| `DATA-SOURCE-UC-TAG` | A data source is a Unity Catalog schema tagged `cql_databricks_data_source = <name>`. Nothing stores a record. The client lists them (`cql_databricks.compiler.data_source.list_data_sources`), reading bindings and change log from each tagged schema; the Designer caches the list per user. |
| `DATA-SUGGEST-TERMINOLOGY` | The compiler suggests terminology entries for user confirmation: unmapped codes ranked by frequency and similarity, and miscoding detection from cross-field correlations. Grounded, never fabricated. |
| `DATA-SYNTH-LARGE` | The synthetic population large data source (`synthetic-population-large`, schema `synthetic_population_large`): about a million patients generated by the patient data synthesizer at realistic rates, 250,000 requested per pinned measure (`loader/docs/synthetic-data.md`), with one patient per manifest row carrying expected results. Managed source, never the default. Amended 2026-10-21: the cohorts are re-synthesized from the repository's own measures; with one pinned sample measure the source holds about 250,000 patients, and earlier cohorts generated from official measures are removed. |
| `DATA-SYNTH-MARKETPLACE` | Not built. Planned after v0.3 (roadmap `DATA-SYNTH-MARKETPLACE`). A pre-generated synthetic population (`DATA-SYNTH-LARGE`), on the `DATA-CONTRACT` layout with synthetic markers (`SYNTH-PDS-SYNTHETIC-TAG`), is published as a free Databricks Marketplace listing. A deployment installs it as a catalog and tags its schema `cql_databricks_data_source` (`DATA-SOURCE-UC-TAG`), so it is demoable from the first deploy without generating data in the workspace. One population serves every measure, so a shared library's results are computed once (`DEC-LIBRARY-OWNS-RESULTS`). The repository holds the generation Job, the install-and-tag step and the docs; the listing's provider profile is the publisher's. |
| `DATA-VIEW-SCAFFOLD` | The compiler generates starter retrieve-view DDL with `cql.*` properties as a template. The customer adopts and owns the deployed object; the compiler authors the shell, never the view. |
| `PERF-CACHE-REVISION` | Browser cache revalidation uses one computed digest of compiled libraries, data sources, knowledge locations, and run history; every `/api` GET is served with weak ETag based on the digest, and only 200 responses are tagged. |
| `PERF-CLIENT-REVISION` | The BFF publishes its revision to every response via header and dedicated API; the SPA mirrors it and serves cached data until the revision changes, then drops everything. |
| `PERF-CLOSURE-TRANSLATE` | Include closure compilation passes each library only its own declared includes to the compiler, avoiding quadratic re-parsing of accumulated sources. |
| `PERF-DISCOVER-LIGHTWEIGHT` | Library discovery omits CQL source to avoid transferring megabytes; source is lazy-loaded on demand. |
| `PERF-RESPONSE-GZIP` | The BFF gzips responses over 1024 bytes; SSE streams are excluded from compression to ensure incremental event delivery. |

## Synthetic data

The synthetic patient generator.

| ID | Requirement |
|---|---|
| `SYNTH-MODULE` | The compiler ships a synthetic data generation module: realistic synthetic healthcare data across multiple standards (FHIR first, generated from measure logic by `SYNTH-PDS-MODULE`), landed on the same `data-contract` so a generated source is indistinguishable from a customer source to the binding path. |
| `SYNTH-PDS-CORRELATED` | The solver instantiates related resources and solves temporal relations by pinning slots, supporting measures with multiple encounters, extension slots, and constraints on value sets or null values. |
| `SYNTH-PDS-DETERMINISM` | All randomness flows from one seed via `(seed, patient, site)` keying. Identical (measure, config, seed) reproduce byte-identical NDJSON and manifest; rows carry no wall-clock timestamp. |
| `SYNTH-PDS-ECQM` | The synthesizer handles multi-library measures with a full multi-library ELM interpreter, solving population logic via declared heuristic strategies and verifying 100% of patients through the full ELM. Amended 2026-10-20: the official measures it was built for were removed; it is re-verified on the measures written for this repository. |
| `SYNTH-PDS-FIXTURE-PINS` | Measure fixtures (ELM and valueset expansions) are generated on the developer's machine and pinned by SHA-256 in `cql_databricks_synth/pins.json`; the cache is rebuilt and verified against the pins at test time. |
| `SYNTH-PDS-IR-CONSUMER` | The synthesizer reads measure semantics from the Compiler's ELM only, walking the same ELM the engine compiles. It fails loud on unsupported constructs rather than approximating. |
| `SYNTH-PDS-MODULE` | The patient data synthesizer generates coverage-directed synthetic cohorts from measure logic: enumerate population strata, solve constraints into skeleton patients, emit FHIR bundles with ground truth known by construction. Python command-line tool, offline, file-based. |
| `SYNTH-PDS-MEASURE-CONFIG` | Added 2026-10-21. A measure's own realism lives in an optional `<Measure>.synth.json` beside its CQL, pinned with it (`SYNTH-PDS-FIXTURE-PINS`): population rates, weights over the branches of an `or` define, a plausible range per declared code or value set, and weights over value-set members. Every key resolves against the measure's ELM or the load fails naming every unresolved key; a weight is positive, so every route and member stays reachable. It applies in the realistic mix only, a rate flag overrides it for one run, and its hash is part of the cohort identity. Adding a measure never edits synthesizer code. |
| `SYNTH-PDS-ORACLE` | Every generated patient carries an expected-results manifest row: the expected value of every define (true/false/null/value), computed by evaluating the bundle through ELM. Rows join against Runtime output for oracle-based validation. |
| `SYNTH-PDS-PRIORS` | Where measure logic leaves a choice open, realistic mode draws it from a distributional prior keyed to FHIR data or terminology, never to measure-specific rates, reweighting choices while keeping every route reachable. Amended 2026-10-21: measure-specific facts (route mix, quantity ranges, member weights) come only from the measure's config (`SYNTH-PDS-MEASURE-CONFIG`); the priors module holds no per-code range table. |
| `SYNTH-PDS-SCALE` | Cohort composition is derived from per-stratum count plans in three mix modes (uniform, realistic, explicit), and generation streams to files bounded by disk rather than memory, with 100% verification inline. Amended 2026-10-21: the realistic mix takes each rate from its flag, else the measure's config (`SYNTH-PDS-MEASURE-CONFIG`), else the default. |
| `SYNTH-PDS-SKIPPED` | A route the solver cannot invert is skipped when a sibling route satisfies the same criterion; skips are recorded per stratum in `StratumOutcome.skipped_routes` and reported as patient counts in `coverage.md`. |
| `SYNTH-PDS-STRATA` | Strata are enumerated by scoring type (funnel first: IPP, denominator, excluded, numerator hit/miss, null membership). Contradictory combinations are reported `unsatisfiable` with conflicting constraint sites. |
| `SYNTH-PDS-SYNTHETIC-TAG` | Every emitted resource carries synthetic markers: `meta.security` set to FHIR security marker plus a generator-version `meta.tag`, so synthesizer data cannot be mistaken for real data downstream. |
| `SYNTH-PDS-TERMINOLOGY` | The synthesizer generates code from the same valueset expansions the runtime binds, so code membership is asserted at generation time and generator and engine never disagree. Amended 2026-10-20: the expansions are the shipped measures' own value sets, not VSAC. |
| `SYNTH-PDS-VERIFY` | After fill, every patient (100%, never a sample) is re-evaluated through the same ELM and must land in its target stratum. Mismatch is a hard failure, never a quiet re-roll. |
| `SYNTH-SEED-OUT` | Data-source seeding leaves the synthesizer; seed scripts move to `scripts/` as a separate loader feature. |

## Results address and compile surface

Where results live and how compiled SQL is shaped for a run.

| ID | Requirement |
|---|---|
| `PIPE-BARRIER-HASH` | SQL hash per materialization barrier determines recompute scope; compiler identity determines recompile scope; UDF identity provides third determinant covering runtime semantic changes. |
| `PIPE-CROSS-SCHEMA-QUALIFY` | When a pipeline's SQL references materialized views owned by different libraries, the BFF fully qualifies those references with `{catalog}.{schema}.{view}` before uploading, handling both `sql_file` (pipeline) and `query` (SQL warehouse). |
| `PIPE-DEPLOY-NO-PLACEHOLDER-ENV` | Deployment never ships unsubstituted `__PLACEHOLDER__` env variables; all substitutions happen at bundle time before any service starts. |
| `PIPE-DRAFT-IDENTITY` | Draft pipeline identity is derived from evaluation parameters (data source, measurement period, custom params), not ELM content, so editing CQL keeps the same pipeline and updates in-place. |
| `PIPE-EMIT-SQL` | The compiler returns complete, deploy-ready SQL for each define in the `sqlFile` field, enabling direct SQL execution without runtime compilation. |
| `PIPE-INCREMENTAL-REUSE` | During authoring, unchanged defines are not recomputed; draft pipelines read from published dependency tables, and the BFF uses selective refresh for changed defines and their descendants only, based on per-barrier SQL hashes. |
| `PIPE-MEASUREMENT-PERIOD` | Measurement period is a parameter to the pipeline run; interactive users select from a dropdown with instant switching between cached periods, and scheduled runs expand a min/max range to individual periods. |
| `PIPE-OWNERSHIP` | All pipelines and result tables are owned by a system security group or service principal, with no per-user ownership; the BFF authenticates as the service principal to create and trigger pipelines. |
| `PIPE-PER-LIBRARY` | Pipeline granularity is one per `(library_name, library_version, data_source)`, so a new published version is a new pipeline and two measures using different versions produce distinct pipelines with no collision. |
| `PIPE-PER-LIBRARY-SCHEMA` | Each published library version writes its own schema `{catalog}.{library}_{version}` with one table per define; CQL that is not published writes to `cql__drafts`; evaluation sets are keyed as rows by `__eval_id`. |
| `PIPE-PIPELINE-IDENTITY` | One `(library, version, data_source)` maps to exactly one pipeline; lookup order is process cache, the metadata store, and adoption of existing pipelines by name (lowest id wins), with deployment serialized per identity. |
| `PIPE-PIPELINE-NAME` | A pipeline is named `cql-databricks-{lib}-{year}-{ver}-{ds}` with the measurement period's year (for readability), but the year is cosmetic and lookup remains keyed on library, version, and data source. |
| `PIPE-PIPELINE-RECOVERY` | A pipeline deleted by accident is recreated reactively at the moment something needs it; recovery re-uploads the stored SQL and full-refreshes every barrier to populate the schema. |
| `PIPE-PRE-COMPUTED` | The Designer serves pre-computed results via `/api/results` from the shared results schema; opening a published measure with cached results is instant. |
| `PIPE-RESULT-DERIVATIONS` | The client (`cql_databricks.result`) computes derived fields from base results (e.g., numerator flag, result type) during calculation rather than storing them separately. |
| `PIPE-UNIFIED` | All measure computation runs through pipelines, producing persistent materialized views; ad-hoc CQL evaluation (probes, scenarios) runs in-process via the OSS client and materializes nothing. |
| `PIPE-VERSION-AS-SCOPE` | Results are keyed by `(library_version, data_source)`: published versions produce shared results, draft versions produce private results known only to the author. |

## Running calculations

Statements, fingerprints, durable calculations and drivers.

| ID | Requirement |
|---|---|
| `CLIENT-DISCOVERY-SCOPE` | Clients discover data sources, knowledge, and results in an ordered list of catalog scopes; the earliest scope containing a name wins, and every query searches only the specified scopes. The default scope is `*` (every catalog the caller can see). Results are written only to the caller's destination and read from the destination first, then from the `catalog.schema` entries of the scopes in order; whole catalogs and `*` are never read for results. The scopes are the trust boundary: a result in a listed schema is reused when it is the same calculation, compiler and shape over the same upstream generations, with no check of its table owner or publisher. A shared result schema grants write access only to its publishers (`DEC-RESULT-SCOPE`, amended 2026-10-11). Amended 2026-10-11 by `RUN-CLIENT-RESULT-IDENTITY`: a result in a listed schema is found by its table name, which is its identity, and reused when its fingerprint tag matches; there is no calculation-hash lookup, compiler or shape check, or upstream-generation check. |
| `RUN-CALC-SERVICE` | Goal, not built: a managed calculation service computes measures on behalf of callers and handles orchestration and lifecycle. |
| `RUN-CLEANUP` | A scheduled maintenance Job deletes session jobs idle beyond seven days, selected by tags, never while running. |
| `RUN-CLIENT-ASYNC-CHECKPOINTS` | Bound background tag work; invalidate synchronously, respect completion ordering, drain on exit and propagate errors. |
| `RUN-CLIENT-CATALOG-CHECKPOINTS` | Store compact per-table UC success tags with computation-start time and fingerprint; bulk discovery and synchronous invalidation. |
| `RUN-CLIENT-COMPLETION` | Publish reuse evidence only after successful physical write; pin artifacts, reject partial results, separate execution state from discovery. Amended 2026-10-11 by `RUN-CLIENT-RESULT-IDENTITY`: a reused result is no longer pinned to the generations of the results it read; freshness is the data source's (`DEC-RESULT-IDENTITY`). |
| `RUN-CLIENT-DURABLE` | Durable materialization is explicit and caller-authorized; ordinary notebook execution retains its released behavior. |
| `RUN-CLIENT-FACTORED-IDENTITY` | Separate discovery, exact compatibility/storage identity and execution provenance; expose bounded scalar metadata without confusing completion time with freshness. SUPERSEDED 2026-10-11 by `RUN-CLIENT-RESULT-IDENTITY`: one identity, the fingerprint, serves discovery, compatibility and the table name; the calculation and execution hashes are removed, and `search_results` filters only by fingerprint and completion time. |
| `RUN-CLIENT-IDENTITY` | Versioned identity covers definition logic, bound addresses, parameters, compiler version and owner scope; unrelated definitions do not invalidate results. SUPERSEDED 2026-10-11 by `RUN-CLIENT-RESULT-IDENTITY`: the identity no longer covers the destination, the owner or the compiler version, so one calculation is reused across schemas and users; a compiler change that matters changes the emitted SQL. |
| `RUN-CLIENT-LIFECYCLE` | Automatic successful-computation-age expiry replaces manual archive; private and published storage have separate lifecycles. |
| `RUN-CLIENT-MAINTENANCE` | One daily `cql-maintenance` Job expires completed private results across users after configurable retention by UC tags. The deploy owns the Job (`databricks.yml`, `scripts/jobs/maintenance.py`) and the principal it runs as (`scripts/deploy/maintenance_principal.py`); the expiry is `cql_databricks.result.maintenance`. The schedule deploys paused until a run is checked on the workspace. |
| `RUN-CLIENT-MIGRATION` | Keep the bindings-into-client move; migrate callers and preserve released contracts before retiring paths. |
| `RUN-CLIENT-OPTIMISTIC` | Trust completion metadata and owner-serialized writes; actual SQL errors propagate instead of defensive verification. |
| `RUN-CLIENT-PRIVATE-PROVISIONING` | Private results catalog is `designer_results_catalog`; Apps get catalog-level privileges only, per-schema for data. |
| `RUN-CLIENT-PUBLISHED` | Publishing is the implementer's, not this repository's: it ships no publishing Job or schedule. A result's UC tags are the contract. An implementer's Job that publishes shared definitions tags them, and other clients discover and reuse them read-only under their grants. |
| `RUN-CLIENT-REFRESH` | Refresh an owner-scoped calculation in the same Delta table, retain only current completion, preserve pinned versions within retention. Amended 2026-10-11 by `RUN-CLIENT-RESULT-IDENTITY`: the calculation is no longer owner-scoped; its identity carries no owner. |
| `RUN-CLIENT-REFRESH-RESUME` | Explicit recompute saves one UTC computed-since cutoff; retries reuse matching defines completed since it. |
| `RUN-CLIENT-REQUEST-REOPEN` | Reconstruct the request, batch compile, discover UC completion tags; current-table reads use caller grants. |
| `RUN-CLIENT-RESULT-IDENTITY` | Replaces `RUN-CLIENT-IDENTITY`. A result's identity, and the fingerprint digits in its table name, is one hash of: an identity format version, the define's emitted SQL, the parameter values it uses, the identities of the defines it reads, and the address of the bound data source. The destination, the user, the compiler version, the library and define names and the population label are not part of it; the library and define appear only in the readable part of the table name (`RUN-CLIENT-TABLE-NAMES`). So one calculation has one table name in every schema, and a result in another scope is found by that name (`CLIENT-DISCOVERY-SCOPE`). Data freshness is the data source's: it serves as-of data. A release that changes the runtime functions tells callers to refresh everything. A non-deterministic define is never reused (`DEC-RESULT-IDENTITY`). The hash is the `fingerprint`, and a result table carries four tags: `cql_result_format`, `cql_result_state`, `cql_computed_at` and `cql_fingerprint`. The calculation, upstream, shape, publisher, compiler, library, version, define and scope hashes and the generation tag are dropped. The compiler computes the fingerprint (`PIPE-DURABLE-IDENTITY`); the address is the bound data source's `catalog.schema`, so a durable compile that retrieves data must bind a tagged data source, not an explicit per-type binding or the bare `retrieve_<Type>` convention. Results stored before this identity are a different result format and are computed again once. |
| `RUN-CLIENT-RESULT-SETS` | Designer uses per-user schemas and session-scoped completions; shared readers use recorded metadata and exact artifact versions. Amended 2026-10-11 by `RUN-CLIENT-RESULT-IDENTITY`: readers match results by identity, not by exact upstream artifact versions; a selection's address names its destination, not a publisher or population. |
| `RUN-CLIENT-RESUME` | Extend calculations in the same session and resolve authorized results in new sessions without depending on Python objects or caches. |
| `RUN-CLIENT-REUSE` | Completion metadata and matching inputs govern reuse; skip if computed and recompute are the only modes. |
| `RUN-CLIENT-TABLE-NAMES` | Result tables are named `<library>_<define>_<first 16 fingerprint hex digits>`: lowercase, digits and single underscores only. |
| `RUN-CONTEXT-BY-ID` | Goal, not built: a request names its patient context as one or more subject IDs or the whole data source. |
| `RUN-EVAL-ID-INPUTS` | Result table columns include `__eval_id` marker and input fingerprints for debugging; future enhancements can populate data term columns. |
| `RUN-EVENTS` | The executor records each dataset attempt with run id, fingerprint, decision and timestamps. |
| `RUN-EXECUTOR` | Datasets start execution when their upstream dependencies have committed, not level-by-level, with per-warehouse concurrency caps and transient failure retries. |
| `RUN-JOB-OWNER` | Every run uses the service principal's identity; the requester check is performed on the durable calculation request before submission. |
| `RUN-JOB-RECOVERY` | A Job deleted out of band is recreated, not treated as a permanent error. |
| `RUN-LEASE` | Durable evaluation identity is independent of whether a lease table exists; correctness depends only on deterministic fingerprinting of inputs. |
| `RUN-LINEAGE` | Unity Catalog captures lineage per query for SQL interfaces and `CREATE TABLE AS SELECT`. |
| `RUN-NOTHING-TO-DO` | A run is skipped only when no barrier SQL changed and every result table exists and is current. |
| `RUN-ONE-WRITER` | One writer per evaluation; a new run cancels the old and waits for in-flight statements to finish. |
| `RUN-POINT-CALC` | Goal, not built: single-patient lookup returns precomputed answers; single-patient calculation runs the same SQL as batch with one patient. |
| `RUN-PROGRESS-TASKS` | The Designer's progress gauge shows the fraction of barrier tasks in a terminal state. |
| `RUN-PROVENANCE` | Every result table is stamped with library, version, define, data source and period in tags and schema comments. |
| `RUN-READ-BARRIER` | A filtered retrieve becomes a materialized barrier with its own table, task and content-addressed name. |
| `RUN-RECOMPUTE-OVERRIDE` | Setting `recompute: true` on a run request forces full evaluation of the entire closure regardless of cache state. |
| `RUN-RESULTS-BY-EVAL` | Result table shape is standardized: one table per evaluation set, clustered by `(__eval_id, __subject_id)`, containing `__eval_id` marker for REPLACE semantics. |
| `RUN-RESULTS-WRITER` | Only the evaluator's service identity writes to the results store, and only through versioned evaluation logic. |
| `RUN-SCHEMA-NAME` | Result table names encode library and data source; multiple evaluations can write into one schema under distinct result tables addressed by evaluation ID. |
| `RUN-STANDING-COST` | Goal, not built: a standing request serves multiple subscribers from the evaluator's warehouse without charge splits. |

## Library registry

Measures and libraries as governed, versioned content.

| ID | Requirement |
|---|---|
| `REG-CONFORMANCE-DECK` | Conformance decks carry golden results as MeasureReports with per-subject population counts. Validate computed results against the deck and stamp a pass/fail receipt (not built). Amended 2026-10-21: the shipped decks are the measures written for this repository, generated from the conformance corpus by `loader/importer/scripts/generate_decks.py` with their CQL unchanged; each per-patient count is the population define's value from the hand-written answer key. SUPERSEDED 2026-10-21 by `CONTENT-SAMPLE-PRELOAD`: decks existed only for published test content. The sample measures ship in `examples/`, with their expected results per patient beside the sample patients. |
| `REG-CRUD` | Draft → validate (clean diagnostics) → publish (immutable, indexed) → fork; performed as the signed-in user. |
| `REG-CURATION` | Seed marketplace with trusted publishers (standards bodies, quality vendors, centers of excellence); open publication later once community trust is load-bearing. |
| `REG-DISCOVERY` | Search/browse the Delta index by name/tags/resources/value sets/author/status; no separate search service. |
| `REG-PACK-DATA` | A Measure Pack bundles conformance data — FHIR test fixtures — as a (deck, version) slice of the shared corpus. Install via MERGE; uninstall via DELETE. SUPERSEDED 2026-10-21 by `CONTENT-SAMPLE-PRELOAD` as far as it is decks: no pack ships conformance decks. |
| `REG-PACKS` | Clinical intelligence bundles measures + libraries (CQL/ELM), value sets, and authoring instructions; versioned, forkable, rateable. |
| `REG-REGISTRY` | Libraries are CQL source plus metadata and discovery index; governed by Unity Catalog. |
| `REG-SOURCE-PROVIDER` | One registry-backed LibrarySourceProvider resolves includes and hands the closure to the engine as input; one resolution path, no gap between publishing and runtime. |
| `REG-VERSION` | Identity is (name, version); closure resolved for compilation; versions immutable. |

## Identity and governance

Who a request runs as, and how Unity Catalog governs it.

| ID | Requirement |
|---|---|
| `SEC-APP-TO-APP` | BFF reaches Compiler Service App with this process's own Databricks identity; read off shared WorkspaceClient so SDK owns caching and refresh. Gate is in front of every path. |
| `SEC-GOVERNANCE` | Bindings, value-set tables, the registry, and shared objects are ordinary Unity Catalog objects; access control, lineage, and audit are Unity Catalog's. |
| `SEC-LOCAL-AUTH-PREFLIGHT` | Local runs settle Databricks auth before stack starts via `databricks auth token` (OS-keyring cache); interactive login runs only on failure. |
| `SEC-LOCAL-IDENTITY` | Local target uses a single dev identity and local catalog (no OAuth on-behalf-of) — a dev convenience, not a production posture. |
| `SEC-OBO` | Designer data-source lookups and Bind action run as the signed-in user via OAuth on-behalf-of. The BFF builds a per-request user client from the forwarded token; service-principal client handles App plumbing and startup. Target detection, not fallback: missing token raises. |
| `SEC-OBO-SCOPE-GAP` | Jobs and pipelines scopes are not currently declarable in user_api_scopes. Interactive runs use service-principal client for orchestration. This is a documented cost, not mitigated. |
| `SEC-WS-CLIENT-SHARED` | One Databricks SDK WorkspaceClient per process, built lazily behind a lock; reached only through service-principal-client. Never cached per-call; refresh is single and serialized. |

## Accelerator and compiler integration

How the rest of the system uses the compiler.

| ID | Requirement |
|---|---|
| `ACC-BRAND-NAMING` | Designer in visible labels, code, headers, paths, docs, and requirement IDs. Full terminology migration explicitly approved. CQL Builder canonical naming: product/repo = cql-databricks; components cql-databricks-{compiler,runtime,app}. |
| `ACC-ENGINE-CLIENT` | The accelerator is a client of the compiler. It never re-implements CQL semantics or edits emitted SQL by hand. |
| `ACC-ENGINE-DEP` | Compiler is an ordinary project dependency (project(':compiler')) in a single Gradle build; neutrality is enforced mechanically by checkNeutrality. |
| `ACC-KA-LAYERS` | Adopt HL7 CPG separation of concerns: Pathway (PlanDefinition), Care Plan (CarePlan), Case+Features (FHIR events; CQL define), Measurement &amp; Prediction, Workflow (delivery, out of scope). Each capability declares its layer(s). |
| `ACC-KA-TRACE` | Traceability across KA layers is first-class: navigate lineage down (to patient Case), up (to Pathway), and across (features shared by measurement and prediction). |
| `ACC-KNOWLEDGE-LEVELS` | Content follows the HL7 CQF knowledge levels: L1 narrative, L2 semi-structured, L3 structured (FHIR Library, CQL, ELM), L4 executable (compiled SQL). A measure links its levels. |
| `ACC-NAMESPACE-ALIGN` | Accelerator code under com.databricks.cql.platform with distinct product tokens; engine rename to add com. prefix is engine-led. |

## Content

Importing, storing and resolving eCQM content and value sets.

| ID | Requirement |
|---|---|
| `CONTENT-BAKE-AUTHORITY` | Content-ingest schemas baked in (one per content source; VSAC → vsac); not parameterized. Schema names are defaults. Additional schemas added as named constants. Amended 2026-10-21: the eCQI import's defaults are `ecqi_measures` and `vsac`; the samples land in `sample_measures` (`CONTENT-SAMPLE-PRELOAD`). |
| `CONTENT-CATALOG-LAYOUT` | Authority-segregated schemas: catalog.&lt;authority&gt;.{measure, library, valueset_ref} for measure authorities; catalog.vsac.{valueset} for VSAC. |
| `CONTENT-ECQI-PIN` | The repository kept a tracked list of eCQI packages to fetch. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-USER-PACKAGE`: the repository keeps no list of measures; the user names the package. |
| `CONTENT-ECQI-TERMS-GATE` | Before any download, the eCQI import shows the third-party terms and requires typing `I AGREE`; after the download, it shows each Measure's own `copyright` field verbatim and requires `I AGREE` again before any content is loaded. Anything else cancels. A non-interactive run must pass `--accept-terms`, which still shows both texts; the acceptance is logged with the package, its SHA-256, the local user and the time. The terms text is a draft pending legal review. Added 2026-10-21. |
| `CONTENT-ECQI-USER-PACKAGE` | The user names the eCQI package to import, by its eCQI file name or a local zip. The repository holds no pinned list, no lock and no tracked measure list. Added 2026-10-21. |
| `CONTENT-ECQI-LOAD-UNCHANGED` | An imported package's `Measure` and `Library` resources are loaded as shipped: no Measurement Period edit, no stripping, no relabelling. Added 2026-10-21. |
| `CONTENT-ENV-CONSOLIDATE` | Single root .env file or databricks.local.yml for workspace settings; .env keeps local-run settings and secrets. Per-component .env files eliminated. |
| `CONTENT-FHIR-VARIANT` | Knowledge tables store each FHIR resource in one `VARIANT` column, so nothing is lost and no schema is generated. A typed struct is not used: the Measure resource alone has thousands of fields. |
| `CONTENT-FIXTURE-ONE-PER-SUBJECT` | Test-case identity is (measure directory, patient id); keep exactly ONE bundle per key. De-duplicate, do not restrict: all layout paths used. Bundle with no Patient ID is reported separately. SUPERSEDED 2026-10-21: the importer no longer loads test cases. |
| `CONTENT-IG-ENGINE-ALIGN` | Content-ingest and the engine read the same upstream implementation-guide repository. SUPERSEDED 2026-10-21: nothing is fetched from an implementation-guide repository. Value-set expansions come from VSAC with the user's own key (`CONTENT-VSAC`). |
| `CONTENT-IMPORT-TARGET-TAG` | Importer loads into named catalog.schema and tags it cql_databricks_knowledge / cql_databricks_terminology. --tag-only tags an earlier load. Tags need Unity Catalog. |
| `CONTENT-IMPORTER-FHIR-SCHEMAS` | Importer owns its FHIR resource schemas (content_ingest/resource-schemas.json). Compiler's dumpResourceSchemas no longer overwrites it; retrieve UDTF adapts, so they can drift without breaking runs. |
| `CONTENT-INCREMENTAL` | MERGE on (url, version) for Measure/Library (upsert). Delete-and-append by source_package for ValueSet references. Re-runnable without data loss. |
| `CONTENT-INGEST` | Standalone ETL tool fetches publicly available FHIR Measure/Library resources and loads into Unity Catalog; zero dependency on app or engine. One IG source and one content schema named for it. Amended 2026-10-21: two inputs, the sample content in `examples/` (`CONTENT-SAMPLE-PRELOAD`) and one eCQI package the user names behind its terms (`CONTENT-ECQI-TERMS-GATE`). There is no IG source. |
| `CONTENT-LAKEHOUSE-STANDARD-ONLY` | Published tables are standardized FHIR resources or do not belong. Junctions and denormalizations are derived downstream. Removes measure_library and valueset_ref from published schema. Valueset keeps tall shape (engine binding contract). |
| `CONTENT-MARKETPLACE-READY` | Table layout and provenance columns designed for eventual Databricks Marketplace publishing. |
| `CONTENT-MEASURE-LIBRARY-LINK` | The link from a Measure to its root CQL Library is derived from the Measure resource when read. It is an index with no truth of its own. |
| `CONTENT-MEASURE-NAME-PUBLISHED` | Corpus stores the published measure name read from the package header. Fixtures found by draft name (pre-publication), stored under published name. Pins stay the single place the rename is recorded. SUPERSEDED 2026-10-21: there are no fixtures and no pins, so there is no rename to record. |
| `CONTENT-NO-DERIVED-COLUMNS` | A knowledge table holds the resource, its identity and its provenance only. `url` and `version` are generated columns. Anything else derived from the resource is computed when read. |
| `CONTENT-PIN-ASSERT` | Fetch aborts unless (a) every CQL file header agrees with its filename, and (b) root library matches pin's name and version exactly. Catches version lies. Every problem reported, not the first. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-USER-PACKAGE`: there is no pin to assert against. The package is loaded as shipped (`CONTENT-ECQI-LOAD-UNCHANGED`). |
| `CONTENT-PIN-IG-TEST-DIR` | MeasurePin records ig_test_dir (measure directory under IG repo). Required, not optional. IG archive names fixtures with pre-publication name; published names are renamed by hand; the pin records the rename. SUPERSEDED 2026-10-21: there are no pins. |
| `CONTENT-PIN-LOCAL-EDIT` | Fetched packages carried one documented local edit: binding the measurement-period parameter default. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-LOAD-UNCHANGED`: no edit at all; the measurement period is a run-time parameter (`LANG-PARAMETER`). |
| `CONTENT-PIN-LOCK` | A tracked lock file recorded the hashes of fetched eCQI packages. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-USER-PACKAGE`: there is no tracked lock. |
| `CONTENT-RESOLVER` | ContentResolver finds all tagged schemas (knowledge, terminology, data-source) in caller's ordered catalog scopes in one query; earliest scope wins for duplicates. Data-bindings package in compiler/client/; shipped in cql-databricks wheel. |
| `CONTENT-RUN-ALL-DEFAULT` | Content-ingest runs everything by default (measures, bundled expansions, test cases). Skip flags opt out. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-USER-PACKAGE`: the import loads the one package the user names, and its value sets unless `--no-valuesets`. |
| `CONTENT-SAMPLE-PRELOAD` | `examples/` is the one place that pre-loads a workspace with sample measures. The deploy loads its CQL as FHIR `Library` resources and each sample measure's authored `Measure` resource into tagged knowledge (`sample_measures`), its value sets into tagged terminology in the same schema, and its patients as `DATA-SAMPLE-SOURCE`. It never uses the eCQI path, and nothing outside `compiler/` reads the compiler's conformance corpus: the two copies of a measure are independent on purpose. Added 2026-10-21. Amended 2026-10-21: the conformance corpus keeps a copy of each sample measure (its CQL, patients, value sets and expected results as an answer key). For now the copies are the same and are changed in lock step, by hand. Nothing reads one from the other, so they may diverge later. |
| `CONTENT-SAMPLE-STANDARDS` | The sample measures in `examples/` follow CMS and ONC quality measurement standards and use only open-source, license-free content. Each is written against QI-Core 6.0.0 and includes FHIRHelpers 4.0.1, which `examples/measures` distributes unmodified. Each has an authored FHIR `Measure` with proportion scoring, the CQF Measures populations (initial population, denominator, exclusions, exceptions, numerator), improvement notation and supplemental data, and takes the measurement period at run time. Its value sets are the repository's own, with codes only from code systems free to use (ICD-10-CM, ICD-10-PCS, HCPCS Level II, LOINC under its license, CVX, HL7 terminology) or local codes: no CPT, no SNOMED CT, and no published measure, value set or library. Added 2026-10-21. |
| `CONTENT-SHARE-FAITHFUL` | Published knowledge stays faithful to FHIR spec including where inconvenient (base64 stays base64). Ergonomics live downstream of the share. Consumers derive optimized forms. |
| `CONTENT-VSAC` | Fetches ValueSet expansions from NLM VSAC FHIR API; writes valueset(valueset_id, system, code) matching engine binding contract. Writes to VSAC authority schema. Amended 2026-10-21: the import expands the value sets the package's libraries name, with the user's own `UMLS_API_KEY`, and a value set that fails to expand fails the import. |
| `CONTENT-VSAC-AUTHORITY` | ValueSet expansions in own authority schema (catalog.vsac) to avoid duplication when multiple programs reference same ValueSets. valueset_ref stays with measure authority. |
| `KNOWLEDGE-CORPUS-REFRESH` | Measure resources load lazily on first access behind an LRU cache; library metadata loads eagerly and refreshes on Delta version movement. Manual reload is available via API. |
| `KNOWLEDGE-FAIL-LOUD` | Knowledge read failures propagate rather than returning empty results; failed loads are retried on next access and never cached. |
| `KNOWLEDGE-FEED-INGEST` | Lakehouse and serving tables are publisher and consumer; seam is knowledge feed over OpenSharing, not internal detail. Any recipient runs same derivation step. Published side optimizes for faithfulness; consumer side for use. |
| `KNOWLEDGE-LAKE-DIRECT` | The BFF reads knowledge library and measure metadata directly from Lakehouse tables in one eager batch load held in memory, eliminating the prior sync job. |
| `KNOWLEDGE-ORIGIN` | Every Data Registry entry carries an origin: `authored` (we are the authority, mutable, in our registry) or `external` (customer-owned, read-only, backed by a view in their lake). `authored` subsumes the old user class; `external` subsumes live-source bindings. |
| `KNOWLEDGE-RESOLVE-API` | Measure/Library resolve through one resolver composing external content by reference, never by copy. The notebook client and the app resolve byte-identically from tagged knowledge, feeding the returned closure ELM into the engine as `deps`. Amended 2026-10-20: an include that pins a version resolves only to that version. Another version of the same name never stands in; a missing pin is reported unresolved. |
| `TERMINOLOGY-STANDARDIZE` | Knowledge sources (not locations) carry Measure/Library/ValueSet bindings. Test Deck is read-only. Data Sources bind retrieval. Tabs show Knowledge Sources paired with Data Sources as one choice between two things of a kind. |

## Retired

| ID | Status |
|---|---|
| `ACC-ENGINE-RESOLUTION` | Retired: monorepo merge dissolved the problem. See `overview.md` section 3.1. |
| `APP-ANALYZE-LENS` | Retired: superseded by `APP-ANALYTICS`. |
| `APP-ASSISTANT-PANE` | Retired: the right pane holds data only, not conversation. See `APP-ASSISTANT`. |
| `APP-DATA-GAPS` | Retired: the module and its route are deleted. See `DESIGNER-ONE-MODULE`. |
| `APP-DATA-READINESS` | Retired: the Data Readiness module and its screen are deleted. See `DESIGNER-ONE-MODULE`. |
| `APP-DQ-SCORECARD` | Retired: the scorecard was part of Data Readiness and is deleted with it. |
| `APP-FLOW-SPINE` | Retired: the visual flow navigation was removed; populations are accessed through the nav. See `APP-SPEC-MODEL`. |
| `APP-IMPACT` | Retired: impact moved to the Data tab; dependency counts are shown inline on nav rows. See `APP-DATA-PROFILE`. |
| `APP-LENSES` | Retired: Designer is one surface and the lens set is withdrawn. |
| `APP-LOGIC-LENS` | Retired: the Logic lens and its force-directed DAG are removed; logical structure is read through the Define lens. |
| `APP-MAP-LENS` | Retired: the Map lens as a lens is retired; its content moves into Validate. |
| `APP-MEASURE-FLOW` | Retired: the flow tab is gone. Its counts live on in `APP-COHORT-ALGEBRA` and `APP-DECISIVE-COUNT`. |
| `APP-MODULE-SWITCHER` | Retired with SDP. Designer is the one module. |
| `APP-MODULES` | Retired: superseded by single-module Designer architecture. See `DESIGNER-ONE-MODULE`. |
| `APP-NARRATIVE-SIDECAR` | Retired: the JSON sidecar was replaced by XSD-conformant Specification instances. See `APP-NARRATIVE-TAGS`. |
| `APP-SAMPLE-METRICS` | Retired: the document shows real figures from result tables. See `APP-SPEC-MODEL`. |
| `APP-SCHEDULING` | Retired: Scheduling is removed from the Designer. |
| `APP-SETTINGS` | Retired: Settings screen removed; deploy auto-binds data sources. See `APP-NO-UI-MOCK`. |
| `APP-VALIDATE-LENS` | Retired: the Validate lens as a lens is retired; readiness content lives in the Data Readiness module. |
| `CONTENT-FHIR-IG` | Retired: IG packages are no longer fetched from CI builds. See `CONTENT-IG-ENGINE-ALIGN`. |
| `CONTENT-STRUCT-RENDER-FIDELITY` | Retired: resources are stored as `VARIANT`, which drops nothing. See `CONTENT-FHIR-VARIANT`. |
| `CONTENT-STRUCT-ROUNDTRIP` | Retired: resources are stored as `VARIANT`, which drops nothing. See `CONTENT-FHIR-VARIANT`. |
| `DATA-BIND-AUTO` | Retired: Designer bind button and auto-reload removed. |
| `DATA-REGISTRY-RENAME` | Retired: Data Source record naming superseded by UC tag reading. |
| `DATA-SCOPED-VIEW-ADVISOR` | Retired: Proposed future feature. Not committed. |
| `DATA-SOURCE-REGISTRY` | Retired: Superseded by UC tags. See `DATA-SOURCE-UC-TAG`. |
| `DATA-SYNTH-1M` | SUPERSEDED 2026-10-11 by `DATA-SYNTH-LARGE`: renamed, because the population grows with each measure's cohort, so a count in its name goes stale. |
| `DATA-SYNTHEA-SOURCE` | SUPERSEDED 2026-10-11 by `SYNTH-PDS-MODULE`: the synthesizer generates the synthetic sources from measure logic, and nothing used Synthea. The importer's Synthea path and the Synthea seeder are removed. |
| `DEPLOY-SEED-KNOWLEDGE` | Retired: content importer tags sources instead of seeding rows. See `CONTENT-IMPORT-TARGET-TAG`. |
| `DESIGNER-COMMAND-PALETTE` | Retired: finding a define is the Open menu. See `DESIGNER-TOP-BAR`. |
| `DESIGNER-PUBLISH-GATE` | Retired with the Data Readiness module. See `DESIGNER-ONE-MODULE`. |
| `DESIGNER-SDP-DIAGNOSTICS` | Retired with SDP. |
| `DESIGNER-SPACE-CONTEXT` | Retired: data sources are found by UC tag. See `DATA-SOURCE-UC-TAG`. |
| `EVALUATOR-CLIENT-EVALUATE` | Retired: the evaluator engine plugin is removed with the App. See `RUN-CLIENT-DURABLE`. |
| `KNOWLEDGE-LAKEBASE-ALIGN` | Retired: derivations moved to memory; Lakebase tables no longer defined. See `KNOWLEDGE-LAKE-DIRECT`. |
| `KNOWLEDGE-SYNC` | Retired: direct Lakehouse read replaced the sync job and cron. See `KNOWLEDGE-LAKE-DIRECT`. |
| `MOUNT-MEASURE-NAME-MAP` | Retired: the published name is stored at ingest. See `CONTENT-MEASURE-NAME-PUBLISHED`. |
| `PHASE-S0` | Retired: old roadmap phase. Foundations + hosting de-risk (scaffold + local JVM engine transport). Transport now standalone CQL Compiler Service. |
| `PHASE-S1` | Retired: old roadmap phase. Authoring core: editor + on-demand diagnostics, local and as App. |
| `PHASE-S2` | Retired: old roadmap phase. Data binding + runtime spine: run real measure end to end. |
| `PHASE-S3` | Retired: old roadmap phase. Marketplace/registry: publish/discover/fork/run with unified include resolution. |
| `PHASE-S4` | Retired: old roadmap phase. Single pane: plan debug, per-define preview, data quality. |
| `PHASE-S5` | Retired: old roadmap phase. Hardening, perf, optional GraalVM native-image. |
| `PHASE-S6` | Retired: old roadmap phase. Databricks App fit + deployment; OpenSharing activation. |
| `PIPE-DEPLOY-CATALOG-VARS` | Retired: Designer catalog variables removed with Designer. |
| `PIPE-ELM-ORIGIN` | Retired: Pipeline metadata tracking removed with Lakeflow pipelines. |
| `PIPE-ENZYME` | Retired: Lakeflow Enzyme pipeline infrastructure removed. See `PIPE-BARRIER-HASH`. |
| `PIPE-INCREMENTAL-NO-ENZYME` | Retired: Investigation into non-Enzyme incremental refresh concluded. |
| `PIPE-JOB-ACCUMULATE` | Retired: Pipeline job accumulation logic removed with Lakeflow. |
| `PIPE-JOB-IDENTITY` | Retired: Job scheduling no longer created. See `DEC-RETIRE-SCHEDULE-JOB`. |
| `PIPE-JOB-ORCHESTRATION` | Retired: Scheduled job execution superseded. See `RUN-SERVICE-DRIVER`. |
| `PIPE-LIFECYCLE` | Retired: Draft pipeline lifecycle and cleanup removed with pipelines. |
| `PIPE-PROGRESS-CHIP` | Retired: Pipeline progress UI removed with Designer and pipelines. |
| `PIPE-RECOMPILE-GATE` | Retired: Pipeline recompile gating superseded by PIPE-BARRIER-HASH determinants. |
| `PIPE-RESULT-NAMING` | Retired: Table naming convention revised. See `PIPE-PER-LIBRARY-SCHEMA`. |
| `PIPE-RESULTS-CATALOG-ISOLATION` | Retired: Shared results catalog variable removed with Designer. |
| `PIPE-RESULTS-SCHEMA` | Retired: Schema structure revised. See `PIPE-PER-LIBRARY-SCHEMA`. |
| `PIPE-SDP` | Retired: SDP and Lakeflow pipeline orchestration removed. |
| `PIPE-SERVICE` | Retired: Pipeline orchestration service was future work, not implemented. |
| `PIPE-STATE-LAYER` | Retired: Lakebase pipeline state layer rebuilt into cache logic. See `PIPE-BARRIER-HASH`. |
| `RUN-ADDRESS-BY-LIBRARY` | Retired: the evaluator App's draft sweeper and address API are removed. See `RUN-CLIENT-RESULT-SETS`. |
| `RUN-CLIENT-BATCH-METADATA` | Retired: bulk discovery remains. See `RUN-CLIENT-CATALOG-CHECKPOINTS`. |
| `RUN-CLIENT-OPERATIONS` | Retired: publishing and scheduling are the implementer's. See `RUN-CLIENT-PUBLISHED`. |
| `RUN-CLIENT-PORTABLE-HANDLE` | Retired: request reopening no longer uses compiled-plan handles. See `RUN-CLIENT-REQUEST-REOPEN`. |
| `RUN-DAG-EXECUTOR` | Retired: App statement executor and warm session pool removed. |
| `RUN-DEPLOYMENT-CONFIG` | Retired: Deployment orchestration removed; calculations are data-only. |
| `RUN-DRAFT-RESULTS` | Retired: Designer draft results feature removed with Designer. |
| `RUN-ELM-HISTORY` | Retired: the evaluator App's history store is no longer written. See `RUN-READ-BARRIER`. |
| `RUN-FINGERPRINT` | Retired: Evaluator App fingerprint and Lakebase state removed. |
| `RUN-FINGERPRINT-ORDER` | Retired: Fingerprint ordering assumption removed with App. |
| `RUN-INTERACTIVE-AS-USER` | Retired: Interactive ad-hoc requests handled via compiler client. |
| `RUN-POINT-CLIENT` | Retired: the point client is removed. See `EVALUATOR-RETIRE`. |
| `RUN-POINT-CQF` | Retired: the point service is removed; the CQF engine (`compiler/cqf`) remains the conformance oracle. See `EVALUATOR-RETIRE`. |
| `RUN-SCAN-BARRIER` | Retired: renamed to `RUN-READ-BARRIER`. See `RUN-READ-BARRIER`. |
| `RUN-SCHEDULE-CURRENCY` | Retired: there are no schedules to refuse. See `RUN-CALC-SERVICE`. |
| `RUN-SCHEDULE-JOB` | Retired: Designer scheduled Job feature removed. |
| `RUN-SERVICE-DRIVER` | Retired: Evaluator App service removed with EVALUATOR-REMOVE. |
| `RUN-SESSION-JOB` | Retired: Designer runs execute in the evaluator App, not as session Jobs. See `RUN-ONE-WRITER`. |
| `RUN-SKIP-IN-TASK` | Retired: Per-statement currency check superseded by table state. |
| `RUN-STATEMENT-EXEC` | Retired: Barrier statement execution removed with evaluator App. |
| `SDP-ALL-DEFINES` | Retired with SDP. |
| `SDP-DEPLOY-ADDRESS` | Retired with SDP. |
| `SDP-DEPLOYER` | Retired with SDP. |
| `SDP-DESIGNER-RUN` | Retired with SDP. |
| `SDP-DIAGNOSTICS` | Retired with SDP. |
| `SDP-EXECUTION` | Retired with SDP. |
| `SDP-LIBRARY-BUNDLE` | Retired with SDP. |
| `SDP-LIBRARY-RUNNER` | Retired with SDP. |
| `SDP-LIVE-STATUS` | Retired with SDP. |
| `SDP-MEASUREMENT-PERIOD` | Retired with SDP. |
| `SDP-MV-NAMING` | Retired with SDP. |
| `SDP-NO-ENZYME` | Retired with SDP. |
| `SDP-PERIOD-REFRESH` | Retired with SDP. |
| `SDP-PERIOD-STATUS` | Retired with SDP. |
| `SDP-PIPELINE` | Retired with SDP. |
| `SDP-PLAN-PUBLISH` | Retired with SDP. |
| `SDP-SPACE` | Retired with SDP. |
| `SPEC-INDEX-TREE` | Retired. See `SPEC-NAV-COMPOSITION`. |
| `SPEC-LIBRARY-DENSITY` | Retired: every library renders in full and the reader collapses it. See `APP-SPEC-COLLAPSE`. |
| `SPEC-READ-METRICS` | Retired: retrieves are not materialized, so there are no read counts. See `SPEC-READ-REF`. |
| `SVC-COMPILER-UC` | Retired with SVC-COMPILER-M2M. |
| `SVC-EVALUATOR-CLIENT` | Retired with evaluator App redesign. |
| `SVC-EXECUTION-JOBS` | Retired with SDP. |
| `SVC-LAKEBASE-CONFIG` | Retired with Designer Lakebase. |
| `SVC-LAKEBASE-POOL` | Retired with Designer Lakebase. |
| `SVC-PARTNER-SDK` | Retired; replaced by SVC-PLATFORM-API. |
| `SVC-POINT-APP` | Retired: the point App is removed. See `EVALUATOR-RETIRE`. |
| `SVC-SDP-PACKAGE` | Retired with SDP. |
| `SVC-SPIKE` | Retired; replaced by standalone Compiler Service App. |
| `SVC-TOOL-ADDRESS` | Retired with evaluator App redesign. |
| `SVC-TOOL-FRESHNESS` | Retired; replaced by RUN-CLIENT-REFRESH. |
| `SYNTH-DETERMINISM` | Retired: Proposed, see `SYNTH-PDS-DETERMINISM`. |
| `SYNTH-FORMAT-EMITTER` | Retired: Proposed multi-format support. Not implemented. |
| `SYNTH-MEASURE-PROFILE` | Retired: Proposed generation profiles. Not implemented. |
| `SYNTH-REALISM` | Retired: Proposed realism layer. Not implemented. |
| `SYNTH-ROUNDTRIP` | Retired: Proposed cross-format parsing. Not implemented. |
| `SYNTH-SINGLE-SOURCE` | Retired: Proposed design principle. Not implemented. |
| `THEME-COMMODITIZE` | Retired: old roadmap theme. |
| `THEME-GLASSBOX` | Retired: old roadmap theme. |
| `THEME-INTEROP` | Retired: old roadmap theme. |
| `THEME-KNOWLEDGE-ARCH` | Retired: old roadmap theme. |
| `THEME-LIVING-DOC` | Retired: old roadmap theme. |
| `THEME-MARKETPLACE` | Retired: old roadmap theme. |
| `THEME-PLATFORM` | Retired: old roadmap theme. |
| `THEME-PREDICTIVE` | Retired: old roadmap theme. |
