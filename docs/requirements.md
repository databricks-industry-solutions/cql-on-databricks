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
that stops being true is deleted, with every citation of it (`PROC-CURRENT-DEFINITION`).

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
| `PROC-CURRENT-DEFINITION` | A requirement's text is its whole current definition. A change rewrites it in place. It records no dates, no "added" or "amended" notes, and nothing it used to say. A requirement that stops being true is deleted from its registry, in the same change that removes every citation of it from code, tests and docs. History lives in git. | `../AGENTS.md` |
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
| `DEPLOY-AGENT-REGISTRATION` | Genie One calls only MCP servers connected through Unity Gateway, so the deploy registers the agent App there (`scripts/deploy/register_agent_mcp.py`): a UC HTTP MCP connection to the App's `/mcp` that signs each user in through an OAuth app integration, and an MCP Service over it in `agent_skills_schema`. Per user only, so each tool call runs as the signed-in user (`SEC-OBO`); a shared service-principal credential is never used. The workspace offers no dynamic client registration, so the deploy creates the integration through the account API, with the account admin's account-level CLI profile `agent_account_profile` names. The deploy checks that profile before it builds anything. The client's secret goes from the account API straight into the connection, and is never printed, stored or asked for. A connection that must be created or repointed gets a new client. A connection already pointing at the App is kept with no account call, so only creating or repointing it needs an account admin; without `agent_account_profile` the deploy leaves such a connection alone and says Genie One is not connected. Unity Gateway can't register a Databricks App as your own MCP, so the deploy reaches the App through this connection. `agent_users_group` gets `EXECUTE` on the service, never `USE CONNECTION`. Idempotent, and fails the deploy on any error. |
| `DEPLOY-WORKSPACE-SETTINGS` | The deploy turns on each workspace setting it relies on only when its bundle variable is true, as a workspace admin, before the Apps are built: Databricks Apps (`enable_databricks_apps`), user authorization for Apps (`enable_apps_user_authorization`, `SEC-OBO`), Unity Gateway (`enable_unity_gateway`, `DEPLOY-AGENT-REGISTRATION`) and Unity Gateway skills in Genie (`enable_genie_uc_skills`, `DEPLOY-AGENT-SKILLS`). Each is workspace-wide, so it is the implementer's choice and defaults to false. False leaves the setting as it is and never turns it off. Fails the deploy on any error. |
| `DEPLOY-AGENT-SKILLS` | The deploy publishes every skill in `agent/skills/` except `clinical-explain`, the Designer's narration contract; `scripts/deploy/publish_skills.py` lists them. Each skill's `SKILL.md` is published as it is, the same file the agent serves through `get_skill`, to two places: as a Unity Catalog skill in `agent_skills_schema`, readable by `agent_users_group`, an account group, which Genie One loads; and to the workspace skills folder `/Workspace/.assistant/skills/`, which Genie Code loads (Genie Code reads no UC skills). The deploy creates that folder and grants `workspace_users_group` CAN_READ, a workspace group, since a new one is readable by admins alone and a workspace object takes workspace groups, which Unity Catalog does not; only a workspace admin can create it, so another deployer gets `/Shared/cql_databricks/skills` and a loud warning. No skill holds a deployment value to fill in. It runs as the bundle script `publish_skills`, since a bundle has no resource type for either place. Idempotent, and fails the deploy on any error. |
| `DEPLOY-APP-GRANTS` | Each App receives its own service principal. Its schema grants are bundle resources in `databricks.yml`, naming the App's principal (`${resources.apps.<key>.service_principal_client_id}`): the Designer's principal gets `USE SCHEMA` and `SELECT` on the default environment's `knowledge` (`KNOWLEDGE-LAKE-DIRECT`), and nothing on a store or a result. `USE CATALOG` on `uc_catalog`, for the Designer's and the agent's principals, is granted by `scripts/deploy/grant-app-access.sh` after the bundle deploy: a bundle grants on a catalog only when it holds the catalog, and `uc_catalog` is the deployer's. No App principal holds a catalog-wide data privilege, which every user's table in `draft_results` would inherit; one an earlier deploy granted is revoked. The compiler App touches no data and gets nothing. The Designer and agent Apps each declare the compiler App as a resource, so the bundle creates them one after the other: created at once, their two writes to the compiler App's permissions conflict and leave one App unrecoverable. |
| `DEPLOY-DEFAULT-ENVIRONMENT` | The deploy builds the default environment in `uc_catalog` (`SVC-CATALOG-LAYOUT`). Its schemas, those of `DATA-ENVIRONMENT-LAYOUT`, are bundle resources in `databricks.yml`, with their schema grants: `app_users_group` gets `USE SCHEMA` and `SELECT` on `data`, `knowledge` and `results`, and `USE SCHEMA` and `CREATE TABLE` on `draft_results` (`RUN-CLIENT-DRAFT-RESULTS`); the `files` volumes of `data` and `knowledge` are bundle resources too, and `app_users_group` gets `READ VOLUME` on each; the Designer's principal is `DEPLOY-APP-GRANTS`'s. A bundle's grants on a schema are its whole grant list. After the bundle deploy, the deploy fills the schemas: first the same content as files in the `files` volumes, each left holding exactly those files, then the synthetic small population (`DATA-SYNTH-SMALL`) as tables in `data`, the example measures and their value sets as tables in `knowledge` (`CONTENT-EXAMPLE-PRELOAD`), and the environment tags, with the layout version this release's client supports (`CLIENT-ENVIRONMENT`). It sets `cql_databricks_environment_default = true` only if no catalog the deployer can see has it. Then it makes the two grants the bundle cannot: `USE CATALOG` on `uc_catalog` to `app_users_group`, because the bundle does not hold the catalog, and `USE SCHEMA` and `MANAGE` on `draft_results` to the maintenance principal (`RUN-CLIENT-MAINTENANCE`), which the deploy creates, so no other reader of the bundle knows it. `app_users_group` must be an account group: Unity Catalog grants take account principals only. Idempotent, and fails the deploy on any error. |
| `DEPLOY-ENVIRONMENT-TAG-POLICIES` | The three environment tags (`CLIENT-ENVIRONMENT`) are governed tags, so owning a catalog is not enough to make it an environment or the default. With `govern_environment_tags` true, the default, the deploy makes their tag policies before it tags a catalog: `cql_databricks_environment` allows any value, `cql_databricks_environment_version` the layout versions this release's client supports, and `cql_databricks_environment_default` only `true`. It sets a policy's values when they differ. The deployer and each of the project's admins (`DEPLOY-PROJECT-ADMINS`) get the assigner role on each policy; no one else is granted it. A bundle has no tag-policy resource, so this is a bundle script, `environment_tag_policies`, which `deploy.sh` runs. Creating a policy needs the create privilege on governed tags, and an account holds at most 1,000 governed tags. With `govern_environment_tags` false the deploy makes no policy and warns that anyone who can tag a catalog can make it an environment or the default. Idempotent, and fails the deploy on any error. |
| `DEPLOY-BASE-ENVIRONMENT` | The deploy publishes the workspace base environment "CQL on Databricks" (`workspace-base-environments/cql-on-databricks`), so a serverless notebook or job that selects it has the client without an install cell. Its YAML, `cql-databricks-env.yaml` at the root of the runtime Volume, names the environment version the example notebooks run on and the one client wheel this deploy built, by its exact version (`SVC-WHEEL-VERSION`). Databricks builds a base environment once and caches it, so every deploy rewrites the YAML and creates, re-points or refreshes the environment, then waits for the build. With `default_base_environment` true the deploy makes it the workspace default for new serverless notebooks, as a workspace admin; the default is workspace-wide, so the variable is false by default, and false leaves the default as it is and never unsets it. A bundle has no base-environment resource, so this is a bundle script, `base_environment`, which `deploy.sh` runs after the wheels are uploaded. The example notebooks name it (`SVC-EXAMPLE-NOTEBOOKS`). Users need `READ VOLUME` on the runtime Volume, as for any install from it. Idempotent, and fails the deploy on any error, a missing wheel or an environment Databricks could not build among them. |
| `DEPLOY-PROJECT-ADMINS` | The `admins` bundle variable lists the project's admins besides the deployer: users by email, service principals by application ID, or groups; empty by default. Every deploy, as the deployer, who must be a workspace admin, adds each to the workspace when it is not in it, then to the workspace's `admins` group; a group must already be in the workspace, else the deploy fails, naming it. It never removes anyone. The same principals assign the environment tags (`DEPLOY-ENVIRONMENT-TAG-POLICIES`). Fails the deploy on any error. |
| `DEPLOY-LAKEBASE-SCHEMA` | Database DDL lives only in the deploy definition. Code assumes its tables exist and fails loud if they don't. Seed scripts issue DML only. No feature uses Lakebase today. |
| `DEPLOY-SHIPPED-SOURCES` | The deploy's own data is the package `shipped_sources`, in `scripts/deploy/shipped_sources`: the example preload (`CONTENT-EXAMPLE-PRELOAD`), the population lander every shipped data source lands through, the shipped sources' names, their FHIR resource schemas (`CONTENT-IMPORTER-FHIR-SCHEMAS`) and the change-log exemplar (`DATA-CHANGE-LOG`). It is installed in the repository venv and never shipped to the Volume. The example measures go through the client's import (`CONTENT-IMPORT-MEASURES`). `loader/scripts/synthetic-population/load.py` lands the large population through the same lander. |
| `PIPE-DEPLOY-NO-PLACEHOLDER-ENV` | Deployment never ships unsubstituted `__PLACEHOLDER__` env variables; all substitutions happen at bundle time before any service starts. |
| `RUN-CLEANUP` | A scheduled maintenance Job deletes session jobs idle beyond seven days, selected by tags, never while running. |
| `RUN-CLIENT-MAINTENANCE` | One daily `cql-databricks-designer-maintenance` Job expires completed draft results across users after configurable retention by UC tags, in the `draft_results` schema of each environment the deploy grants it `MANAGE` on (`RUN-CLIENT-DRAFT-RESULTS`). The deploy owns the Job (`databricks.yml`, `scripts/jobs/maintenance.py`) and the principal it runs as (`scripts/deploy/maintenance_principal.py`); the expiry is `cql_databricks.result.maintenance`. The schedule deploys paused until a run is checked on the workspace. |
| `SVC-ANSWER-PROVENANCE` | Distinguish engine-computed answers from agent-derived answers via provenance metadata on each result. |
| `SVC-APP-TOPOLOGY` | Three independent apps: Compiler (JVM), Designer (Python/React), and agent (Python/MCP). |
| `SVC-CATALOG-LAYOUT` | Unity Catalog structure: the deployment's catalog (bundle variable `uc_catalog`, no default; set in `databricks.local.yml`) holds the `runtime` schema for the `cql_*` functions and the `artifacts` volume. In the default deployment it is also the default environment (`DEPLOY-DEFAULT-ENVIRONMENT`), and its other schemas are the environment's (`DATA-ENVIRONMENT-LAYOUT`). Every environment's runs use this one `runtime`. No catalog name appears in source. |
| `SVC-CDS-HOOKS` | Emit CDS Hooks-compatible artifacts and provide the evaluation backend for hook-driven clinical workflows. |
| `SVC-DEPLOY-BUNDLE` | One Asset Bundle declares every App and its permissions, and deploys them together. |
| `SVC-DEPLOY-ENV-LOCAL` | Workspace hosts, profiles, cluster IDs, and warehouse IDs are never committed; they live in gitignored local files. |
| `SVC-DEPLOY-TARGETS` | One codebase runs unchanged on Local and Databricks targets through a platform-provider abstraction. |
| `SVC-EXAMPLE-INDEX` | Published examples start with `start_here`, a one-page documentation index cell with no computation, at the top of `/Shared/cql_databricks/examples`. It lists every example in reading order, by group, and links to each. |
| `SVC-EXAMPLE-KNOWLEDGE` | Examples ship with supporting content. The example CQL libraries are published beside the notebooks, in `/Shared/cql_databricks/examples/measures`, and the notebooks read them as a workspace folder of libraries. The patients, value sets and libraries they run over are the default environment's, as tables and as files in its `files` volumes (`DEPLOY-DEFAULT-ENVIRONMENT`), and nothing else is published beside the notebooks. |
| `SVC-EXAMPLE-NOTEBOOKS` | Runnable example notebooks are deployed as product artifacts, not repository samples, in a folder per group: `getting_started/` for learning the client and `scenarios/` for applied work, each notebook named for its subtopic. Every folder and notebook name is snake_case. A `getting_started/` notebook starts with its two-digit place in the reading order (`getting_started/01_overview`); a `scenarios/` notebook has no number (`scenarios/quality_measurement`). They are checked by the live test suite (`scripts/test.sh integration`) as one-time runs; the deploy creates no Job for them. Each example is a `.ipynb` whose serverless environment is the base environment the deploy publishes, named `workspace-base-environments/cql-on-databricks` and nothing else (`DEPLOY-BASE-ENVIRONMENT`): no install cell, no restart, no Volume path and no placeholder, so the published copy is the repository copy. Its first code cell is `%reload_ext cql_databricks.compiler` and `from cql_databricks.compiler import cql`, and nothing else. The publish step fails on any placeholder. The index page (`SVC-EXAMPLE-INDEX`) is the one source notebook. |
| `SVC-EXAMPLE-RESOLUTION-PATHS` | Example notebook `getting_started/03_data_and_knowledge_resolution` runs one question over every place each role can come from, with the same answer from each: data and value sets from the environment's tables, from the files in its `files` volumes (`VolumeDataResolver`, `FolderTerminologyResolver`) and from DataFrames (`DataFrameDataResolver`, `DataFrameTerminologyResolver`); a measure's libraries from the environment's knowledge, from the `.cql` files in its volume and from the `measures/` workspace folder published with the examples (`FolderKnowledgeResolver`), with the same population counts; and its results kept for the session (`CLIENT-SESSION-RESULTS`), computed by one `materialize` and reused by the next. It finds the volumes from the default environment's catalog, and writes no file. |
| `SVC-FHIR-ENDPOINTS` | Expose a conformant HL7 FHIR Clinical Reasoning API surface: Measure/$evaluate-measure, Library/$evaluate, etc. |
| `SVC-GENIE-ONTOLOGY` | Publish each measure's result surface as governed enterprise knowledge in the Genie ontology. |
| `SVC-LOCAL-CONFIG-BUNDLE` | All local settings have one home: workspace settings in gitignored local files, session configuration in code. |
| `SVC-MODEL-ENABLEMENT` | Enable the Predictive Foundation via platform ML: Feature Store, MLflow tracking and Model Registry, Model Serving, Lakehouse Monitoring, AutoML. |
| `SVC-PACKAGING` | Small jars ship as app source. Oversized runtime artifacts (JRE, three public jars) are hosted in a UC Volume, which the App reads: a failed read stops its boot. The public URLs are used only when no volume is configured. |
| `SVC-PLATFORM-API` | Three-level composable API: App-to-App resource, published SDK, and MCP/agents. |
| `SVC-PROTOCOL` | Browser-to-BFF and BFF-to-Compiler over REST/JSON HTTPS with bearer tokens and OAuth. The compiler ignores an unknown request key and refuses a retired one with a 400 that names it. |
| `SVC-PY311-SYNTAX` | Deployed backend code must parse under Python 3.11, the Apps' runtime: each App's staged source and the client wheel it installs (`compiler/client/src`). Staging validates both with `python3.11 -m compileall`, writing no bytecode into the source tree, and fails before any App starts. |
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
| `SEC-OBO` | The Designer's environment lookups, runs and publishes run as the signed-in user via OAuth on-behalf-of. The BFF builds a per-request user client from the forwarded token; service-principal client handles App plumbing and startup. Target detection, not fallback: missing token raises. |
| `SEC-OBO-SCOPE-GAP` | Jobs and pipelines scopes are not currently declarable in user_api_scopes. Interactive runs use service-principal client for orchestration. This is a documented cost, not mitigated. |
| `SEC-WS-CLIENT-SHARED` | One Databricks SDK WorkspaceClient per process, built lazily behind a lock; reached only through service-principal-client. Never cached per-call; refresh is single and serialized. |

## Data sources

The data-source contract several features share, and data-source features no one feature owns.

| ID | Requirement |
|---|---|
| `DATA-CONTRACT` | The customer data contract: an environment's `data` schema of tables or views, one per FHIR type, each returning the resource as a `resource` struct and optionally the root columns `patient_id`, `min_date` and `max_date` (`BIND-CONTRACT`, `DATA-SUBJECT-KEY`, `BIND-PREFILTER`, `DATA-ENVIRONMENT-LAYOUT`); value sets in its `knowledge.valueset`; optional `ConceptMap`. The compiler owns none of the data; the customer or their Databricks product implements them. |
| `DATA-CROSSWALK` | The compiler owns versioned, shareable `ConceptMap` / crosswalk artifacts (code-to-code mapping, many-to-one, conditional `dependsOn`) that the customer's retrieve view applies. A governed content piece paired with the customer's view implementation. |
| `DATA-ENVIRONMENT-LAYOUT` | An environment's catalog (`CLIENT-ENVIRONMENT`) holds these schemas, by these names. `data`: one table or view per FHIR type, named for the type in lower case (`condition`), each returning the contract (`BIND-CONTRACT`), and optionally `change_log` (`DATA-CHANGE-LOG-PERIODS`). `knowledge`: `library` and `measure`, with the columns the import writes (`CONTENT-IMPORT-MEASURES`), and `valueset(valueset_id, system, code, version)`. Terminology is knowledge: neither holds patient data, and they have the same readers and writers. `results` and `draft_results` (`RUN-CLIENT-DRAFT-RESULTS`). There is no store schema: the deploy and the loader write tables in `data` and `knowledge`, and publishing writes the `knowledge` tables. Optionally, each of `data` and `knowledge` holds a volume `files` with the same content as files, for a run whose resolver reads files (`CLIENT-DATA-RESOLVER`, `CLIENT-TERMINOLOGY-RESOLVER`, `CLIENT-KNOWLEDGE-RESOLVER`): `data/files/fhir/<Type>.ndjson`, one resource per line, and `knowledge/files/valuesets/<name>.json`, one FHIR `ValueSet` with its expansion, and `knowledge/files/libraries/<Library>.cql`. A run reads the tables unless its resolver names the files. A customer whose data or knowledge comes from elsewhere defines views in an environment of its own, over tables in any catalog; a view's readers need no grant on the tables under it, and such an environment takes no publish into a view. Readers get `SELECT` on `data`, `knowledge` and `results`, and `READ VOLUME` on the `files` volumes. Nothing checks the layout at run time (`BIND-PUBLISHED-VIEW`). |
| `DATA-OBSERVABILITY` | The compiler observes the view boundary, UC lineage, and direct queries to diagnose coverage, quality, and provenance. It describes, never remaps or prescribes structure. |
| `DATA-PROVENANCE` | For any result value, the compiler traces retrieve view to source tables via UC lineage and optional LLM narration. Diagnostic only; describes and audits, never remaps. |
| `DATA-PUSHDOWN-DIAG` | A diagnostic to verify binding pushdown: call the mount twice (hints populated vs. empty) and report the row-count delta. Secondary tool for implementers; not required by the engine's own gates. |
| `DATA-RESOLUTION` | Retrieve-to-source resolution is engine-owned. The compiler reads the catalog metadata for observability but neither implements nor overrides the engine's best-match algorithm over `cql.*` properties. |
| `DATA-SUGGEST-TERMINOLOGY` | The compiler suggests terminology entries for user confirmation: unmapped codes ranked by frequency and similarity, and miscoding detection from cross-field correlations. Grounded, never fabricated. |
| `DATA-VIEW-SCAFFOLD` | The compiler generates starter retrieve-view DDL with `cql.*` properties as a template. The customer adopts and owns the deployed object; the compiler authors the shell, never the view. |

## Results

Results that the client, the Designer and the agent all read.

| ID | Requirement |
|---|---|
| `PIPE-UNIFIED` | All measure computation runs through pipelines, producing persistent materialized views; ad-hoc CQL evaluation (probes, scenarios) runs in-process via the OSS client and materializes nothing. |
| `RUN-CALC-SERVICE` | Goal, not built: a managed calculation service computes measures on behalf of callers and handles orchestration and lifecycle. |
| `RUN-CLIENT-MIGRATION` | Keep the bindings-into-client move; migrate callers and preserve released contracts before retiring paths. |
| `RUN-CLIENT-RESULT-SETS` | Designer writes to the environment's `draft_results` (`RUN-CLIENT-DRAFT-RESULTS`) with session-scoped completions. Readers match results by identity (`RUN-CLIENT-RESULT-IDENTITY`), not by exact upstream artifact versions. A selection's address names its destination, the schema its result resolver is addressed by (where a store writes, or the first schema a resolver reads), never a publisher, population or purpose; it reopens only through a resolver with that destination (`CLIENT-ENVIRONMENT-RESOLVER`). Every derivation (counts, metrics, profiles, the gate flow, scores and reads) reads only the pinned tables of one complete selection. |
| `RUN-CLIENT-DRAFT-RESULTS` | `draft_results` is one schema in each environment, shared by every user, holding the results of the Designer's runs, written as the user through `EnvironmentResolver.draft_store` (`CLIENT-ENVIRONMENT-RESOLVER`). Users get `USE SCHEMA` and `CREATE TABLE` on it and no `SELECT` or `MODIFY` on the schema, so each owns the tables they create and reads no one else's; a `BROWSE` grant shows table names, never rows. The agent's tools read it and write nothing (`SVC-MCP-TOOLS`). `results` holds only official results, written by whoever the implementer grants (`RUN-CLIENT-PUBLISHED`): through `official_store`, a run is written there only when it binds nothing but its environment's own resolvers, by rule, so every library is from the environment's knowledge, read by name or from its location `store:<catalog>.knowledge`, every value set from its terminology and every retrieve from its data. A run that binds CQL text, `%%cql` cells, a folder library, a volume, a DataFrame or a custom resolver is refused for `results` before it computes, naming what it bound; it may write `draft_results`, a schema of the caller's own or the session's temporary tables (`CLIENT-SESSION-RESULTS`). `%%cql` and `cql()` keep nothing. Retention (`RUN-CLIENT-MAINTENANCE`) keeps `draft_results` under the limit of 10,000 tables per schema; a create refused because the schema is full fails, saying to shorten retention or run the cleanup. No user has a schema of their own from the deploy. |
| `RUN-CLIENT-RESULT-OWNERSHIP` | A run finds a result through its result resolver (`CLIENT-RESULT-RESOLVER`), in an environment in two layers (`CLIENT-RESOLVER-OVERLAY`), one statement each, the second only for what the first did not find: first the caller's own, in `draft_results` or the schema a store writes, then the official ones, in the environment's `results`, whoever wrote them; none found means the run computes. Own results: the writer owns the table it creates, and its name carries the writer's digest (`RUN-CLIENT-TABLE-NAMES`), so two identities computing one calculation write two tables; the lookup matches the caller's digest in the name and the caller as the table's owner, and another identity's table in an own schema is never reused, whatever the caller can read, because row filters, column masks and views that depend on the caller are not part of a result's identity. Official results: `results` is trusted by construction (`RUN-CLIENT-PUBLISHED`); its result tables carry the row filters and column masks of the data they derive from, a deployment concern, so a reader sees its own rows of a shared result. Of several official layers, the earlier wins, then the latest computation in it. Two runs of one identity computing one result write the same rows to the same table. Nothing is looked up again before a write, so both compute it; after a failed write the writer looks the result up again and reuses it if it is done, so a collision costs duplicate work, never a wrong result or a failed run. |
| `RUN-LINEAGE` | Unity Catalog captures lineage per query for SQL interfaces and `CREATE TABLE AS SELECT`. |

## Example content

The example measures and patients in `examples/`.

| ID | Requirement |
|---|---|
| `CONTENT-EXAMPLE-PRELOAD` | `examples/` is the one place that pre-loads a workspace with example measures. The deploy loads its CQL as FHIR `Library` resources and each example measure's authored `Measure` resource and its value sets into the default environment's `knowledge`, and the synthetic small population (`DATA-SYNTH-SMALL`) into its `data`, as tables and as files in their `files` volumes (`DEPLOY-DEFAULT-ENVIRONMENT`), with no expected results. Nothing outside `compiler/` reads the compiler's conformance corpus. The corpus keeps its own copy of each example measure's CQL and value sets, changed in lock step by hand, and unmodified copies of `FHIRHelpers.cql` in `cql/measures/` and `cql/fhir/`, changed in lock step with `examples/measures/FHIRHelpers.cql` (`LANG-FHIRHELPERS-FROM-CONTENT`). Its patients are its own: 48 hand-written patients and their answer keys. Nothing reads one copy from the other, so they may diverge. |
| `CONTENT-EXAMPLE-STANDARDS` | The example measures in `examples/` follow the published FHIR quality measurement standards and use only open-source, license-free content. Each is written against QI-Core 6.0.0 and includes FHIRHelpers 4.0.1, which `examples/measures` distributes unmodified. Each has an authored FHIR `Measure` with proportion scoring, the CQF Measures populations (initial population, denominator, exclusions, exceptions, numerator), improvement notation and supplemental data, and takes the measurement period at run time. Its value sets are the repository's own, with codes only from code systems free to use (ICD-10-CM, ICD-10-PCS, HCPCS Level II, LOINC under its license, CVX, HL7 terminology) or local codes: no CPT, no SNOMED CT, and no published measure, value set or library. |

## Library registry

Measures and libraries as governed, versioned content.

| ID | Requirement |
|---|---|
| `KNOWLEDGE-FAIL-LOUD` | Knowledge read failures propagate rather than returning empty results; failed loads are retried on next access and never cached. |
| `REG-CRUD` | Draft → validate (clean diagnostics) → publish (immutable, indexed) → fork; performed as the signed-in user. |
| `REG-CURATION` | Seed marketplace with trusted publishers (standards bodies, quality vendors, centers of excellence); open publication later once community trust is load-bearing. |
| `REG-DISCOVERY` | Search/browse the Delta index by name/tags/resources/value sets/author/status; no separate search service. |
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

