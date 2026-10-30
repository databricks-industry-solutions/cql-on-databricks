# Designer

The Designer is an App for reading and running a measure. You open a measure and read it as a
Specification document built from its ELM. You pick a data source and a SQL warehouse, then run it. The
figures in the document come from the results.

## What it does

- Specification. Each define renders once, with its prose, its CQL and its dependencies, with dependency
  counts in both directions (`APP-DEP-COUNTS`). Prose comes from the CQL's narrative tags and the measure's
  FHIR `Measure` resource: only the author's words ([narrative-tags.md](docs/narrative-tags.md)).
- Run. The execution menu at the top holds the data source, the warehouse and the run status. Refresh
  runs the measure through the Python client's durable calculations, as you, on the warehouse you picked.
  Results go to your private schema. Opening a measure reopens its last matching results when they exist
  (`RUN-CLIENT-REQUEST-REOPEN`).
- Assistant. A drawer that answers questions about the measure and its results with the agent's tools
  ([agent](../agent/README.md)).
- Run log. Every run is written in the background to Delta tables for operators
  ([run-log.md](docs/run-log.md)).
- CQL highlighting in the browser, from a Monarch grammar (`APP-GRAMMAR`).

The Designer computes nothing itself. It calls the client like any other caller.

## Components

| Directory | What it is |
|---|---|
| `designer/src` | The React SPA (Vite). `measures/` is the workspace and Specification, `authoring/` the CQL helpers, `tour/` the product tour |
| `designer/packages/cql-grammar` | The Monaco Monarch grammar for CQL |
| `designer/server` | The FastAPI backend, `cql_databricks_designer`. It serves the built SPA and `/api` |
| `designer/server/src/cql_databricks_designer/schema` | `specification.xsd`, the Specification document schema, and example instances. Tests validate the instances against it |
| `designer/server/src/cql_databricks_designer/explain` | The reference set: every name, read and value set a library's compile declares (`SPEC-EXPLAIN-INPUTS`) |
| `designer/server/deploy` | The App's `app.yaml` and `requirements.txt` |
| `designer/scripts` | `build-design-doc.mjs`, which builds the design-system HTML page |

## Run it locally

```bash
scripts/setup.sh          # once: the Python venv and npm packages
scripts/run-app.sh --dev
```

This starts the compiler (`:8080`), the agent's MCP server (`:3002`), the Designer backend (`:3000`) and the
Vite dev server (`:5173`). It reads `databricks.local.yml` for workspace settings, the optional `.env` for
local settings, and `~/.databrickscfg` for auth. Without `--dev` it serves the built SPA on `:3000`.

## Build and test

In `designer/`:

```bash
npm run ui:build      # build the SPA into designer/dist
npm run typecheck
npm test              # vitest
```

The backend tests run with pytest from `designer/server` in the root venv. `scripts/test.sh` runs both.

`npm run doc:design` builds `designer/docs/design-system.html` from
[design-system.md](docs/design-system.md) and `docs/design-system.tmpl.html`. The output is not
gitignored.

## Docs

| File | What it covers |
|---|---|
| [docs/design-system.md](docs/design-system.md) | Tokens, type scale and component rules. Read before changing the UI |
| [docs/narrative-tags.md](docs/narrative-tags.md) | The CQL annotation tags behind the Specification's prose, and their precedence |
| [docs/run-log.md](docs/run-log.md) | The run log tables and their grants |

The Designer's requirements are in [docs/requirements.md](docs/requirements.md).
