# AGENTS.md

Instructions for any AI coding agent working in this repository.

## How to communicate

- Be concise. Speak plainly and simply. Short sentences, no filler.
- Say what you changed, what you checked, and what you did not check.
- If something failed, show the error. Do not guess or round up.
- If a request is unclear, ask one clear question instead of guessing.

## Git: always ask first

- Never stage (`git add`), commit, create or switch branches, or push without approval in the current request.
- Never create git worktrees or worktree branches (`git worktree add`, or an agent tool's worktree
  isolation mode). Work in the main checkout.
- Approval covers only the action approved. Ask again next time.
- Leave changes in the working tree and list the files you touched.
- Never discard or rewrite work (`reset --hard`, `checkout -- .`, `rebase`, `push --force`, `stash drop`)
  without approval.

## Ask before building

- Propose the approach first for: new requirements, architecture or data-model changes, new
  dependencies, and new derived data. Wait for a yes.
- Stay in scope. Mention other problems you find; do not fix them unasked.

## Hard rules

- No patient data. Every fixture is synthetic.
- No hostnames, tokens, or credentials in tracked files. Workspace settings (host, profile, catalogs,
  warehouse, Lakebase) go only in `databricks.local.yml`; local-run settings and secrets go in `.env`
  (both gitignored; `SVC-LOCAL-CONFIG-BUNDLE`). Update the `*.example` templates instead.
- Never hardcode a Databricks profile name. Read it from `databricks.local.yml`
  (`scripts/lib/databricks-profile.sh`, `cql_databricks_common/databricks_profile.py`).
- `compiler/core` and `compiler/runtime/udf` take no Databricks dependency. `./gradlew checkNeutrality` enforces it.
- Lakebase DDL lives only in the deploy definition (`DEPLOY-LAKEBASE-SCHEMA`). Seed scripts do DML only.
- One implementation path per feature. No silent fallbacks. Fail loud.
- Do not regex-patch or string-patch output from an upstream dependency. Fix it upstream.

## Commands

| What | Command |
|---|---|
| All JVM checks and conformance | `./gradlew checkAll` |
| Platform wall | `./gradlew checkNeutrality` |
| Offline tests | `scripts/test.sh` (same as `scripts/test.sh unit`) |
| Live tests against the deployed workspace | `scripts/test.sh integration` |
| Both | `scripts/test.sh all` |
| Local setup | `scripts/setup.sh` |
| Run the app locally | `scripts/run-app.sh --dev` |

## Tests

- Run the tests that cover your change before saying it works. Say which ones you ran.
- Semantic compiler changes need a conformance test, not only a unit test.
- A compiler gap, when found, gets a conformance test with its fix: the smallest CQL that causes and checks it,
  under `compiler/conformance/src/test/resources/cql/operators/` or `fhir/`. Never a measure.

## Specs and citations

- Requirement IDs (`LANG-3VL`, `PIPE-STATE-LAYER`) are the source of truth. Cite the ID in the
  docstring of code that implements it, and in tests (`PROC-CITE-REQ`).
- Every ID is a meaningful mnemonic, `<TYPE>-<SLUG>`, never a bare number (`PROC-DESCRIPTIVE-ID`).
  This applies to requirements, plan steps, phases, workstreams, decisions, options and tasks.
  - Good: `SPLIT-PACKAGES`, `DEPLOY-LAKEBASE-SCHEMA`. Bad: `A6`, `W3`, `FR-001`, `T004`.
  - The name says what it is, so a reader can follow a sentence without looking it up.
  - If order matters, put it in a separate "after" column. Never encode order in the name, so
    inserting a step renumbers nothing.
  - Reuse an existing type prefix (see the registries below) before inventing a new one.
- New work with no requirement adds one, in the same change, to its topic doc and its system's
  registry.
- `DEC-<SLUG>` is an open decision, never a requirement. Open ones live in `docs/roadmap.md`. Once
  settled, it amends or creates a requirement, and the ID is listed in `docs/decisions/README.md`.
- A requirement that stops being true is marked RESOLVED or SUPERSEDED in place, with the reason.
  Never delete it: old code that cites the ID must still lead somewhere.
- For CQL semantics, cite the HL7 CQL spec clause in a code comment.
- Docs say what is true now. History lives in git. A durable design rationale goes in `docs/decisions/`.

## Where to look

| Topic | File |
|---|---|
| Docs map | `docs/README.md` |
| A feature: what it does, components, build and test | `<feature>/README.md`, then `<feature>/docs/` |
| Repo layout and feature names | `docs/structure.md`, `docs/systems.md` |
| Code conventions (read before writing code) | `docs/coding-standards.md` |
| Compiler requirements | `docs/compiler/requirements.md` |
| All other requirements | `docs/requirements.md` |
| Decision records | `docs/decisions/` |
| Plan, open decisions, known compiler gaps | `docs/roadmap.md` |
| Deploying | `docs/deploy.md` |
