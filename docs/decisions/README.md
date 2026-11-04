# Decision and step IDs


Code cites these IDs. This file says what each one decided, so a citation always leads somewhere.
Requirements are in [`../requirements.md`](../requirements.md) and the feature registries it lists. Open
decisions are in the [roadmap](../roadmap.md).

| ID | What it decided |
|---|---|
| `AGENT-TOOL-REGISTRY` | The agent owns a tool registry. The Designer registers its Specification tools into it, and the agent never imports the Designer. |
| `API-CONTENT-ADDRESSED` | Expensive read routes are content-addressed GETs, so they can be cached. |
| `BINDINGS-INTO-CLIENT` | Data Bindings moved into `compiler/client/` and ships in the `cql-databricks` distribution. |
| `CALC-API` | Retired with the evaluator App's HTTP API. See `DEC-STATELESS-SERVICES`. |
| `CALC-CONSOLE` | Retired: the evaluator has no admin UI. Data sources are tagged schemas. |
| `CALC-ONLY` | A conformance comparison that times the calculation alone, without infrastructure (`MeasureCalcOnlySpec`). |
| `CARVE-CONTENT` | Content became the importer, the synthesizer and the resolver. Its separate store and registry were removed. |
| `CLIENT-RESOLVE` | One resolution path in the client: find a library closure by tag, read bindings and terminology, then compile. Every caller uses it. |
| `COMMON-LIBRARY` | `common/` (`cql_databricks_common`) is its own small library that the servers and apps share. |
| `DEC-AGENT-DESIGNER-CYCLE` | The Designer depends on the agent, never the reverse. The Designer registers its own tools into the agent. |
| `DEC-BINDINGS-IN-CLIENT` | Data Bindings is a subpackage of the `cql-databricks` client (`cql_databricks.data_bindings`), not its own distribution. |
| `DEC-CLIENT-PER-FEATURE` | One Foundation client distribution with per-feature subpackages under the `cql_databricks` namespace. |
| `DEC-COMMON-HOME` | `cql_databricks_common` is a small shared server library. It is not public interface, and the client doesn't use it. |
| `DEC-CLIENT-PUBLISHES-DATA-SOURCE` | The client, not the loader, publishes a data source over tables: it writes the declaration (schema, `retrieve_<Type>` functions, tag), never data. The loader keeps writing FHIR data, content and value sets (`BIND-PUBLISH`, `BUILD-SYSTEMS`). |
| `DEC-CONSOLE-TOPOLOGY` | One App per feature that serves requests, each serving its own UI and API in one process. |
| `DEC-CONTENT-FEATURE` | Retired: content split into Data Bindings (the resolver) and the loader (importer, synthesizer). See `DEC-DATA-BINDINGS-HOME`. |
| `DEC-CONTENT-LOADERS-AND-RESOLVER` | Tagged UC tables are the store of record for content. No separate store, database or App. |
| `DEC-DATA-BINDINGS-HOME` | Retired: Data Bindings is part of the client, not a feature of its own. The importer and synthesizer are still the loader, run by users. See `BINDINGS-INTO-CLIENT`. |
| `DEC-DATA-SOURCE-UC-TAG` | Superseded by `DEC-ENVIRONMENT`: an environment is a tagged catalog, and its data is a schema of standard name; no schema is tagged `cql_databricks_data_source`. |
| `DEC-DESIGNER-ONE-MODULE` | The Designer has one module. Sharing, Mapping, Scheduling and Data Readiness are not part of it. |
| `DEC-DESIGNER-PUBLISH` | A folder library is published into a tagged store by a Publish action in the Designer, and by the same function in the client, so a notebook or Job publishes the same way. The Designer edits only folders (`DOC-AUTHOR-IN-FOLDERS`); a store changes only by publishing or the import. |
| `DEC-ENGINE-PLUGIN` | The client finds engines through the `cql_databricks.engines` entry-point group. None ships today. |
| `DEC-ENGINE-SESSION-OPTIONS` | Engine options are session settings, set once up front with the same precedence as `BUILD-RESOLVE`, so each call is just `cql(...)`. |
| `DEC-ENVIRONMENT` | Every run is in one environment: a catalog, found by tag, holding data, knowledge (with terminology) and results in standard schemas, with published views to read and store tables to write (`CLIENT-ENVIRONMENT`, `DATA-ENVIRONMENT-LAYOUT`). A client given nothing runs in the default environment, so no business user is asked for one. Every dependency reaches a run through a resolver (`CLIENT-RESOLVERS`); the compiler stays pure, taking a rule or a per-call-site map per role (`BIND-CALL-SITE`). Rationale: [environments.md](environments.md). |
| `DEC-EVALUATOR-APP-REHOME` | What the Python evaluator App did moved into the client and the Designer server, run as the signed-in user. See `RESULT-INTO-CLIENT`. |
| `DEC-FEATURE-ORDER` | Feature order, lowest first: loader, compiler (with the client), agent, designer. |
| `DEC-KNOWLEDGE-LAYOUT-HOME` | Data Bindings owns and checks the knowledge and terminology layouts. The loader writes them. |
| `DEC-LIBRARY-OWNS-RESULTS` | A library version owns its results. A shared library's defines are computed once, in its own schema, for every measure that includes it. |
| `DEC-MODEL-SUPERSET-BOUND` | The compiler's superset (`DATA-MODEL-SUBSET`) is FHIR R4 for the modeled resources: every element, every `value[x]` slot, and `extension` and `modifierExtension` on every complex element with all of FHIR's extension value types, at any depth. `contained` and extensions on primitive values (`_birthDate`) are outside it and fail as not supported by the compiler. |
| `DEC-NO-CONVENTION-NAMES` | Superseded by `DEC-ENVIRONMENT`: an environment is found by catalog tag only, and inside it schemas and views have standard names (`DATA-ENVIRONMENT-LAYOUT`). The UC role tag (`cqldatabricks.role`) and the old default names (`fhir`, `fhir.valueset_expansion`) stay removed. |
| `DEC-NOTEBOOK-DEFAULT-RESOLVER` | A notebook's `connect()` with no resolvers named runs in the default environment (`CLIENT-ENVIRONMENT`, `CLIENT-ENVIRONMENT-RESOLVER`); it never picks among data sources. Rationale: [environments.md](environments.md). |
| `DEC-ONE-VERB-ENGINE-OPTION` | Retired: there is no evaluator engine. `cql()` runs on the caller's compute. See `DEC-STATELESS-SERVICES`. |
| `DEC-POINT-UC-FUNCTION` | Retired: point evaluation runs in a separate point App, not a UC function. See `POINT-EVALUATOR-APP`. |
| `DEC-PROVIDER-RELATION` | A retrieve gets its rows from the relation its data resolver returns, which names where they are and carries no mapping (`BIND-RELATION`). Data in FHIR shape, or a mapping SQL can express, is a Delta table or UC view; published data is an environment's views, bound by name (`BIND-PUBLISHED-VIEW`). A source chosen per retrieve is a custom resolver that picks the relation per call site, in notebooks and Jobs (`BIND-CALL-SITE`); the Apps read published views only. No table function is bound. A mapping that needs PySpark is materialized to Delta by the customer's pipeline or Job; a DataFrame is for development on Spark only. The SQL warehouse is the fast path and the only compute an App's user token reaches, so published data must be readable by SQL alone, and a DataFrame on a warehouse run fails, naming the resolver. Nothing is materialized behind the caller's back. Rationale and measurements: [providers.md](providers.md), [environments.md](environments.md). |
| `DEC-PUBLISH-IMMUTABLE` | A published `(url, version)` may be replaced. Publish upserts on `(url, version)`, as the import does (`CONTENT-INCREMENTAL`), so the Designer, the client and an admin's bulk import treat a version the same way. Knowledge stays in federated Unity Catalog tables, which customers may bring; nothing assumes a store of the Designer's own. |
| `DEC-PUBLISHED-STORE-LOCATION` | Published data and knowledge, and their stores, live in an environment's standard schemas (`DATA-ENVIRONMENT-LAYOUT`), not in schemas each customer names or in `runtime`. `runtime` stays the deployment's (`SVC-CATALOG-LAYOUT`). Rationale: [environments.md](environments.md). |
| `DEC-RESOURCE-VARIANT` | `__resource` stays a typed struct, not a `VARIANT`: FHIR R4 JSON with fields left out, never moved or renamed, with loop cuts instead of a depth cap and bounded extensions (`DATA-MODEL-STATIC-STRUCT`, `DATA-MODEL-SCHEMA`, `DATA-MODEL-CUTS`, `DATA-PATH-IN-SCHEMA`). Rationale: [resource-schema.md](resource-schema.md). |
| `DEC-RESULT-IDENTITY` | A result is identified by what it computes: its emitted SQL, its parameter values, the identities of the defines it reads, the bound data and terminology, and the codes of the value sets it uses (`RUN-CLIENT-RESULT-IDENTITY`). A value set is small and its version should be fixed content, so its codes are hashed; data is identified by name, since hashing it means reading it. Not by where it is stored or who ran it, which can change, nor by the compiler version, since any compiler change that matters changes the SQL. Freshness is the data source's convention (serve as-of data), not part of the hash. Runtime-function fixes are handled by release notes that say to refresh everything, so a compatible runtime change never forces a refresh. A non-deterministic define is never reused. |
| `DEC-RESULT-SCOPE` | A run finds results in two layers, through its result resolver (`RUN-CLIENT-RESULT-OWNERSHIP`): the caller's own, owner-only, then the environment's official `results`, whoever wrote them. Own results stay owner-only because Unity Catalog shows a caller only the tables it holds a privilege on, and the same view can return different rows to different callers, so a fingerprint match across identities proves nothing about the rows. `results` is different by construction: it is the authority, like the published views, and the implementer decides who writes it through grants, so whatever is there is official and needs no owner or publisher check. Its tables carry the row filters and column masks of their source data, a deployment concern; its writers must read the data unfiltered. The resolver owns the lookup chain so no run composes layers, and the environment builds it (`CLIENT-ENVIRONMENT-RESOLVER`). See [environments](environments.md). |
| `DEC-RENAME-DESIGNER` | The application is the Designer, formally "CQL-on-Databricks Designer". |
| `DEC-RUN-LOG-FAILURE` | The run log is written in the background and never delays a run. A failed write is logged at ERROR with the run ID. |
| `DEC-RUN-LOG-LOCATION` | The run log lives in a deploy-created `designer` schema in the `designer_catalog` bundle variable. |
| `DEC-RUN-LOG-SCOPE` | The run log records Refresh and Automatic runs, including failures, with the measurement period. Reopening a measure isn't logged. |
| `DEC-RUN-LOG-SHAPE` | The run log is two tables, `designer.runs` and `designer.run_steps`, so a run that fails before any step still has a row. |
| `DEC-SAME-ANSWER` | One compiled artifact per library version serves every strategy, and conformance runs each supported strategy. |
| `DEC-SDP-ONLY-BULK` | Retired with SDP. Bulk runs are durable client calculations. See `RUN-CLIENT-DURABLE`. |
| `DEC-SESSION-MOUNT` | Retired: session data is a DataFrame or volume data provider, and a dev override is an `Overlay`. See `DEC-PROVIDER-RELATION`. |
| `DEC-SPA-OUTPUT` | The Designer serves its own SPA from `designer/dist`. No build is copied anywhere else. |
| `DEC-SPEC-SPLIT` | The Specification document model belongs to the Designer. Measure repository metadata belongs to Data Bindings. |
| `DEC-SPECIFICATION-SCOPE` | Content holds Library and Measure resources only. Specification overlays and other Designer customizations stay with the Designer. |
| `DEC-STATELESS-SERVICES` | The compiler service is stateless. Shared calling code lives in the Python client and runs as the caller. |
| `DEC-SUBJECT-PARAMETER` | Retired: no caller scoped a compile to patients. It passed the patient list as one named parameter, `:__subject_ids`, a JSON array string, the only parameter marker the compiler emitted. |
| `DEC-TERMINOLOGY-UC-TAG` | The terminology table is found by the `cql_databricks_terminology` tag. Value sets arrive already expanded. |
| `DEC-TOOLCHAIN-VS-EXECUTION` | The compiler is the toolchain: it turns CQL into SQL and ships the runtime and conformance. The client runs that SQL as the caller (`RESULT-INTO-CLIENT`). |
| `DEPLOY-OWNS-PRINCIPALS` | The deploy creates the service principals that Jobs run as: the maintenance principal before `bundle deploy`, since it is the `cql-databricks-designer-maintenance` Job's run-as. The client holds no deploy code. |
| `DERIVATIONS-TO-LIBRARY` | The explain, flow, strata and metrics derivations live in the client (`cql_databricks.result.insights`). The Designer and agent call them there. |
| `DESIGNER-ONE-MODULE` | The Designer keeps one module, for authoring and testing. The other modules were removed. |
| `DESIGNER-RUNS-SDP` | Retired with SDP. The Designer runs CQL through the client. See `RUN-CLIENT-MIGRATION`. |
| `EVALUATOR-REMOVE` | The Python evaluator App was removed. Code other features import moved into the evaluator's client, then out of it with `EVALUATOR-RETIRE`. |
| `EVALUATOR-RETIRE` | The evaluator feature is removed: the point client, the point service and the point App. Its other code moved to its owners (`RESULT-INTO-CLIENT`, `SHIPPED-SOURCES-IN-LOADER`). The last version is on the `feature/evaluator` branch. |
| `MERGE-COMPILER-FEATURE` | `runtime/`, `client/` and `conformance/` moved under `compiler/`. Published jar names stayed the same. |
| `MOVE-DERIVATIONS` | The result derivations moved out of the Designer server. See `DERIVATIONS-TO-LIBRARY`. |
| `POINT-APP-RENAME` | Retired with the point App. See `EVALUATOR-RETIRE`. |
| `POINT-BRANCH-SPLIT` | Point evaluation moved to the `feature/evaluator` branch. `compiler/cqf` stays on `main` as the conformance oracle. See `EVALUATOR-RETIRE`. |
| `POINT-CQF-UC-FUNCTION` | Retired: running the CQF engine as a UC function was not adopted. See `POINT-EVALUATOR-APP`. |
| `POINT-EVALUATOR-APP` | Retired: the point App is removed. The CQF engine (`compiler/cqf`) remains the conformance oracle. See `EVALUATOR-RETIRE`. |
| `RESULT-INTO-CLIENT` | Durable bulk calculations, result reading, the result views and the private-result expiry are `cql_databricks.result`, shipped in the `cql-databricks` client. Listing data sources is `cql_databricks.compiler.data_source`; the knowledge-view bind and the data-source declaration are `cql_databricks.data_bindings`. |
| `RENAME-DESIGNER` | The application is named the Designer throughout the repository. |
| `SCRIPTS-BY-OWNER` | A script lives with what it serves: the deploy's in `scripts/deploy/`, Job entry points in `scripts/jobs/`, the data loaders in `loader/scripts/`, eCQM vendoring in `compiler/conformance/ecqm/`, the Designer's fixture build in `designer/scripts/`. `scripts/` keeps dev tooling. |
| `SHIPPED-SOURCES-IN-LOADER` | Retired: the data sources this repository ships are the deploy's, not the loader's. See `SPLIT-IMPORTER`. |
| `SPLIT-COMPILER` | The compiler split into `compiler/{core,service,server}`. |
| `SPLIT-CONTENT` | Content split into Data Bindings, in Foundation, and the loader. |
| `SPLIT-IMPORTER` | The loader has no importer. `import_measures` is in the client, as `cql_databricks.data_bindings.measure_import` (`CONTENT-IMPORT-MEASURES`). The example preload and the shipped sources' lander are the deploy's, in `scripts/deploy/shipped_sources` (`DEPLOY-SHIPPED-SOURCES`). The import skill is one of the agent's skills (`CONTENT-IMPORT-SKILL`). |
| `SPLIT-LAKEBASE` | Retired: no feature uses Lakebase. |
| `SPLIT-PACKAGES` | The single App package split into per-feature packages. |
