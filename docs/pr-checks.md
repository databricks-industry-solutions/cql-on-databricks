# Pull request checks

The checks every pull request must pass, for a human or an automated reviewer. Each one names a rule from
[`AGENTS.md`](../AGENTS.md), [coding-standards.md](coding-standards.md) or a requirement registry, so a review
can cite it by ID.

## How to report

Report every check as `PASS`, `FAIL` or `N/A`. A `FAIL` names the file and line and says in one sentence what
is wrong. An `N/A` says why the check doesn't apply.

Only a clear violation of a check is a `FAIL`. A subtle difference in wording, style or approach is not; raise
it as a comment if it matters.

| Outcome on `FAIL` | Means |
|---|---|
| `BLOCK` | Don't merge until it's fixed |
| `APPROVAL` | Merge only with a maintainer's explicit approval, recorded in the PR |
| `COMMENT` | Raise it. The author decides |

An automated reviewer reports. It never gives an `APPROVAL` itself.

## Scope and description

| ID | Check | On fail |
|---|---|---|
| `CHECK-PR-SCOPE` | One concern per PR. No unrelated edits, reformatting or drive-by fixes. Other problems are mentioned, not fixed | `COMMENT` |
| `CHECK-PR-DESCRIPTION` | The description says what changed, which tests ran, and what was not checked | `BLOCK` |
| `CHECK-PR-CITES` | The title, commits and description cite the requirement IDs the change realizes (`PROC-CITE-REQ`) | `BLOCK` |
| `CHECK-NO-LOCAL-FILES` | No generated or local files: `build/`, `dist/`, `*.egg-info`, `node_modules/`, `.env`, `databricks.local.yml` | `BLOCK` |

## Build and tests

| ID | Check | On fail |
|---|---|---|
| `CHECK-CI` | CI is green (`.github/workflows/ci.yml`): the neutrality wall, the conformance gate, the JVM, client, Python and frontend tests | `BLOCK` |
| `CHECK-TESTS-COVER` | Changed behaviour has a test that fails without the change and cites the requirement ID. Real objects over mocks | `BLOCK` |
| `CHECK-TESTS-NEW` | New functionality ships with its tests in the same PR: every new function, method, route, MCP tool, resolver, option, deploy step or script is exercised by a test that cites its requirement ID. Docs-only and pure-rename changes are `N/A` | `BLOCK` |
| `CHECK-CONFORMANCE` | A change to CQL semantics or emitted SQL has a conformance test. A compiler gap's fix ships the smallest CQL that shows it, under `compiler/conformance/src/test/resources/cql/operators/` or `fhir/`, never a measure | `BLOCK` |
| `CHECK-NO-SKIPS` | No test is skipped, disabled or loosened to make it pass | `BLOCK` |
| `CHECK-LIVE-TESTS` | A change to the deploy, grants, Apps or warehouse paths says whether `scripts/test.sh integration` ran, and its result. CI runs no live tests | `APPROVAL` |

## Requirements and docs

| ID | Check | On fail |
|---|---|---|
| `CHECK-REQ-EXISTS` | New behaviour has a requirement, added in this PR to its topic doc and its feature's registry. A new requirement was agreed before the PR | `APPROVAL` |
| `CHECK-REQ-CURRENT` | A changed requirement is rewritten as its whole current definition. No "Added", "Amended" or "Built" notes, no dates, no history (`PROC-CURRENT-DEFINITION`) | `BLOCK` |
| `CHECK-REQ-RETIRED` | A requirement that stops being true is deleted from its registry, and nothing still cites it: a search of the whole repository for the ID finds no code, test or doc. Every cited ID resolves to exactly one registry | `BLOCK` |
| `CHECK-IDS` | New IDs are `<TYPE>-<SLUG>` mnemonics, with an existing prefix where one fits. No numbers. Order goes in an "after" column (`PROC-DESCRIPTIVE-ID`) | `BLOCK` |
| `CHECK-DOCS-TRUE` | Docs the change affects say what is now true. Stale text is removed and links resolve. A durable rationale goes in `docs/decisions/` | `BLOCK` |
| `CHECK-DECISIONS` | An open `DEC-*` is in `docs/roadmap.md`. A settled one amends a requirement and is listed in `docs/decisions/README.md` | `COMMENT` |
| `CHECK-NO-HISTORY` | Docs, comments and decision records say what is true now, and why in a sentence or two: the constraint or guardrail and what it protects. No "previously", "we tried", "replaced by" or dated notes, and no retelling of the discussion behind a decision. That history is in git | `BLOCK` |

## Customer compatibility: `CHECK-COMPAT`

Customers build on the contracts below. A PR that changes one states its level in the description, using the
note at the end of this section. The level decides the outcome.

| Level | What breaking it costs the customer | On fail |
|---|---|---|
| `COMPAT-SILENT` | Results change with no error. Wrong numbers can be reported | `APPROVAL`, and a release note |
| `COMPAT-DATA` | They redeploy schemas, migrate tables or recompute results | `APPROVAL`, a migration or recompute path, and a release note |
| `COMPAT-CODE` | Their notebooks, Jobs or resolver plugins fail until they edit code | `APPROVAL`, and a release note |
| `COMPAT-CONFIG` | They edit config, re-register, or upgrade the wheel and the Apps together | `APPROVAL` |
| `COMPAT-ADDITIVE` | Nothing breaks | None |

Additive and free: a new resolver class, a new optional keyword or field with a default, a new MCP tool, a new
endpoint, a new `cql_*` function name, a new bundle variable with a default.

| Contract | Where | Level if broken | How to change it compatibly |
|---|---|---|---|
| `cql_*` runtime functions: names, signatures, results | `UcFunctionManifest.scala` | `COMPAT-SILENT` | New behaviour gets a new name. A fingerprint doesn't cover the functions, so a kept result is never recomputed |
| How a retrieve, value-set membership or `__subject_id` resolves | `SqlEvalEnv.scala`, `reshape.py` | `COMPAT-SILENT` | Never change it in place. Membership keys on `valueset_id` alone |
| `RetrieveRequest` filters are advisory; the plan re-applies them (`BIND-CAPABILITY`) | `resolvers/interfaces.py` | `COMPAT-SILENT` | Never let the compiler trust a resolver's filtering |
| Translator, FHIR and FHIRHelpers versions | `gradle.properties`, `data-model.md` | `COMPAT-SILENT` | Upgrade with a conformance run and a release note |
| Environment layout: catalog tags, schema names, one view per type | `resolvers/environment.py` (`LAYOUT_VERSION`) | `COMPAT-DATA` | Bump the layout version, keep reading the old one in `SUPPORTED_LAYOUTS`, add the value to the tag policy |
| Data contract `(__subject_id, __resource)` (`DATA-CONTRACT`, `BIND-CONTRACT`) | `data-binding.md` | `COMPAT-DATA` | Never rename. Customer views are written against it |
| FHIR resource structs | `resource-schemas.json`, `/resource-schema` | `COMPAT-DATA` | Add fields and safe widenings only. Customers declare `RETURNS TABLE` against them |
| Knowledge and terminology store columns, and their published views | `measure_import/load.py` | `COMPAT-DATA` | Add columns only |
| Result tables: name pattern, format and tags | `result/_catalog_completion.py` | `COMPAT-DATA` | Bump the format. Old tables become misses and are orphaned; say how they are cleaned up |
| Fingerprint inputs: identity format, resolver identity strings, value-set digest, key order | `DurablePlan.scala`, `value_set_digest.py`, `resolvers/` | `COMPAT-DATA` | Any change makes every kept result recompute. Plan it and announce it |
| Run log `designer.runs` and `designer.run_steps` | `run_log.py`, `provision-run-log.py` | `COMPAT-DATA` | Add columns only. A rename needs Delta column mapping, which older readers can't read |
| Folder package layout | `compiler/folder_content.py` | `COMPAT-DATA` | Keep reading every older layout |
| Resolver, provider and plugin interfaces: abstract methods and signatures, the `cql_databricks.libraries` and `cql_databricks.engines` entry-point groups | `resolvers/interfaces.py`, `providers.py`, `libraries.py`, `engines.py`, `runner.py` | `COMPAT-CODE` | A new method gets a concrete default. A new parameter is a keyword with a default. A new abstract method breaks every customer subclass |
| Exported names (each package's `__all__`) and exception classes with their bases | each package's `__init__.py` | `COMPAT-CODE` | Never remove. Re-export when moving |
| Public dataclasses: fields, and field order where positional | `providers.py`, `libraries.py`, `resolvers/interfaces.py` | `COMPAT-CODE` | Add new fields last, with a default |
| Return shapes: `exec()` columns and its single-or-dict return, `LibraryGraph`, insight `to_dict()` keys | `providers.py`, `result/insights/` | `COMPAT-CODE` | Add only |
| Source-string forms, conf keys `spark.cqldatabricks.*`, env vars `CQL_DATABRICKS_*`, `%%cql` flags | `client.py`, `config.py`, `magic.py` | `COMPAT-CODE` | Never rename. Add only |
| Compiler HTTP API: paths, request and response keys, status codes (`SVC-PROTOCOL`) | `CompilerServiceApp.scala`, `compiler-service.md` | `COMPAT-CONFIG` | A customer's pinned wheel meets a newer App. Add only. Unknown keys are ignored and retired ones are refused |
| MCP tools: names, parameters, return keys, the `resultSet` locator | `agent/server/.../mcp.py`, `result/selection_reading.py` | `COMPAT-CONFIG` | Skills and Genie call them by name. Add tools, never rename one |
| Skill names, UC skill IDs, the MCP connection and service names | `publish_skills.py`, `register_agent_mcp.py` | `COMPAT-CONFIG` | A rename leaves stale objects in UC and breaks Genie |
| App names, Job name, maintenance principal name | `databricks.yml`, `provider.py` | `COMPAT-CONFIG` | Never rename. The client finds the compiler App by name |
| Bundle variables, `databricks.local.yml` keys, script and `cql-synth` flags, `<Measure>.synth.json` and recipe formats | `databricks.yml`, `scripts/`, `loader/synthesizer/` | `COMPAT-CONFIG` | Keep an old name as an alias. Add a format version before the first break |

Not contracts: the Designer's `/api/*` routes (only its own web UI calls them), the emitted SQL text, the
Specification XSD (rebuilt on every read), and imports between this repository's features, which deploy together.

The note, in the PR description, one per contract changed:

```
Compatibility: COMPAT-CODE
Contract: DataResolver (compiler/client/src/cql_databricks/resolvers/interfaces.py)
Who breaks: every custom DataResolver subclass
What they must do: implement scope(); no data change
Release note: <the text customers will read>
```

## Security and data

| ID | Check | On fail |
|---|---|---|
| `CHECK-NO-SECRETS` | No hostnames, tokens, credentials, workspace IDs or profile names in tracked files. A new setting goes in the `*.example` templates | `BLOCK` |
| `CHECK-NO-PATIENT-DATA` | Every fixture is synthetic | `BLOCK` |
| `CHECK-AS-USER` | App code that reads or writes data runs as the signed-in user (`SEC-OBO`). No App principal gets a catalog-wide data privilege (`DEPLOY-APP-GRANTS`) | `BLOCK` |
| `CHECK-GRANTS` | A change to a grant, group, scope or principal is listed in the description, saying who gains or loses what | `APPROVAL` |
| `CHECK-SQL-SAFE` | SQL built from input quotes identifiers and passes values as parameters or quoted literals. Scala SQL text goes through `SqlRender` | `BLOCK` |
| `CHECK-LOGS-CLEAN` | Logs and errors carry no token, credential or patient row | `BLOCK` |
| `CHECK-NO-INTERNAL` | Nothing exposes Databricks internals: no internal ticket or incident IDs, no links to Slack, Jira, Confluence or `go/`, no unreleased features or limits, no internal workarounds, no people's names. State the constraint itself, not where it came from | `BLOCK` |
| `CHECK-NO-SOURCE-NAMES` | Nothing names or alludes to a specific measure steward, publisher, program, vendor or content source: not in code, docs, comments, tests, fixtures, object names or IDs, and not as a measure's catalogue number. Say what the thing is ("a measure package", "a value-set service"). Not sources: the standards and their bodies (HL7, FHIR, CQL, QI-Core), the generic terms eCQM and dQM, the canonical URIs of standard code systems, dependency coordinates, and license attributions. The root `README.md` may name the published measures we computed and the performance we measured on them | `BLOCK` |

## Dependencies

| ID | Check | On fail |
|---|---|---|
| `CHECK-DEPS-AGREED` | A new dependency (library, action or service) was agreed before the PR | `APPROVAL` |
| `CHECK-DEPS-PINNED` | GitHub Actions are pinned to a commit SHA. JVM artifacts are in `gradle/verification-metadata.xml`. Python dependencies have a lower bound | `BLOCK` |
| `CHECK-DEPS-LICENSE` | A new dependency's license allows redistribution. A change to `NOTICE.md` is approved by a maintainer | `APPROVAL` |

## Architecture

| ID | Check | On fail |
|---|---|---|
| `CHECK-DESIGN-AGREED` | An architecture, data-model or new derived-data change was proposed and agreed before the PR | `APPROVAL` |
| `CHECK-FEATURE-DEPS` | Feature dependencies go one way, HTTP included, and the Apps use Foundation only through its clients and HTTP API ([structure.md](structure.md)). Not automated yet | `BLOCK` |
| `CHECK-NEUTRALITY` | `compiler/core` and `compiler/runtime/udf` take no Databricks dependency (`./gradlew checkNeutrality`) | `BLOCK` |
| `CHECK-ONE-PATH` | One implementation path. No silent fallback, no "try X, then Y", no swallowed error. A failure raises a typed error with useful text | `BLOCK` |
| `CHECK-NO-UPSTREAM-PATCH` | No regex or string patching of a third-party dependency's output. Our own compiler's output is fixed in the compiler, or adapted under a requirement that says why | `BLOCK` |
| `CHECK-DERIVED-DATA` | The frontend doesn't compute from regex or source scans what the compiler provides. Regex that edits source text, or derives what the compiler doesn't provide, is fine | `BLOCK` |
| `CHECK-CONFIG` | Settings resolve by `BUILD-RESOLVE`. No new `spark.cqldatabricks.*` key. Workspace settings only in `databricks.local.yml`, local ones only in `.env` | `BLOCK` |

## Code quality

| ID | Check | On fail |
|---|---|---|
| `CHECK-STANDARDS` | The code follows [coding-standards.md](coding-standards.md) for its language | `COMMENT` |
| `CHECK-NO-DEAD-CODE` | No commented-out code, unused code or speculative abstraction. Code the change replaces is removed | `COMMENT` |
| `CHECK-NO-TODO` | No new `TODO` or `HACK` without a roadmap item. Nothing new builds on one | `COMMENT` |
| `CHECK-READABLE` | Names say what things are. Comments say why, at the surrounding code's density | `COMMENT` |

## Deploy

| ID | Check | On fail |
|---|---|---|
| `CHECK-DEPLOY-IDEMPOTENT` | A deploy step gives the same result when run twice, and fails the deploy on any error | `BLOCK` |
| `CHECK-DEPLOY-DOCS` | A new bundle variable, grant or deploy step is in [deploy.md](deploy.md) and `databricks.local.yml.example` | `BLOCK` |
| `CHECK-MIGRATION` | A change to a persisted table, tag or file layout ships its migration, or says why it needs none (`CHECK-COMPAT`) | `BLOCK` |
