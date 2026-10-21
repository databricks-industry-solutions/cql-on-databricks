# Roadmap

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
| `CLIENT-RELEASE-TESTS` | A live Job test from the built wheels: discover by tag, run a measure durably, compare with its answer key. Then include the measure in a new library, add a define, and check the upstream results are reused. Offline tests that discovery scopes confine each kind of lookup | `CLIENT-INCLUDED-REUSE` | Built on the sample measures. `examples/tests/test_release_live.py` runs each sample measure durably over the sample patients (`DATA-SAMPLE-SOURCE`), matches its expected results for every patient, reuses every define on a second run, reuses them from a result scope into a new destination, and runs a `%%cql` library that includes the measure and adds one define: every measure define is reused from the first run's tables and only the new define is computed. Offline confinement tests cover every kind |

### After v0.3

| Step | What | After | Status |
|---|---|---|---|
| `DATA-SYNTH-MARKETPLACE` | Publish a pre-generated synthetic population as a free Databricks Marketplace listing, and install and tag it on deploy, so a fresh deployment has a large demo source beside the sample patients (`DATA-SYNTH-MARKETPLACE`, `DATA-SYNTH-LARGE`) | v0.3 | Open |
| `APP-NARRATIVE-OVERLAY` | Generate a narration for any library on request, check it against `specification.xsd` and the run-time reference set before showing it, and cache it per user in a Delta table (`APP-AI-CACHE`, `SPEC-EXPLAIN-INPUTS`, `SPEC-EXPLAIN-VOICE`, `SPEC-EXPLAIN-VIEW`, `SPEC-EXPLAIN-AGAIN`). Accepting a narration back into CQL tags is not part of it | v0.3 | Deferred 2026-10-21. The static overlays are removed (`SPEC-STATIC-OVERLAY` SUPERSEDED), so Explain shows authored tags only until this is built |
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
