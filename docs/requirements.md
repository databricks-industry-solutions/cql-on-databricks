# Requirements

Requirements that span features. Each feature's own requirements are in its registry:

| Feature | Registry |
|---|---|
| compiler, including the client | [`compiler/docs/requirements.md`](../compiler/docs/requirements.md) |
| designer | [`designer/docs/requirements.md`](../designer/docs/requirements.md) |
| agent | [`agent/docs/requirements.md`](../agent/docs/requirements.md) |
| loader | [`loader/docs/requirements.md`](../loader/docs/requirements.md) |

A requirement lives in the registry of the one feature that implements it. One that two or more features
implement, or that no feature owns (the product, process, hosting and deploy, identity), lives here. An ID
is defined in exactly one registry. Each ID is stable and cited from code (`PROC-CITE-REQ`). A requirement
that stops being true moves to its registry's Retired section with a one-line reason, so old citations
still resolve.

## Product

What the product is for and who it serves.

| ID | Requirement |
|---|---|
| `APP-AUDIENCE` | The primary user is a non-technical clinical user; the experience is low-code/no-code, never an IDE; engineer artifacts remain out of the clinical path. |
| `APP-CLINICAL-BOUNDARY` | Databricks provides the mechanics of trust and radical transparency; clinical authority stays with standards bodies, customer staff and publishers. |
| `APP-COMMODITIZE` | The competitor is the incumbent specialized stack, not another tool; the accelerator collapses it onto Databricks, and ergonomic sharing is the amplifier. |
| `APP-ERGONOMICS` | The path from connected data to installed pack to computed result to stepping into a patient must be rapid, ergonomic, convenient and effective on owned compute. |
| `APP-FOCUS` | Execution of measures against customer data and mapping that data into pipelines are the primary value; measure authoring is secondary but a deliberate differentiator. |
| `APP-PLUGGABLE-CLINICAL` | Clinical judgment enters as pluggable, governed, versioned, attributed content authored by clinicians and publishers, including AI authoring instructions that steer NL guidance. |
| `APP-VISION` | Two surfaces onto the engine in one workspace: runtime execution of measures on customer data, and authoring/discovery/editing/evaluation via natural language. |

## Sharing

Sharing measures and results across organizations.

| ID | Requirement |
|---|---|
| `APP-SHARE` | Share measures and libraries with other organizations on Databricks over the open sharing protocol; recipients read them live, without copying artifacts. |
| `KNOWLEDGE-FEED-INGEST` | Lakehouse and serving tables are publisher and consumer; seam is knowledge feed over OpenSharing, not internal detail. Any recipient runs same derivation step. Published side optimizes for faithfulness; consumer side for use. |
| `SHARE-BULK-FHIR` | Population FHIR data represented as Delta tables and shared over the open protocol survives intact for any recipient to run any measure. |
| `SHARE-CANONICAL-LAYOUT` | Publish the standard FHIR-in-Delta layout and value-set-as-table convention as the canonical contract; recipients on this layout get zero-config data binding. |
| `SHARE-ENLIST` | A data source is a configurable union of value-set views and logical mounts that decouples measures from physical shares; enlistment is explicit and UC-governed. |
| `SHARE-LICENSING` | Value-set licensing is resolved on the recipient side; shared FHIR data is governed by recipient controls and UC masking. |
| `SHARE-MEASURE-PACKAGE` | Cross-organization logic sharing ships the measure package (CQL + ELM + value set references); recipients compile locally against their own data. |
| `SHARE-OPENSHARING` | Share measures and libraries between organizations on Databricks over the open sharing protocol; recipients read them live, without copying artifacts. |
| `SHARE-RESULTS` | Measure results (population membership, measure reports, gap views) share as tables over the open protocol, distinct from logic and data modalities. |
| `SHARE-RUN-ANYWHERE` | A measure package published once runs in any recipient organization on Databricks, against its own data or a shared governed FHIR population, without reimplementation. |

## Process and repository structure

How work in this repository is done and how it is laid out. Sources are relative to this folder.

| ID | Requirement | Source |
|---|---|---|
| `BUILD-SYSTEMS` | Per-feature: owns, does not own, interface. One-way order held; HTTP counts. Common library `cql_databricks_common` is own distribution at `common/`. | `systems.md` |
| `BUILD-TAXONOMY` | Feature → component → module. Feature is deployable, functional unit with own docs; component is build unit; no product level. Dependencies one way, HTTP counts. Four features: loader, compiler, agent, designer. | `structure.md` |
| `PROC-CITE-REQ` | Every change cites the requirement id(s) it realizes in code, commits, PRs, and tests. | `../compiler/docs/overview.md` |
| `PROC-DESCRIPTIVE-ID` | Every referenceable label is a descriptive mnemonic, never a bare sequence. Sequence goes in explicit `after` column. | `README.md` |
| `PROC-DRY` | Every piece of knowledge has one authoritative representation; callers reference it, never restate. | `../compiler/docs/engineering-principles.md` |
| `PROC-SOLID` | Single-responsibility modules, extension via seams not edits, substitutable providers, minimal interfaces. | `../compiler/docs/engineering-principles.md` |
| `PROC-TDD` | Each new kernel primitive ships with operator-level + desugaring/lowering tests before done. | `roadmap.md` |

## Services, hosting and deploy

The Apps, the bundle, packaging and local runs.

| ID | Requirement |
|---|---|
| `APP-LOCAL-FIRST` | The Designer UI track builds and validates locally first; the compiler and notebook track deploy via Databricks since measuring against UC data requires the platform. |
| `APP-PER-FEATURE` | The Designer, agent, and compiler each have their own App; each feature App serves requests as the signed-in user with its own identity and persisted state, so they scale independently. |
| `APP-PLANES` | Three separated planes: Presentation (React/TypeScript SPA + Python/FastAPI backend-for-frontend), Compiler Service (JVM), and the OSS CQL engine in a sibling repo. |
| `CONTENT-ENV-CONSOLIDATE` | Single root .env file or databricks.local.yml for workspace settings; .env keeps local-run settings and secrets. Per-component .env files eliminated. |
| `DEPLOY-APP-GRANTS` | Each App receives its own service principal; Unity Catalog grants (USE_CATALOG, USE_SCHEMA, SELECT, CREATE) are applied imperatively by script. |
| `DEPLOY-LAKEBASE-SCHEMA` | Database DDL lives only in the deploy definition. Code assumes its tables exist and fails loud if they don't. Seed scripts issue DML only. No feature uses Lakebase today. |
| `DEPLOY-SEED-DATA-SOURCES` | The seed script populates the Test Deck synthetic data source with idempotent DML; the `data_sources` table itself is created by the deploy's schema definition. |
| `PIPE-DEPLOY-NO-PLACEHOLDER-ENV` | Deployment never ships unsubstituted `__PLACEHOLDER__` env variables; all substitutions happen at bundle time before any service starts. |
| `RUN-CLEANUP` | A scheduled maintenance Job deletes session jobs idle beyond seven days, selected by tags, never while running. |
| `RUN-CLIENT-MAINTENANCE` | One daily `cql-maintenance` Job expires completed private results across users after configurable retention by UC tags. The deploy owns the Job (`databricks.yml`, `scripts/jobs/maintenance.py`) and the principal it runs as (`scripts/deploy/maintenance_principal.py`); the expiry is `cql_databricks.result.maintenance`. The schedule deploys paused until a run is checked on the workspace. |
| `RUN-CLIENT-PRIVATE-PROVISIONING` | Private results catalog is `designer_results_catalog`; Apps get catalog-level privileges only, per-schema for data. |
| `SVC-ANSWER-PROVENANCE` | Distinguish engine-computed answers from agent-derived answers via provenance metadata on each result. |
| `SVC-APP-TOPOLOGY` | Three independent apps: Compiler (JVM), Designer (Python/React), and agent (Python/MCP). |
| `SVC-CATALOG-LAYOUT` | Unity Catalog structure: the configured product catalog (bundle variable `uc_catalog`, no default; set in `databricks.local.yml`) with `runtime` schema for UDFs, `artifacts` volume, and per-Data-Space schemas. No catalog name appears in source. |
| `SVC-CDS-HOOKS` | Emit CDS Hooks-compatible artifacts and provide the evaluation backend for hook-driven clinical workflows. |
| `SVC-DEPLOY-BUNDLE` | One Asset Bundle declares every App and its permissions, and deploys them together. |
| `SVC-DEPLOY-ENV-LOCAL` | Workspace hosts, profiles, cluster IDs, and warehouse IDs are never committed; they live in gitignored local files. |
| `SVC-DEPLOY-TARGETS` | One codebase runs unchanged on Local and Databricks targets through a platform-provider abstraction. |
| `SVC-EXAMPLE-INDEX` | Published examples start with a one-page documentation index cell with no computation. |
| `SVC-EXAMPLE-KNOWLEDGE` | Examples ship with supporting content: data sources, terminology, and reference libraries. |
| `SVC-EXAMPLE-NOTEBOOKS` | Runnable example notebooks are deployed as product artifacts, not repository samples. They are checked by the live test suite (`scripts/test.sh integration`) as one-time runs; the deploy creates no Job for them (amended 2026-10-11). |
| `SVC-FHIR-ENDPOINTS` | Expose a conformant HL7 FHIR Clinical Reasoning API surface: Measure/$evaluate-measure, Library/$evaluate, etc. |
| `SVC-GENIE-ONTOLOGY` | Publish each measure's result surface as governed enterprise knowledge in the Genie ontology. |
| `SVC-LOCAL-CONFIG-BUNDLE` | All local settings have one home: workspace settings in gitignored local files, session configuration in code. |
| `SVC-MODEL-ENABLEMENT` | Enable the Predictive Foundation via platform ML: Feature Store, MLflow tracking and Model Registry, Model Serving, Lakehouse Monitoring, AutoML. |
| `SVC-PACKAGING` | Small jars ship as app source; oversized runtime artifacts (JRE, three public jars) are hosted in a UC Volume with public-URL fallback. |
| `SVC-PLATFORM-API` | Three-level composable API: App-to-App resource, published SDK, and MCP/agents. |
| `SVC-PROTOCOL` | Browser-to-BFF and BFF-to-Compiler over REST/JSON HTTPS with bearer tokens and OAuth. |
| `SVC-PY311-SYNTAX` | Deployed backend code must parse under Python 3.11; staging validates all source with `python3.11 -m compileall` to catch 3.12-only syntax before App startup. |
| `SVC-RUN-DBCONNECT` | In-process execution via Spark Connect to serverless or all-purpose clusters. |
| `SVC-STAGE-LOCAL-JARS` | Source the three public Maven jars from installDist output instead of re-fetching from Maven Central. |
| `SVC-STATE-BOUNDARY` | Knowledge sources are UC-based; per-instance state, terminology, and UDFs are stored separately. |
| `SVC-UPDATE-MECHANISM` | Each accelerator component carries a version and supports upgrade-in-place with migration paths. |
| `SVC-WHEEL-VERSION` | Wheel filenames encode version derived from pyproject.toml; substituted at staging with a guard to prevent drift. The client wheel's version is `<pyproject version>+src<content hash>`, so a client whose contents changed always installs over an App's kept venv, with no manual bump. |

## Identity and governance

Who a request runs as, and how Unity Catalog governs it.

| ID | Requirement |
|---|---|
| `APP-TO-APP-OBO` | A user reached through one App and making requests to another is reached as themselves (on-behalf-of) through a shared OAuth identity, so inter-App calls preserve the caller's identity and permissions. |
| `SEC-APP-TO-APP` | BFF reaches Compiler Service App with this process's own Databricks identity; read off shared WorkspaceClient so SDK owns caching and refresh. Gate is in front of every path. |
| `SEC-GOVERNANCE` | Bindings, value-set tables, the registry, and shared objects are ordinary Unity Catalog objects; access control, lineage, and audit are Unity Catalog's. |
| `SEC-LOCAL-AUTH-PREFLIGHT` | Local runs settle Databricks auth before stack starts via `databricks auth token` (OS-keyring cache); interactive login runs only on failure. |
| `SEC-LOCAL-IDENTITY` | Local target uses a single dev identity and local catalog (no OAuth on-behalf-of) — a dev convenience, not a production posture. |
| `SEC-OBO` | Designer data-source lookups and Bind action run as the signed-in user via OAuth on-behalf-of. The BFF builds a per-request user client from the forwarded token; service-principal client handles App plumbing and startup. Target detection, not fallback: missing token raises. |
| `SEC-OBO-SCOPE-GAP` | Jobs and pipelines scopes are not currently declarable in user_api_scopes. Interactive runs use service-principal client for orchestration. This is a documented cost, not mitigated. |
| `SEC-WS-CLIENT-SHARED` | One Databricks SDK WorkspaceClient per process, built lazily behind a lock; reached only through service-principal-client. Never cached per-call; refresh is single and serialized. |

## Data sources

The data-source contract several features share, and data-source features no one feature owns.

| ID | Requirement |
|---|---|
| `DATA-CONTRACT` | The customer data layout contract: canonical FHIR-in-Delta tables with `(__subject_id, __resource)`, `retrieve_<Type>` views with `cql.*` properties, a `valuesets` table, and optional `ConceptMap`. The compiler owns none of this; the customer or their Databricks product implements it. |
| `DATA-CROSSWALK` | The compiler owns versioned, shareable `ConceptMap` / crosswalk artifacts (code-to-code mapping, many-to-one, conditional `dependsOn`) that the customer's retrieve view applies. A governed content piece paired with the customer's view implementation. |
| `DATA-OBSERVABILITY` | The compiler observes the view boundary, UC lineage, and direct queries to diagnose coverage, quality, and provenance. It describes, never remaps or prescribes structure. |
| `DATA-PROVENANCE` | For any result value, the compiler traces retrieve view to source tables via UC lineage and optional LLM narration. Diagnostic only; describes and audits, never remaps. |
| `DATA-PUSHDOWN-DIAG` | A diagnostic to verify binding pushdown: call the mount twice (hints populated vs. empty) and report the row-count delta. Secondary tool for implementers; not required by the engine's own gates. |
| `DATA-RESOLUTION` | Retrieve-to-source resolution is engine-owned. The compiler reads the catalog metadata for observability but neither implements nor overrides the engine's best-match algorithm over `cql.*` properties. |
| `DATA-SOURCE-DESCRIPTION` | Each data source has an optional one-line description (the schema's `COMMENT`) shown in the compute pill. Absence is stated, not filled. Replaces assembled metadata that described the wiring rather than the data. |
| `DATA-SOURCE-SCOPE` | A data source binds only the data plane. Logic (Measure and Libraries) and terminology (value-set expansions) are global, resolved outside the source. This keeps authoring separate from execution. |
| `DATA-SOURCE-UC-TAG` | A data source is a Unity Catalog schema tagged `cql_databricks_data_source = <name>`. Nothing stores a record. The client lists them (`cql_databricks.compiler.data_source.list_data_sources`), reading bindings and change log from each tagged schema; the Designer caches the list per user. |
| `DATA-SUGGEST-TERMINOLOGY` | The compiler suggests terminology entries for user confirmation: unmapped codes ranked by frequency and similarity, and miscoding detection from cross-field correlations. Grounded, never fabricated. |
| `DATA-VIEW-SCAFFOLD` | The compiler generates starter retrieve-view DDL with `cql.*` properties as a template. The customer adopts and owns the deployed object; the compiler authors the shell, never the view. |

## Results

Results that the client, the Designer and the agent all read.

| ID | Requirement |
|---|---|
| `PIPE-UNIFIED` | All measure computation runs through pipelines, producing persistent materialized views; ad-hoc CQL evaluation (probes, scenarios) runs in-process via the OSS client and materializes nothing. |
| `RUN-CALC-SERVICE` | Goal, not built: a managed calculation service computes measures on behalf of callers and handles orchestration and lifecycle. |
| `RUN-CLIENT-MIGRATION` | Keep the bindings-into-client move; migrate callers and preserve released contracts before retiring paths. |
| `RUN-CLIENT-RESULT-SETS` | Designer uses per-user schemas and session-scoped completions; shared readers use recorded metadata and exact artifact versions. Amended 2026-10-11 by `RUN-CLIENT-RESULT-IDENTITY`: readers match results by identity, not by exact upstream artifact versions; a selection's address names its destination, not a publisher or population. |
| `RUN-LINEAGE` | Unity Catalog captures lineage per query for SQL interfaces and `CREATE TABLE AS SELECT`. |

## Sample content

The sample measures and patients in `examples/`.

| ID | Requirement |
|---|---|
| `CONTENT-SAMPLE-PRELOAD` | `examples/` is the one place that pre-loads a workspace with sample measures. The deploy loads its CQL as FHIR `Library` resources and each sample measure's authored `Measure` resource into tagged knowledge (`sample_measures`), its value sets into tagged terminology in the same schema, and its patients as `DATA-SAMPLE-SOURCE`. It never uses the eCQI path, and nothing outside `compiler/` reads the compiler's conformance corpus: the two copies of a measure are independent on purpose. Added 2026-10-21. Amended 2026-10-21: the conformance corpus keeps a copy of each sample measure (its CQL, patients, value sets and expected results as an answer key). For now the copies are the same and are changed in lock step, by hand. Nothing reads one from the other, so they may diverge later. Amended 2026-10-21 by `LANG-FHIRHELPERS-FROM-CONTENT`: the corpus also keeps unmodified copies of `FHIRHelpers.cql`, in `cql/measures/` and `cql/fhir/`, changed in lock step with `examples/measures/FHIRHelpers.cql` by hand. Amended 2026-10-21 by `DATA-SYNTH-SMALL`: the deploy loads the synthetic small population as the one sample set, with no expected results. The conformance corpus keeps the 48 hand-written patients and their answer keys as its own fixtures. The sample measures' CQL and value sets are still copied in lock step; the patients are not. |
| `CONTENT-SAMPLE-STANDARDS` | The sample measures in `examples/` follow CMS and ONC quality measurement standards and use only open-source, license-free content. Each is written against QI-Core 6.0.0 and includes FHIRHelpers 4.0.1, which `examples/measures` distributes unmodified. Each has an authored FHIR `Measure` with proportion scoring, the CQF Measures populations (initial population, denominator, exclusions, exceptions, numerator), improvement notation and supplemental data, and takes the measurement period at run time. Its value sets are the repository's own, with codes only from code systems free to use (ICD-10-CM, ICD-10-PCS, HCPCS Level II, LOINC under its license, CVX, HL7 terminology) or local codes: no CPT, no SNOMED CT, and no published measure, value set or library. Added 2026-10-21. |

## Library registry

Measures and libraries as governed, versioned content.

| ID | Requirement |
|---|---|
| `KNOWLEDGE-FAIL-LOUD` | Knowledge read failures propagate rather than returning empty results; failed loads are retried on next access and never cached. |
| `REG-CONFORMANCE-DECK` | Conformance decks carry golden results as MeasureReports with per-subject population counts. Validate computed results against the deck and stamp a pass/fail receipt (not built). Amended 2026-10-21: the shipped decks are the measures written for this repository, generated from the conformance corpus by `loader/importer/scripts/generate_decks.py` with their CQL unchanged; each per-patient count is the population define's value from the hand-written answer key. SUPERSEDED 2026-10-21 by `CONTENT-SAMPLE-PRELOAD`: decks existed only for published test content. The sample measures ship in `examples/`, with their expected results per patient beside the sample patients. |
| `REG-CRUD` | Draft → validate (clean diagnostics) → publish (immutable, indexed) → fork; performed as the signed-in user. |
| `REG-CURATION` | Seed marketplace with trusted publishers (standards bodies, quality vendors, centers of excellence); open publication later once community trust is load-bearing. |
| `REG-DISCOVERY` | Search/browse the Delta index by name/tags/resources/value sets/author/status; no separate search service. |
| `REG-PACK-DATA` | A Measure Pack bundles conformance data — FHIR test fixtures — as a (deck, version) slice of the shared corpus. Install via MERGE; uninstall via DELETE. SUPERSEDED 2026-10-21 by `CONTENT-SAMPLE-PRELOAD` as far as it is decks: no pack ships conformance decks. |
| `REG-PACKS` | Clinical intelligence bundles measures + libraries (CQL/ELM), value sets, and authoring instructions; versioned, forkable, rateable. |
| `REG-REGISTRY` | Libraries are CQL source plus metadata and discovery index; governed by Unity Catalog. |
| `REG-VERSION` | Identity is (name, version); closure resolved for compilation; versions immutable. |

## Accelerator and compiler integration

How the rest of the system uses the compiler.

| ID | Requirement |
|---|---|
| `ACC-BRAND-NAMING` | Designer in visible labels, code, headers, paths, docs, and requirement IDs. Full terminology migration explicitly approved. CQL Builder canonical naming: product/repo = cql-databricks; components cql-databricks-{compiler,runtime,app}. |
| `ACC-ENGINE-CLIENT` | The accelerator is a client of the compiler. It never re-implements CQL semantics or edits emitted SQL by hand. |
| `ACC-ENGINE-DEP` | Compiler is an ordinary project dependency (project(':compiler')) in a single Gradle build; neutrality is enforced mechanically by checkNeutrality. |
| `ACC-KA-LAYERS` | Adopt HL7 CPG separation of concerns: Pathway (PlanDefinition), Care Plan (CarePlan), Case+Features (FHIR events; CQL define), Measurement &amp; Prediction, Workflow (delivery, out of scope). Each capability declares its layer(s). |
| `ACC-KNOWLEDGE-LEVELS` | Content follows the HL7 CQF knowledge levels: L1 narrative, L2 semi-structured, L3 structured (FHIR Library, CQL, ELM), L4 executable (compiled SQL). A measure links its levels. |
| `APP-DECOUPLE` | Engine modules remain unmodified for this accelerator; the accelerator depends on the engine one-way with no Databricks-specific dependencies in the engine. |

## Retired

| ID | Status |
|---|---|
| `ACC-ENGINE-RESOLUTION` | Retired: monorepo merge dissolved the problem. See `overview.md` section 3.1. |
| `DATA-SCOPED-VIEW-ADVISOR` | Retired: Proposed future feature. Not committed. |
| `DATA-SOURCE-REGISTRY` | Retired: Superseded by UC tags. See `DATA-SOURCE-UC-TAG`. |
| `DEPLOY-SEED-KNOWLEDGE` | Retired: content importer tags sources instead of seeding rows. See `CONTENT-IMPORT-TARGET-TAG`. |
| `EVALUATOR-CLIENT-EVALUATE` | Retired: the evaluator engine plugin is removed with the App. See `RUN-CLIENT-DURABLE`. |
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
| `PIPE-SERVICE` | Retired: Pipeline orchestration service was future work, not implemented. |
| `PIPE-STATE-LAYER` | Retired: Lakebase pipeline state layer rebuilt into cache logic. See `PIPE-BARRIER-HASH`. |
| `RUN-ADDRESS-BY-LIBRARY` | Retired: the evaluator App's draft sweeper and address API are removed. See `RUN-CLIENT-RESULT-SETS`. |
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
| `THEME-COMMODITIZE` | Retired: old roadmap theme. |
| `THEME-GLASSBOX` | Retired: old roadmap theme. |
| `THEME-INTEROP` | Retired: old roadmap theme. |
| `THEME-KNOWLEDGE-ARCH` | Retired: old roadmap theme. |
| `THEME-LIVING-DOC` | Retired: old roadmap theme. |
| `THEME-MARKETPLACE` | Retired: old roadmap theme. |
| `THEME-PLATFORM` | Retired: old roadmap theme. |
| `THEME-PREDICTIVE` | Retired: old roadmap theme. |
