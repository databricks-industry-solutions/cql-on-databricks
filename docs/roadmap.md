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
| `CLIENT-KNOWLEDGE-INCLUDES` | `cql()` and durable calculations include a library by name from tagged knowledge (`SVC-NOTEBOOK-LIBRARY-FOLDER`). Folders under `/Workspace` and `/Volumes` already work | — | Built: cells, then the folder, then tagged knowledge in scope. Checked live by the release test: each sample measure and its includes resolve from tagged knowledge (`CONTENT-SAMPLE-PRELOAD`) |
| `RUN-CLIENT-RESULT-IDENTITY` | A result's identity is its emitted SQL, parameter values, upstream identities and bound data source, nothing else (`RUN-CLIENT-RESULT-IDENTITY`, `DEC-RESULT-IDENTITY`). One hash replaces the fingerprint, calculation, upstream, shape and other identity hashes. Marks the requirements it replaces | `CLIENT-DISCOVERY-SCOPE` | Built. The compiler computes the fingerprint; a result carries four tags and is found by its table name in the destination and each result scope. `population` and the publisher and hash search filters are removed from the API. Every stored result recomputes once |
| `CLIENT-INCLUDED-REUSE` | A define compiles to the same SQL, recipe name and cut point whichever library includes it, so the including library reuses the included library's stored results (`EMIT-ROOT-INDEPENDENT`, `DEC-LIBRARY-OWNS-RESULTS`) | `RUN-CLIENT-RESULT-IDENTITY` | Built. Recipes are named and attributed by their owning library, and no retrieve argument names a library. A measure alone and inside a library that includes it give the same recipes, with the same names, SQL and fingerprints (`IncludedReuseSpec`). Every stored result recomputes once and retrieve UDTFs drop their `library` parameter |
| `CLIENT-RELEASE-TESTS` | A live Job test from the built wheels: discover by tag, run a measure durably, compare with its answer key. Then include the measure in a new library, add a define, and check the upstream results are reused. Offline tests that discovery scopes confine each kind of lookup | `CLIENT-INCLUDED-REUSE` | Not tested live. The live release test (`examples/tests/test_release_live.py`) was removed 2026-10-21 with the sample patients' expected results: the sample set is now the synthetic population small, which ships no answer key (`DATA-SYNTH-SMALL`). Nothing runs a measure live from the built wheels, checks reuse on a second run, or checks a `%%cql` library that includes a measure. Offline confinement tests cover every kind |
| `SVC-NOTEBOOK-SESSION-SCOPE` | `%%cql` cells and `cql("...")` strings with no `library` header join one library per Spark session. Later fragments see earlier defines by name, and redefining a name replaces it. The compiler merges fragments (`merge-cql`) | — | Built 2026-10-21, not deployed. Tested offline and against the local compile host. Next: redeploy the compiler App and the UC compiler function, which serve `merge-cql`, then run notebooks 01 and 02 live. Until then, header-less CQL fails on a deployed workspace |

### After v0.3

| Step | What | After | Status |
|---|---|---|---|
| `DATA-SYNTH-MARKETPLACE` | Publish a pre-generated synthetic population as a free Databricks Marketplace listing, and install and tag it on deploy, so a fresh deployment has a large demo source beside the sample patients (`DATA-SYNTH-MARKETPLACE`, `DATA-SYNTH-LARGE`) | v0.3 | Open |
| `APP-NARRATIVE-OVERLAY` | Generate a narration for any library on request, check it against `specification.xsd` and the run-time reference set before showing it, and cache it per user in a Delta table (`APP-AI-CACHE`, `SPEC-EXPLAIN-INPUTS`, `SPEC-EXPLAIN-VOICE`, `SPEC-EXPLAIN-VIEW`, `SPEC-EXPLAIN-AGAIN`). Accepting a narration back into CQL tags is not part of it | v0.3 | Built 2026-10-22, not deployed. "Generate narration" in the Explain menu writes one library's narration, checks it, retries once, and caches it per user; included libraries are narrated from their own page. Tested offline with a fake model. Not yet run against a real endpoint: no model has been asked to pass the checks, so how often a real reply passes them is unknown. Next: deploy (new `designer.narrations` table, `xmlschema` runtime dependency) and generate for the four sample measures |
| `AGENT-CARE-GAPS` | MCP tools that compile CQL probes against a measure and compute them in bulk through the client, as the caller. Skills for the care-gap workflow. A recorded session on synthetic data showing Genie drive it (`APP-CARE-GAPS`, `APP-PROBE`, `SVC-AGENTS`, `SVC-MCP-SKILLS`) | v0.3 | Open. Starts by amending `SVC-MCP-TOOLS`, which is read-only today |

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

Found 2026-10-21 while writing the QI-Core sample measures (`CONTENT-SAMPLE-STANDARDS`), except "One context per
library" and "String functions", found building the session library (`SVC-NOTEBOOK-SESSION-SCOPE`). The Java CQF reference handles
each one. The sample measures avoid all of them; the last column says how. Silent gaps come first: they give a
wrong answer with no error.

| Gap | What happens | Requirement | How the samples avoid it |
|---|---|---|---|
| One context per library | **Silent.** The compiler lowers every define in the library's first declared context (`CompileService.resolveContext`), ignoring each define's own ELM context. A define written before `context Patient` is `Unfiltered` by the spec, but runs per patient and reads `retrieve_Patient`. The session library makes this easy to hit: a context-free cell followed by a `context Patient` cell | `LANG-CONTEXT` | Every sample define follows `context Patient` |
| Choice value as a function argument | **Silent.** A FHIR choice element (`Observation.effective`) passed to a function whose parameter is a choice (`Choice<DateTime, Interval<DateTime>>`) lowers to a constant null. Every test on it is null or false. Breaks `CORE-NO-SILENT-MISLOWER` | `LANG-CHOICE` | The function takes the resource instead (`latestResult(LaboratoryResultObservation)`) |
| List aggregate functions | **Built 2026-10-21** (all 15, gated by `AggregateFunctions`, `FhirAggregateMostRecent` and the vendored `CqlAggregateFunctionsTest`). Was: they failed at Normalize, only `Count` was built. The samples still use the workaround; switching them to `Max` is a separate change | `LANG-AGGREGATE-FUNCTIONS` | "Most recent" keeps the item with no later item in the list, then takes `First(... sort by id)` |
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
