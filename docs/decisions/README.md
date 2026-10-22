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
| `DEC-ADDRESS-NO-DEPLOYMENT` | A library version's results live at `{catalog}.{library}_{version}`, with no deployment term. Inputs are told apart by `__eval_id`. |
| `DEC-AGENT-DESIGNER-CYCLE` | The Designer depends on the agent, never the reverse. The Designer registers its own tools into the agent. |
| `DEC-BINDINGS-IN-CLIENT` | Data Bindings is a subpackage of the `cql-databricks` client (`cql_databricks.data_bindings`), not its own distribution. |
| `DEC-CLIENT-PER-FEATURE` | One Foundation client distribution with per-feature subpackages under the `cql_databricks` namespace. |
| `DEC-COMMON-HOME` | `cql_databricks_common` is a small shared server library. It is not public interface, and the client doesn't use it. |
| `DEC-CLIENT-PUBLISHES-DATA-SOURCE` | The client, not the loader, publishes a data source over tables: it writes the declaration (schema, `retrieve_<Type>` functions, tag), never data. The loader keeps writing FHIR data, content and value sets (`BIND-PUBLISH`, `BUILD-SYSTEMS`). |
| `DEC-CONSOLE-TOPOLOGY` | One App per feature that serves requests, each serving its own UI and API in one process. |
| `DEC-CONTENT-FEATURE` | Retired: content split into Data Bindings (the resolver) and the loader (importer, synthesizer). See `DEC-DATA-BINDINGS-HOME`. |
| `DEC-CONTENT-LOADERS-AND-RESOLVER` | Tagged UC tables are the store of record for content. No separate store, database or App. |
| `DEC-DATA-BINDINGS-HOME` | Retired: Data Bindings is part of the client, not a feature of its own. The importer and synthesizer are still the loader, run by users. See `BINDINGS-INTO-CLIENT`. |
| `DEC-DATA-SOURCE-UC-TAG` | A data source is a UC schema tagged `cql_databricks_data_source`. The compiler owns its layout. No feature stores data-source records. |
| `DEC-DESIGNER-ONE-MODULE` | The Designer has one module. Sharing, Mapping, Scheduling and Data Readiness are not part of it. |
| `DEC-DRAFT-ADDRESS` | Results of CQL that isn't a published library version go to one common drafts schema, in tables named by a hash of the CQL. |
| `DEC-ENGINE-PLUGIN` | The client finds engines through the `cql_databricks.engines` entry-point group. None ships today. |
| `DEC-ENGINE-SESSION-OPTIONS` | Engine options are session settings, set once up front with the same precedence as `BUILD-RESOLVE`, so each call is just `cql(...)`. |
| `DEC-EVAL-ID-LOOKUP` | Retired with the evaluator's Lakebase lookup. Result rows still carry `__eval_id`. See `DEC-EVAL-ID-TERMS`. |
| `DEC-EVAL-ID-TERMS` | `__eval_id` hashes every input that changes the answer: data source, terminology, period and other parameters, each in canonical form. |
| `DEC-EVALUATOR-APP-REHOME` | What the Python evaluator App did moved into the client and the Designer server, run as the signed-in user. See `RESULT-INTO-CLIENT`. |
| `DEC-EVALUATOR-OPAQUE` | Retired: there is no evaluator API. The client derives result addresses itself. See `DEC-STATELESS-SERVICES`. |
| `DEC-FEATURE-ORDER` | Feature order, lowest first: loader, compiler (with the client), agent, designer. |
| `DEC-KNOWLEDGE-LAYOUT-HOME` | Data Bindings owns and checks the knowledge and terminology layouts. The loader writes them. |
| `DEC-LIBRARY-OWNS-RESULTS` | A library version owns its results. A shared library's defines are computed once, in its own schema, for every measure that includes it. |
| `DEC-NO-CONVENTION-NAMES` | Data and value sets are found by tag only. The UC role tag (`cqldatabricks.role`) and the default names (`fhir`, `fhir.valueset_expansion`) are removed (`BUILD-RESOLVE`, `CLIENT-EXEC-DISCOVERY`). |
| `DEC-ONE-VERB-ENGINE-OPTION` | Retired: there is no evaluator engine. `cql()` runs on the caller's compute. See `DEC-STATELESS-SERVICES`. |
| `DEC-POINT-UC-FUNCTION` | Retired: point evaluation runs in a separate point App, not a UC function. See `POINT-EVALUATOR-APP`. |
| `DEC-PUBLISHED-TEST` | CQL is the published version it names when its source equals the corpus's, line endings aside. Any other difference makes it a draft. |
| `DEC-RESULT-IDENTITY` | A result is identified by what it computes: its emitted SQL, its parameter values, the identities of the defines it reads, and the bound data source (`RUN-CLIENT-RESULT-IDENTITY`). Not by where it is stored or who ran it, which can change, nor by the compiler version, since any compiler change that matters changes the SQL. Freshness is the data source's convention (serve as-of data), not part of the hash. Runtime-function fixes are handled by release notes that say to refresh everything, so a compatible runtime change never forces a refresh. A non-deterministic define is never reused. |
| `DEC-RESULT-SCOPE` | Results are written to the caller's destination, which is read first, then the `catalog.schema` scopes in order (`CLIENT-DISCOVERY-SCOPE`). The destination stays a separate argument, not the first scope. The scope is the trust boundary: listing a schema sanctions every result in it, so a result is never checked against its table owner or publisher. Don't add such a check. A table owner can hand a table to anyone, so results are read only from `catalog.schema` scopes, never a whole catalog, and a shared result schema grants write access only to its publishers. Amended 2026-10-11: the destination is separate, and the table-owner check is removed. |
| `DEC-RENAME-DESIGNER` | The application is the Designer, formally "CQL-on-Databricks Designer". |
| `DEC-RUN-LOG-FAILURE` | The run log is written in the background and never delays a run. A failed write is logged at ERROR with the run ID. |
| `DEC-RUN-LOG-LOCATION` | The run log lives in a deploy-created `designer` schema in the `designer_catalog` bundle variable. |
| `DEC-RUN-LOG-SCOPE` | The run log records Refresh and Automatic runs, including failures, with the measurement period. Reopening a measure isn't logged. |
| `DEC-RUN-LOG-SHAPE` | The run log is two tables, `designer.runs` and `designer.run_steps`, so a run that fails before any step still has a row. |
| `DEC-SAME-ANSWER` | One compiled artifact per library version serves every strategy, and conformance runs each supported strategy. |
| `DEC-SDP-ONLY-BULK` | Retired with SDP. Bulk runs are durable client calculations. See `RUN-CLIENT-DURABLE`. |
| `DEC-SESSION-MOUNT` | Session data belongs to the Spark session, not a client: every client on a session shares its temp namespace, so there is one mount per session, kept in the session record beside the `%%cql` cells (`CLIENT-SESSION-MOUNT`, `CLIENT-SESSION-STATE`). It replaces discovery. `mount` is the only way to bind session data; `register_retrieve_udtfs` was removed. |
| `DEC-SPA-OUTPUT` | The Designer serves its own SPA from `designer/dist`. No build is copied anywhere else. |
| `DEC-SPEC-SPLIT` | The Specification document model belongs to the Designer. Measure repository metadata belongs to Data Bindings. |
| `DEC-SPECIFICATION-SCOPE` | Content holds Library and Measure resources only. Specification overlays and other Designer customizations stay with the Designer. |
| `DEC-STATELESS-SERVICES` | The compiler service is stateless. Shared calling code lives in the Python client and runs as the caller. |
| `DEC-SUBJECT-PARAMETER` | Retired with `BIND-RETRIEVE-SUBJECT`: no caller scoped a compile to patients. It passed the patient list as one named parameter, `:__subject_ids`, a JSON array string, the only parameter marker the compiler emitted. |
| `DEC-TERMINOLOGY-UC-TAG` | The terminology table is found by the `cql_databricks_terminology` tag. Value sets arrive already expanded. |
| `DEC-TOOLCHAIN-VS-EXECUTION` | The compiler is the toolchain: it turns CQL into SQL and ships the runtime and conformance. The client runs that SQL as the caller (`RESULT-INTO-CLIENT`). |
| `DEPLOY-OWNS-PRINCIPALS` | The deploy creates the service principals that Jobs run as: the maintenance principal before `bundle deploy`, since it is the `cql-maintenance` Job's run-as. The compiler caller is retired with `SVC-COMPILER-M2M`. The client holds no deploy code. |
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
| `SHIPPED-SOURCES-IN-LOADER` | The data sources this repository ships are the loader's (`content_ingest`): the conformance data source and its decks, the shared FHIR mount reshape (`seed_fhir`), the change-log exemplar and the shipped sources' names. |
| `SPLIT-COMPILER` | The compiler split into `compiler/{core,service,server}`. |
| `SPLIT-CONTENT` | Content split into Data Bindings, in Foundation, and the loader. |
| `SPLIT-LAKEBASE` | Retired: no feature uses Lakebase. |
| `SPLIT-PACKAGES` | The single App package split into per-feature packages. |
