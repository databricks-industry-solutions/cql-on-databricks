# Roadmap — CQL on Databricks

The plan, open decisions (`DEC-*`) and known gaps. The rest of `docs/` says what is true now; this file says
what is not done yet. Requirement IDs in it (`CLIENT-*`, `LANG-*`, `DATA-*` and the rest) are defined in
[requirements.md](requirements.md) and the feature registries it lists.

The main scenario is a customer running CQL through the Python client in their own notebooks and Jobs.
The Designer is an extra. The steps below are in the order they're done.

## Release plan

### v0.3

| Step | What | After | Status |
|---|---|---|---|
| `CLIENT-DISCOVERY-SCOPE` | Discovery of data sources, value sets, libraries and results reads only the caller's ordered list of catalog scopes. Earlier scopes win | — | Built. Results are written to the destination and read from it, then from the `catalog.schema` scopes, with no owner check (`DEC-RESULT-SCOPE`). Scopes are set with `connect(catalog_scopes=...)` or `CQL_DATABRICKS_CATALOG_SCOPES` |
| `CLIENT-KNOWLEDGE-INCLUDES` | `cql()` and durable calculations include a library by name from tagged knowledge (`SVC-NOTEBOOK-LIBRARY-FOLDER`). Folders under `/Workspace` and `/Volumes` already work | — | Built: cells, then the folder, then tagged knowledge in scope. Checked live by the release test: each example measure and its includes resolve from tagged knowledge (`CONTENT-EXAMPLE-PRELOAD`) |
| `RUN-CLIENT-RESULT-IDENTITY` | A result's identity is its emitted SQL, parameter values, upstream identities and bound data source, nothing else (`RUN-CLIENT-RESULT-IDENTITY`, `DEC-RESULT-IDENTITY`). One hash replaces the fingerprint, calculation, upstream, shape and other identity hashes. Marks the requirements it replaces | `CLIENT-DISCOVERY-SCOPE` | Built. The compiler computes the fingerprint; a result carries four tags and is found by its table name in the destination and each result scope. `population` and the publisher and hash search filters are removed from the API. Every stored result recomputes once |
| `CLIENT-INCLUDED-REUSE` | A define compiles to the same SQL, recipe name and cut point whichever library includes it, so the including library reuses the included library's stored results (`EMIT-ROOT-INDEPENDENT`, `DEC-LIBRARY-OWNS-RESULTS`) | `RUN-CLIENT-RESULT-IDENTITY` | Built. Recipes are named and attributed by their owning library, and no retrieve argument names a library. A measure alone and inside a library that includes it give the same recipes, with the same names, SQL and fingerprints (`IncludedReuseSpec`). Every stored result recomputes once and retrieve UDTFs drop their `library` parameter |
| `CLIENT-RELEASE-TESTS` | A live Job test from the built wheels: discover by tag, run a measure durably, compare with its answer key. Then include the measure in a new library, add a define, and check the upstream results are reused. Offline tests that discovery scopes confine each kind of lookup | `CLIENT-INCLUDED-REUSE` | Not tested live. The live release test (`examples/tests/test_release_live.py`) was removed 2026-10-21 with the example patients' expected results: the example set is now the synthetic population small, which ships no answer key (`DATA-SYNTH-SMALL`). Nothing runs a measure live from the built wheels, checks reuse on a second run, or checks a `%%cql` library that includes a measure. Offline confinement tests cover every kind |
| `SVC-NOTEBOOK-SESSION-SCOPE` | `%%cql` cells and `cql("...")` strings with no `library` header join one library per Spark session. Later fragments see earlier defines by name, and redefining a name replaces it. The compiler merges fragments (`merge-cql`) | — | Built 2026-10-21, not deployed. Tested offline and against the local compile host. Next: redeploy the compiler App and the UC compiler function, which serve `merge-cql`, then run notebooks 01 and 02 live. Until then, header-less CQL fails on a deployed workspace |

### After v0.3

| Step | What | After | Status |
|---|---|---|---|
| `DATA-SYNTH-MARKETPLACE` | Publish a pre-generated synthetic population as a free Databricks Marketplace listing, and install and tag it on deploy, so a fresh deployment has a large demo source beside the example patients (`DATA-SYNTH-MARKETPLACE`, `DATA-SYNTH-LARGE`) | v0.3 | Open |
| `APP-NARRATIVE-OVERLAY` | Generate a narration for any library on request, check it against `specification.xsd` and the run-time reference set before showing it, and cache it per user in a Delta table (`APP-AI-CACHE`, `SPEC-EXPLAIN-INPUTS`, `SPEC-EXPLAIN-VOICE`, `SPEC-EXPLAIN-VIEW`, `SPEC-EXPLAIN-AGAIN`). Accepting a narration back into CQL tags is not part of it | v0.3 | Superseded 2026-10-30 by `DOC-EXPLAIN-REMOVE`, and removed. It was deployed but never run against a real model endpoint |
| `AGENT-CARE-GAPS` | MCP tools that compile CQL probes against a measure and compute them in bulk through the client, as the caller. Skills for the care-gap workflow. A recorded session on synthetic data showing Genie drive it (`APP-CARE-GAPS`, `APP-PROBE`, `SVC-AGENTS`, `SVC-MCP-SKILLS`) | v0.3 | Open. Starts by amending `SVC-MCP-TOOLS`, which is read-only today |
| `RUN-CLIENT-MEASURE-SCORE` | The client scores a measure; populations are counted as written and only the score is cascaded | — | Built 2026-10-29 in the client (`insights/score.py`) and used by the Designer: populations are raw counts, each a plain funnel bar, and the header rate is the client's score. Tested offline. Not yet checked against the CQF reference: conformance runs on the JVM and the scorer is Python, so a test needs a way to run one against the other's `MeasureReport` |
| `CLIENT-INLINE-WAREHOUSE` | Run CQL inline on a SQL warehouse session with temporary views only, and keep nothing | — | Built 2026-10-29, tested offline against a fake warehouse. Not run on a workspace |
| `AGENT-RESEARCH-TOOLS` | MCP tools and skills for a population question asked from Genie One: find a definition, reuse a saved result, explain the CQL, and run experiments inline that save nothing (`AGENT-DEFINITION-DISCOVERY`, `AGENT-VALUE-SET-LOOKUP`, `AGENT-SAVED-RESULTS`, `AGENT-INLINE-EXPERIMENTS`, `AGENT-CQL-REVIEW`) | `RUN-CLIENT-MEASURE-SCORE`, `CLIENT-INLINE-WAREHOUSE` | Built and deployed 2026-10-29. On the workspace, every research tool runs as the caller, and inline experiments write nothing (query history: all statements as the user, no writes). The skills are published as UC skills. Genie One cannot reach the agent per user yet: the deploy creates its per-user connection and OAuth client, but only when an account admin runs it (`DEPLOY-AGENT-REGISTRATION`). Not yet run. Registering a Databricks App under Unity Gateway is unsupported until AIGOV-487 |
| `DOC-WRITE-SCOPE-CHECK` | Find out whether the Designer can write a library's CQL as the signed-in user: to a `/Workspace` folder and a `/Volumes` folder (scopes `workspace.workspace`, `files`), and to a tagged knowledge table (`sql`) | — | Done 2026-10-30 for folders. With both scopes on the App, the user's forwarded token wrote, read back and deleted a `.cql` file in `/Workspace/Users/<user>/` through the Workspace API and in a Volume through the Files API. The Files API refuses `/Workspace` paths, so each folder kind has its own API. Which of the two scopes each write needs was not isolated. A tagged knowledge table takes the user's writes through the warehouse (`sql`): on 2026-10-30 a Designer save inserted, updated and refused rows as the user (`DOC-OPEN-SAVE`) |
| `DOC-EXPLAIN-REMOVE` | Remove the Explain toggle and menu, the library-wide narration and its cache table, and the per-section "Explain again" badge. The page shows only the author's words; the assistant explains ad hoc (`SPEC-EXPLAIN-VIEW`, `SPEC-EXPLAIN-AGAIN`, `APP-NARRATIVE-OVERLAY`) | — | Built and deployed 2026-10-30. The toggle, menu, badge, banner, generate route, cache store and deploy step are gone, and `xmlschema` is test-only. The empty `designer.narrations` table earlier deploys created was dropped by hand on the workspace |
| `DOC-OPEN-SAVE` | New, Open, Save and Save As for a library, in tagged knowledge stores and workspace or Volume folders. Save overwrites in place as the user, published versions included, and refuses if the source changed since it was opened. Save As makes a draft copy linked to its original (`APP-DERIVE-MEASURE`) | `DOC-WRITE-SCOPE-CHECK` | Partly built, deployed 2026-10-30. Built: locations in every library address (`RUN-ADDRESS-LOCATION`) across the client, the Designer's routes and the agent's tools; Open from a store or a browsed workspace folder; Save in place to a folder (rewriting the package's JSON copies, asking before deleting its XML and HTML) or a store (guarded MERGE); New measure, New library and Save As, into a folder or a store, under the deployment's `canonical_base`, also from an empty catalog. Checked on the workspace through the API, as the user: New, Save, Save As, the stale-copies prompt, and the conflicts each refuses, in a folder and in a store. Not checked in a browser. Not built: Import CQL; Volume folders |
| `DOC-SECTION-EDIT` | Every section's documentation is editable and saved as CQL tags, in strict CommonMark (no tables, no raw HTML, links to defines as `[text](<#Define Name>)`), parsed with markdown-it and markdown-it-py. Overview fields are root tags and win over the Measure resource (`APP-NARRATIVE-EDIT`, `SPEC-NARRATIVE-TAGS`) | `DOC-OPEN-SAVE` | Open |
| `DOC-SECTION-EXPLAIN` | An Explain action on each section drafts that section's tags with AI, checked, into the editor. Nothing changes until the author saves (`SPEC-EXPLAIN-VOICE`, `SPEC-EXPLAIN-ACCOUNTING`) | `DOC-SECTION-EDIT` | Open |
| `DOC-CQL-EDIT` | Edit a library's CQL in place in the Specification, one define at a time, with the whole-library editor for the rest. AI proposes a change to a define as a diff the author accepts | `DOC-OPEN-SAVE` | Partly built 2026-10-30, not deployed. Built: the whole-library editor (Monaco, loaded on first use), live recompile of the edit, Save and Cmd/Ctrl+S, unsaved-change guards. Built, not deployed, checked in a local browser on 2026-10-30: editing a define in place in the Specification. Not built: the AI-proposed change shown as a diff |
| `DOC-EDIT-PERMISSION-CHECK` | Find out how the Designer can learn, as the signed-in user and before a library opens, whether the user can write it: in a `/Workspace` folder, counting access through groups, and in a tagged knowledge table | — | Partly done 2026-10-30, as a workspace admin, so nothing a non-admin sees was checked. Folders: no call states the caller's own level. `workspace get-status` carries none, and `permissions/directories/{id}/permissionLevels` lists the levels that exist. `GET permissions/directories/{id}` returns the ACL with inherited entries marked, from which the user's level is the highest entry naming the user or one of their groups (`current-user me` lists them). The API reference does not say who may read an ACL, and it needs the `access-management` scope, which the Designer App does not request. Creating or deleting items in a folder needs CAN_MANAGE on it; editing a file needs CAN_EDIT. Stores: `information_schema.table_privileges` filtered to `current_user()` or `is_account_group_member(grantee)`, with `information_schema.tables.table_owner`, gives the user's privileges, inherited ones included, through the `sql` scope the App already has. `grants get-effective` also counts group grants but needs the `unity-catalog` scope. Workspace-local groups (`users`) hold no Unity Catalog grants. Neither API reference says what a caller without MANAGE may read |
| `DOC-SCOPE-OPEN` | Open a folder or a store as a scope; the left nav lists its libraries, and includes from outside it open as references | `DOC-OPEN-SAVE` | Built 2026-10-30, not deployed, tested offline; not checked in a browser. Built: a folder holds every library at any depth, for the Designer and the notebook client alike; the left nav's Scope tab; New and Save As default to the open folder scope. Includes from outside the scope already open read-only (`DOC-CQL-EDIT` offers Edit on the open library alone) |
| `DOC-EDITABLE-IN-SCOPE` | Open a library read-only, with the reason, when it is a reference or, in a store, the user cannot write it | `DOC-SCOPE-OPEN`, `DOC-EDIT-PERMISSION-CHECK` | Built 2026-10-30, not deployed. A store the user cannot write opens read-only with the reason (`GET /api/locations/access`): no Edit, a read-only whole-library editor, Save disabled. Checked in a local browser as an admin, writable for real and read-only with the answer faked; the check as a non-admin was not run. Folders are not asked, by decision |
| `DOC-SCOPE-DRAFTS` | Unsaved drafts per library across the scope, compiles against in-scope drafts, Save all | `DOC-SCOPE-OPEN`, `DOC-CQL-EDIT` | Open |

## Care-gap agents

A measure says who failed. It doesn't say why, or what would close the gap. An agent can write CQL probes
derived from the measure's own CQL to explain each failure, and compute them in bulk through the client.
The agent explains and assembles. Acting on patients stays downstream (`APP-CLINICAL-BOUNDARY`).

## Open decisions

| ID | Question |
|---|---|
| `DEC-ADHOC-COMPUTE-TYPES` | Which compute types ad-hoc runs support, each proven by conformance on that type |
| `DEC-RESOURCE-VARIANT` | Should `__resource` be a `VARIANT` the compiler reads with typed paths from the model, instead of a typed struct (`DATA-MODEL-STATIC-STRUCT`)? For: any FHIR JSON publishes with `parse_json`, there is no nesting cap, sparse data costs nothing, and plans carry no wide structs. Against or unknown: per-row cost unless the variant is shredded, pushdown on variant paths, the `cql_*` functions that take structs, and conformance on the new encoding. Next: a spike running an authored measure on the synthetic population in both encodings, on serverless and a SQL warehouse, comparing time, bytes scanned and plan size |

## Known compiler gaps

Found 2026-10-21 while writing the QI-Core example measures (`CONTENT-EXAMPLE-STANDARDS`), except "One context per
library" and "String functions", found building the session library (`SVC-NOTEBOOK-SESSION-SCOPE`). The Java CQF reference handles
each one. The example measures avoid all of them; the last column says how. Silent gaps come first: they give a
wrong answer with no error.

| Gap | What happens | Requirement | How the examples avoid it |
|---|---|---|---|
| One context per library | **Silent.** The compiler lowers every define in the library's first declared context (`CompileService.resolveContext`), ignoring each define's own ELM context. A define written before `context Patient` is `Unfiltered` by the spec, but runs per patient and reads `retrieve_Patient`. The session library makes this easy to hit: a context-free cell followed by a `context Patient` cell | `LANG-CONTEXT` | Every sample define follows `context Patient` |
| Choice value as a function argument | **Silent.** A FHIR choice element (`Observation.effective`) passed to a function whose parameter is a choice (`Choice<DateTime, Interval<DateTime>>`) lowers to a constant null. Every test on it is null or false. Breaks `CORE-NO-SILENT-MISLOWER` | `LANG-CHOICE` | The function takes the resource instead (`latestResult(LaboratoryResultObservation)`) |
| List aggregate functions | **Built 2026-10-21** (all 15, gated by `AggregateFunctions`, `FhirAggregateMostRecent` and the vendored `CqlAggregateFunctionsTest`). Was: they failed at Normalize, only `Count` was built. The examples still use the workaround; switching them to `Max` is a separate change | `LANG-AGGREGATE-FUNCTIONS` | "Most recent" keeps the item with no later item in the list, then takes `First(... sort by id)` |
| Sort key on a choice or `$this` | `sort by` a choice element (`effective`) or a fluent call on `$this` fails in Spark with `FIELD_NOT_FOUND`. This is the usual "most recent" form, `Last(... sort by ...)` | `LANG-QUERY-SORT` | As above: no sort on the time |
| Overloaded functions | Two functions with the same name and different argument types fail loudly ("type-based overload resolution not yet supported") | `LANG-FUNCTION` | Distinct names (`latestReading`, `latestResult`) |
| Date and time components | `year from X` fails at Normalize | `LANG-DATETIME-COMPONENT` | The flu season is interval arithmetic on the period start |
| Choice element off a union define | A choice-typed fluent function reading `onset` on a define that unions two condition profiles, read by another query, fails in Spark with `FIELD_NOT_FOUND` `onset` | `LANG-CHOICE-MODEL` | The union is written inside the query that reads it |
| Returned date-time offset | A define holding a date-time computed from the offset-less measurement period returns no offset; the reference returns `+00:00` | `LANG-DATETIME-OFFSET` | The flu season is a function, not a define |
| Field demand off a nested element | The field-demand analysis files a field read off a nested element under the element's type (`Extension`, `Observation.Component`) instead of the resource's. It happens for `Patient.birthsex` and for `"A Define".component` on a define holding one resource. `MaterializePolicySpec` catches it. A demand-bounded mount or a typed-subset binding check could then drop the field, and the value would be null with no error | `EMIT-SQL-RETRIEVE-DEMAND-BOUND` | Supplemental sex reads `Patient.gender`, not the birth sex extension. Blood pressure components are read through a query alias over the readings |
| Nested extensions | US Core race and ethnicity are nested extensions, which the data model drops (`extension` is kept at resource level only) | `DATA-MODEL-SCHEMA` | The supplemental data reports gender and payer only |
| String functions | `Upper(...)` and string concatenation with `+` fail at Normalize (`UnknownExpr`) | None yet | Not used |

Published measures ran during development. Whether they use any of these forms is checked outside this
repository, against that content.

## Deferred

Not on the plan.

- Deploy hardening (`docs/deploy.md` documents each as a manual step
  for now): grant the App service principals on every catalog in `catalog_scopes`, not only `uc_catalog`
  (`DEPLOY-APP-GRANTS`, `CLIENT-DISCOVERY-SCOPE`); grant them `CAN_USE` on the warehouse; a preflight that
  checks catalogs, the warehouse, the groups and the imported content before the build; checksums for the
  JRE and jars fetched at deploy and App start; pinned App Python dependencies; release tags.
- Live checks of non-admin private provisioning (`RUN-CLIENT-PRIVATE-PROVISIONING`) and the maintenance Job
  (`RUN-CLIENT-MAINTENANCE`), then unpausing the Job's schedule.
- Concurrency, retention and scale acceptance for durable calculations (`CLIENT-DURABLE-VALIDATE`).
- Retention for orphaned results.
- One Foundation deploy and one Foundation client (`FOUNDATION-ONE-DEPLOY`, `FOUNDATION-ONE-CLIENT`), and
  an automated feature-boundary check (`CHECK-FEATURE-DEPS`).
- The Designer opening and saving files in a workspace folder (`APP-WORKSPACE-FOLDER`).
- Natural-language authoring (`APP-CREATE-NL`), review threads (`APP-DISCUSSION`) and document export
  (`REND-EXPORT`).
- Sharing one resource retrieval within a define (`EMIT-RETRIEVE-ONCE`).
- Lint and type checks, coverage, the remaining upstream operator tests, the CQL support table.
