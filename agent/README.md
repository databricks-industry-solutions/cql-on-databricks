# Agent

The agent is an MCP server over the definitions, results and data this product works with. It reads saved
results and runs CQL inline, and it saves nothing (`SVC-MCP-TOOLS`). It runs as its own App,
`mcp-cql-databricks`, at `/mcp`. Any MCP client can use it, including Genie One. The server also holds the
tool loop behind the Designer's assistant drawer.

## Components

Three components, like the compiler's `service` and `server` (`docs/structure.md`):

| Component | Package | Distribution | What it is |
|---|---|---|---|
| `service/` | `cql_databricks.agent.service` | `cql-databricks-agent-service` | The tools, as plain functions over the client. Host-agnostic: it imports nothing that hosts it, and `tests/test_host_agnostic.py` fails if it does |
| `server/` | `cql_databricks.agent.server` | `cql-databricks-agent-server` | The MCP surface over the service, and the App that serves it |
| `skills/` | `cql_databricks.agent.skills` | `cql-databricks-agent-skills` | One folder per skill, and the code that reads them |

| Path under `agent/` | What it does |
|---|---|
| `service/src/cql_databricks/agent/service/data.py` | The data tools |
| `service/src/cql_databricks/agent/service/definitions.py` | Definition and value-set lookups |
| `service/src/cql_databricks/agent/service/saved.py` | Finding and summarizing saved results |
| `service/src/cql_databricks/agent/service/experiments.py` | Inline experiments: check, run and compare CQL |
| `service/src/cql_databricks/agent/service/model.py` | Result-set types |
| `server/src/cql_databricks/agent/server/mcp.py` | Every MCP tool: publishes the service's tools and the skill pair on a FastMCP server |
| `server/src/cql_databricks/agent/server/instructions.py` | The standing instructions every client gets at `initialize` |
| `server/src/cql_databricks/agent/server/app.py` | The App: serves only `/mcp` |
| `server/src/cql_databricks/agent/server/wiring.py` | Binds the tools to the signed-in user's session and the client (`cql_databricks.result`) |
| `server/src/cql_databricks/agent/server/registry.py` | Tools another feature contributes, such as the Designer's |
| `server/src/cql_databricks/agent/server/ask.py` | The assistant drawer's tool loop |
| `server/deploy/` | The App's `app.yaml` and `requirements.txt` |
| `skills/<name>/SKILL.md` | One skill |
| `skills/__init__.py` | Reads the skills: `list_skills` and `get_skill` |

## Tools

| Tool | What it answers |
|---|---|
| `list_data_sources` | The data sources you can run against |
| `list_tables` | The output names in one complete result set |
| `get_output` | One define's output: a bounded preview, its row count and its address |
| `query_data` | One read-only `SELECT` or `WITH` statement over those outputs and the source tables |
| `get_freshness` | Whether a result exists and when it was computed. It doesn't track source data |

The research tools find a definition, read what was saved, and run experiments that save nothing:

| Tool | What it answers |
|---|---|
| `list_definitions` | Every library in scope, with its title and description. The model matches the user's words and asks them to pick (`AGENT-DEFINITION-DISCOVERY`) |
| `get_definition` | One library's CQL as written, and its declared scoring |
| `list_value_sets` | Every value set the libraries declare, the names it is declared under, and who uses it (`AGENT-VALUE-SET-LOOKUP`) |
| `value_sets_with_code` | The value sets in the terminology that contain a code |
| `find_results` | Whether a calculation of exactly this definition, data source and period is saved, and when. Never computes (`AGENT-SAVED-RESULTS`) |
| `get_population_summary` | A saved calculation's populations, counted as written, and its score (`RUN-CLIENT-MEASURE-SCORE`) |
| `check_cql` | Translates a library and reports diagnostics, with a diff against a baseline |
| `run_cql` | Runs a library inline and returns its counts, score and the CQL that ran (`AGENT-INLINE-EXPERIMENTS`) |
| `compare_cql` | Runs a baseline and an edit inline, and reports what changed and who moved |

`run_cql` and `compare_cql` run on the caller's SQL warehouse session through the client's inline path
(`CLIENT-INLINE-WAREHOUSE`). They create temporary views only, and drop them before returning. Their figures
are labelled `experiment`, never the measure's result. Any CQL the agent writes is shown to the user, or
offered, before it runs (`AGENT-CQL-REVIEW`).

Result tools take an explicit result-set locator from the Designer or a publishing Job. No tool reads a
selection or conversation state, so an external client gets the same answers as the Designer. Inside the
Designer, the Designer also registers its own tools, such as `get_specification`
(`AGENT-TOOL-REGISTRY`). The agent never imports the Designer.

No tool saves anything: no table, tag, schedule, binding or measure change. Tools for care-gap probes are
planned (`AGENT-CARE-GAPS`).

## Skills

`list_skills` and `get_skill(name)` serve longer guidance on demand. Each skill is a folder under `skills/`
holding one `SKILL.md`: `name` and `description` frontmatter over the body. The folder name is the skill's name.
`get_skill` serves the file as it is, and the deploy publishes the same file, so adding a skill is adding a
folder:

| Skill | For |
|---|---|
| `clinical-explain` | The contract for the Designer's narration of a measure. The narration generator loads the same file |
| `population-question` | A population question in the user's words: choose a definition, find and reuse a saved result, report it |
| `experiment` | Changing a definition, reviewing the CQL with the user, and comparing it with the original |
| `explain-cql` | Explaining a define to a clinician from its CQL |
| `cql-authoring` | Writing and editing CQL a measure author would accept (`APP-AUTHORING-SKILL`) |
| `import-quality-measures` | Importing a user's measures and value-set codes into Unity Catalog with `import_measures` (`CONTENT-IMPORT-SKILL`) |

## Identity

Every tool call runs as the signed-in user, with their token, so Unity Catalog decides what they can read
(`SEC-OBO`). Genie One reaches the App through Unity Gateway: the deploy registers it as an MCP Service with a per-user
connection, and publishes every skill but `clinical-explain` as UC skills and to the workspace skills folder ([deploy guide](../docs/deploy.md#the-agent-in-genie-one),
`DEPLOY-AGENT-REGISTRATION`, `DEPLOY-AGENT-SKILLS`). The App allows any origin for CORS, because MCP clients register it from a browser. The App
uses no cookies or ambient credentials. Every call carries the user's own token, which another page can't
mint.

## Assistant drawer

`ask.py` runs the drawer's tool loop. It generates the tool definitions from the same MCP server and calls
the tools in process. It calls a chat endpoint on Model Serving as the signed-in user. Set the bundle
variable `assistant_endpoint` to choose it; there is no default. Any chat endpoint works.

The drawer saves nothing, like the tools. It overlays the Designer's reading column, so opening it doesn't
resize the document (`APP-STABLE-LAYOUT`).

The agent's requirements are in [docs/requirements.md](docs/requirements.md).

## Build and test

```bash
.venv/bin/python -m pytest -q agent     # the tests of all three components (extra: [test])
scripts/run-app.sh --dev                # runs the MCP server locally on :3002/mcp, with the Designer
```

`scripts/setup.sh` installs the three components editable. `scripts/test.sh` runs the agent tests.
`scripts/deploy/stage-app.sh` installs all three into the App's payload, `agent/server/build/app`, and into the
Designer's, for deploy.
