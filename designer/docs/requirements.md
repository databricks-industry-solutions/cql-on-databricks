# Designer requirements

Requirements the Designer owns. Requirements that span features, and the rule for where a requirement
lives, are in [`docs/requirements.md`](../../docs/requirements.md). Each ID is stable, unique across
every registry, and cited from code (`PROC-CITE-REQ`). A requirement that stops being true moves to
[Retired](#retired) with a one-line reason, so old citations still resolve.

## Product and Designer experience

What the Designer does for a user.

| ID | Requirement |
|---|---|
| `ACC-KA-TRACE` | Traceability across KA layers is first-class: navigate lineage down (to patient Case), up (to Pathway), and across (features shared by measurement and prediction). |
| `APP-AI-CACHE` | Every AI-generated summary and explanation is cached and reused; edge-case narratives are generated only on explicit request. |
| `APP-ANALYTICS` | Analysis is a module, not a pane, answering what is true about the population and what to do — attribution, co-occurrence, stratified comparisons and trends scoped to the measure. |
| `APP-ASSISTANT` | One natural-language assistant routes three intents — Ask, Edit, Operate — to the right places: Ask as conversational prose, Edit as a document diff, Operate as a filled form. |
| `APP-ASSISTANT-CONTEXT` | The assistant knows the measure, data source, period, and selected define as context that prefills tool arguments and labels; context defaults, never forces. |
| `APP-ASSISTANT-MARKDOWN` | The assistant answers in markdown and is told which markdown; the SPA renders the parsed set (paragraphs, bold, code, links, lists, tables) and degrades unrecognized constructs to plain characters. |
| `APP-ASSISTANT-REFS` | Assistant answers cite measures by `cql-databricks:` links that render as the Specification does — the same controls and inks — so a reader can open what the assistant named. |
| `APP-AUTHOR-TABS` | The authoring workspace is multi-tab — multiple measures and libraries open simultaneously. |
| `APP-AUTHORING-VS-RESULTS` | Authoring optimizes for future evaluations; Evaluation and Improvement optimize for actual calculations against real members; each module is specialized for its job. |
| `APP-BROWSE` | One reusable content-marketplace component (cards, pill filters, search) serves both the Measures library and the Authoring "Open" picker. |
| `APP-CARE-GAPS` | A care-gaps view over calculated results lists members who are eligible but not yet met, each with a plain-language reason. It is a set of derived views, not a separate gaps engine. |
| `APP-COHORT-ALGEBRA` | Every figure on the flow is a co-membership query over per-define result tables in the warehouse using typed predicates (a boolean define's own value, any other's presence), never restating CQL semantics. |
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
| `APP-DEFAULT-EXAMPLE` | Every product feature ships a bundled example (the sample measure `HypertensionBPControl`) plus a preset lens/mode so new users see that feature working immediately, offline, with no backend or connected data. Amended 2026-10-21: the bundled example is `HypertensionBPControl`; `BasicEncounterAndCondition` is removed. |
| `APP-DEP-COUNTS` | In the Narrative view each criterion shows dependency counts in both directions and selecting a criterion highlights both directions of its transitive closure in the display. |
| `APP-DEP-NAV` | Opening an included library from the artifact selector navigates in place without repeating the parent label — a back affordance replaces the earlier redundant breadcrumb. |
| `APP-DERIVE-MEASURE` | A Derive action on external measures creates a new owned copy with lineage back to the original, recording source library and version, resetting its own version to draft, and carrying over any personal narrative overlay. |
| `APP-DESIGN-SYSTEM` | One visual language across the SPA: the Databricks brand palette plus three type roles and component patterns, implemented in theme.css and specified in design-system.md. |
| `APP-DIAGNOSTICS` | Validation via the Compiler Service is surfaced as plain-language clinical messages, preserving fail-loud semantics. |
| `APP-DISCUSSION` | Shared review threads anchored to Specification sections — the reviewer's channel back into the measure, keyed by library, version, and section anchor so they survive recompiles. |
| `APP-DQ-DEMO-EXCEPTION` | The Validate lens's readiness figures come from a bundled profile and are not marked as sample/synthetic — a narrow exception bounded to demo deployments, which replace the profile when observed data arrives. |
| `APP-EDITOR` | The frontend is a Vite + React SPA (TypeScript); the backend is Python/FastAPI running measures in-process via the OSS client; Monaco editor is guard-railed for clinical users. |
| `APP-EVALUATION` | The Evaluation module runs an authored measure or single definition against a dataset optimized for result inspection: population funnel, member list, plain-language per-member explanations. |
| `APP-EXEC-MODE` | Computation is a single dropdown colocated with the measurement period: Static, Test Deck, Live Sample, Live Population. |
| `APP-EXEC-PARAMS` | Execution parameters are measurement period and any parameters the measure declares, stepped via `‹ ›` arrows and dialled in the toolbar context-control bar. |
| `APP-EXECUTION-PILL` | The toolbar's one execution control with a status dot and caret, carrying data source, warehouse and update mode in its tooltip; its popover shows the run status and a Refresh button. |
| `APP-FLOW-GRAIN` | Where the unit of analysis changes the flow draws an explicit node naming the grain change and its multiplier; every scoring type (proportion, continuous variable, ratio) renders its terminal appropriate to that scoring. |
| `APP-GRAMMAR` | Client-side CQL Monarch grammar drives keystroke-live highlighting and bracket matching. |
| `APP-HOME` | Landing page is a workspace dashboard over a searchable catalog of measures and libraries. |
| `APP-INSPECTOR` | Every tab in the right pane is always selectable; its body names what it has or, if empty, why (no run, no data source chosen, etc.); the layout does not reflow when tabs become available. |
| `APP-LIBRARY-VS-MEASURE` | A measure and a reusable library are distinct first-class artifacts with distinct create flows. |
| `APP-LIVING-CHARTS` | Contextual visualizations: inline contextual metrics appear in every lens when computed; consumption charts are the Analyze module's native content gated on run history. |
| `APP-LIVING-MEASURE` | The measure reads as a clinical specification in which every figure is computed and every concept is a link. Changing the measurement period recomputes the figures in place. |
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
| `APP-PILL-MENU` | The reusable selector-pill control carries a status dot and trailing chevron, opening a popover with independent single-select groups for an execution-context axis. |
| `APP-PREDICT-FOUNDATION` | The product provides foundational predictive modeling where factors are CQL defines, grounded in the customer's clinical logic and glass-box interpretable. The accelerator computes factors and hosts the module; the customer controls the model and threshold. |
| `APP-PREDICT-NL` | Users build predictions in natural language through the assistant, which translates intent to factor definitions in CQL, selects model classes, and wires MLflow automation. |
| `APP-PROBE` | Inspecting a sub-expression runs it in-process against the scoped cohort; the query and results are displayed. |
| `APP-PROFILE-STRATA` | Definitions are stratified by grain (subject or element) with labels, units, and notes stating the distinction; stratifications use the subject grain by default and never show element cuts for boolean defines. |
| `APP-RATE-AT-RISK` | The reported rate beside the band it could occupy once identified data gaps resolve, stating only failing checks widen it and only members who could cross a population boundary count. |
| `APP-RUN-LOG` | The Designer logs each run to deployment-owned Delta tables with user, measure, period, compute time, and status; no CQL or patient data. |
| `APP-SCENARIO` | A counterfactual is asked in natural language and answered as a side-by-side diff: the user describes a change to one definition, the assistant authors CQL against the live library, and the result renders beside the baseline. |
| `APP-SHELL` | Persistent three-region shell with top bar, left module navigation, and context-aware right AI agent; navigation and agent are independently collapsible. |
| `APP-SPEC-CELLS` | Each criterion section behaves like a notebook cell with a hover hairline margin bar and context actions (Run, Copy, Genie, Focus mode); a between-cell Add affordance invokes the assistant to author a new criterion. |
| `APP-SPEC-COLLAPSE` | Every top-level Specification section, library group, and definition is independently collapsible with a caret; an Expand all / Collapse all control drives whichever sections are present. |
| `APP-SPEC-MODEL` | Both the left nav and the Specification page render one model derived entirely from the compiled ELM, so the two surfaces cannot disagree about what they show. |
| `APP-STABLE-LAYOUT` | Selecting or toggling a control never shifts position or size of others unless movement is the point; controls appear always and disable when idle, reserving their space; active state uses color or underline, not weight or size. |
| `APP-STEP-INTO-PATIENT` | Any population count opens one member's story: a timeline of relevant events and which criterion placed them where, with a way back to the aggregate. |
| `APP-STYLE-GUIDE` | Enforceable style guide operationalizing the design system: one semantic color vocabulary, fixed type scale, and DM Mono reserved for code/data only. |
| `APP-TOUR` | An in-app product tour shows the platform's pillars — analytics, living document, glass-box transparency, governance, knowledge architecture, the clinical decision layer and its applications — each with a Show me deep-link to a live example or the example notebooks. |
| `APP-TRAJECTORY` | A member's view can show a disease-state trajectory from a pluggable, attributed model. The visualization is ours; the model is content. |
| `APP-UPDATE-MODE` | Measures update automatically when logic changes, or only on explicit Refresh; the default is manual, and the mode is shown in the compute pill's status line with reasons why no results are displayed. |
| `APP-URL-ROUTING` | Workspace reading position lives in the URL (active lens as path, open artifact as query parameter, selected criterion as query parameter); navigation pushes history entries. |
| `APP-VALUE-READABLE` | Returned values render with a summary line, expandable field list in clinical order, and raw value in a tooltip; the block is labelled by grain (subjects at row level, elements at element level), and bars appear only over aggregated distributions. |
| `APP-WORKBENCH` | The right pane holds data about the selected definition (return type, runtime profile, terminology bindings) in tabs; pane scope is resolved once per tab, never re-derived per-tab. |
| `APP-WORKSPACE-FOLDER` | The Designer loads CQL libraries from and saves them to a Databricks workspace folder as an alternative to tagged knowledge sources, read-write as the signed-in user, with collision detection on save; folder sources are draft only. |

## The Specification document

The measure document the Designer derives from ELM.

| ID | Requirement |
|---|---|
| `PROC-DATA-PROVENANCE` | A data element reachable from a measure boundary but not used by any measure define (dead code) is not part of the measure. Reachability walks the compiler's describe graph. |
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
| `RUN-PROGRESS-TASKS` | The Designer's progress gauge shows the fraction of barrier tasks in a terminal state. |
| `SVC-MODULE-OPTIN` | Deploy-time module manifest gates which functional modules the Designer app harnesses and mounts. |
| `SVC-MODULE-SPLIT` | Optional promotion of a module from in-app surface to its own Databricks App with cross-app launcher. |
| `SVC-RUN-EVAL` | Measure execution in the Designer instance's Spark session via the OSS cql() client. |
| `SVC-RUN-STAGE` | Current execution stage name and progress indicator displayed on the compute pill. |
| `SVC-SESSION-INSTANCE` | Each Designer instance owns its Spark Connect session; concurrent instances do not share temp views. |

## Data sources and knowledge

How the Designer lists, binds and shows data sources and knowledge.

| ID | Requirement |
|---|---|
| `DATA-REGISTRY` | The Settings module's first page shows three sections: Knowledge Sources (logic and terminology, global), Test Decks (conformance fixtures), and Data Sources (per-type retrieve bindings). All are read-only; a reload button searches for tagged schemas. |
| `DATA-SOURCE-AUTORESOLVE` | The source editor offers a convenience: pick a catalog and schema, and the BFF introspects it to prefill type mappings by matching the `retrieve_<Type>` convention and `(__subject_id, __resource)` columns. The user reviews and overrides. |
| `DATA-SOURCE-MODE` | The run's binding target is selected from discovered data sources: Test Deck (bundled synthetic offline), conformance (golden fixtures), or live customer sources. The UI discovers them from UC tags; live samples vs populations are separate registered sources. |
| `DATA-SOURCE-PROFILE` | Each data source's binding profile maps modeled FHIR types to retrieve UDTFs and a valuesets location. The profile resolves from the tagged schema's functions and is mapped back by the layout check. Sampling is implemented entirely inside the customer's UDTFs. |
| `DATA-SOURCE-REQUIRED` | Governance classes on data sources: Conformance is required and selectable in every environment to stamp a certification receipt. Test Deck is default and non-removable. Only user-defined live sources are removable. |
| `KNOWLEDGE-LAKE-DIRECT` | The BFF reads knowledge library and measure metadata directly from Lakehouse tables in one eager batch load held in memory, eliminating the prior sync job. |
| `KNOWLEDGE-ORIGIN` | Every Data Registry entry carries an origin: `authored` (we are the authority, mutable, in our registry) or `external` (customer-owned, read-only, backed by a view in their lake). `authored` subsumes the old user class; `external` subsumes live-source bindings. |
| `TERMINOLOGY-STANDARDIZE` | Knowledge sources (not locations) carry Measure/Library/ValueSet bindings. Test Deck is read-only. Data Sources bind retrieval. Tabs show Knowledge Sources paired with Data Sources as one choice between two things of a kind. |

## Results and pipelines

How the Designer runs measures and serves their results.

| ID | Requirement |
|---|---|
| `PIPE-DRAFT-IDENTITY` | Draft pipeline identity is derived from evaluation parameters (data source, measurement period, custom params), not ELM content, so editing CQL keeps the same pipeline and updates in-place. |
| `PIPE-INCREMENTAL-REUSE` | During authoring, unchanged defines are not recomputed; draft pipelines read from published dependency tables, and the BFF uses selective refresh for changed defines and their descendants only, based on per-barrier SQL hashes. |
| `PIPE-MEASUREMENT-PERIOD` | Measurement period is a parameter to the pipeline run; interactive users select from a dropdown with instant switching between cached periods, and scheduled runs expand a min/max range to individual periods. |
| `PIPE-OWNERSHIP` | All pipelines and result tables are owned by a system security group or service principal, with no per-user ownership; the BFF authenticates as the service principal to create and trigger pipelines. |
| `PIPE-PER-LIBRARY` | Pipeline granularity is one per `(library_name, library_version, data_source)`, so a new published version is a new pipeline and two measures using different versions produce distinct pipelines with no collision. |
| `PIPE-PIPELINE-IDENTITY` | One `(library, version, data_source)` maps to exactly one pipeline; lookup order is process cache, the metadata store, and adoption of existing pipelines by name (lowest id wins), with deployment serialized per identity. |
| `PIPE-PIPELINE-NAME` | A pipeline is named `cql-databricks-{lib}-{year}-{ver}-{ds}` with the measurement period's year (for readability), but the year is cosmetic and lookup remains keyed on library, version, and data source. |
| `PIPE-PIPELINE-RECOVERY` | A pipeline deleted by accident is recreated reactively at the moment something needs it; recovery re-uploads the stored SQL and full-refreshes every barrier to populate the schema. |
| `PIPE-PRE-COMPUTED` | The Designer serves pre-computed results via `/api/results` from the shared results schema; opening a published measure with cached results is instant. |
| `PIPE-VERSION-AS-SCOPE` | Results are keyed by `(library_version, data_source)`: published versions produce shared results, draft versions produce private results known only to the author. |

## Performance

How the Designer's backend keeps responses fast.

| ID | Requirement |
|---|---|
| `PERF-CACHE-REVISION` | Browser cache revalidation uses one computed digest of compiled libraries, data sources, knowledge locations, and run history; every `/api` GET is served with weak ETag based on the digest, and only 200 responses are tagged. |
| `PERF-CLIENT-REVISION` | The BFF publishes its revision to every response via header and dedicated API; the SPA mirrors it and serves cached data until the revision changes, then drops everything. |
| `PERF-DISCOVER-LIGHTWEIGHT` | Library discovery omits CQL source to avoid transferring megabytes; source is lazy-loaded on demand. |
| `PERF-RESPONSE-GZIP` | The BFF gzips responses over 1024 bytes; SSE streams are excluded from compression to ensure incremental event delivery. |

## Retired

| ID | Status |
|---|---|
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
| `DATA-BIND-AUTO` | Retired: Designer bind button and auto-reload removed. |
| `DATA-REGISTRY-RENAME` | Retired: Data Source record naming superseded by UC tag reading. |
| `DESIGNER-COMMAND-PALETTE` | Retired: finding a define is the Open menu. See `DESIGNER-TOP-BAR`. |
| `DESIGNER-PUBLISH-GATE` | Retired with the Data Readiness module. See `DESIGNER-ONE-MODULE`. |
| `DESIGNER-SDP-DIAGNOSTICS` | Retired with SDP. |
| `DESIGNER-SPACE-CONTEXT` | Retired: data sources are found by UC tag. See `DATA-SOURCE-UC-TAG`. |
| `KNOWLEDGE-LAKEBASE-ALIGN` | Retired: derivations moved to memory; Lakebase tables no longer defined. See `KNOWLEDGE-LAKE-DIRECT`. |
| `KNOWLEDGE-SYNC` | Retired: direct Lakehouse read replaced the sync job and cron. See `KNOWLEDGE-LAKE-DIRECT`. |
| `SPEC-INDEX-TREE` | Retired. See `SPEC-NAV-COMPOSITION`. |
| `SPEC-LIBRARY-DENSITY` | Retired: every library renders in full and the reader collapses it. See `APP-SPEC-COLLAPSE`. |
| `SPEC-READ-METRICS` | Retired: retrieves are not materialized, so there are no read counts. See `SPEC-READ-REF`. |
