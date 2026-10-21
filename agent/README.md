# Agent

The agent is an MCP server with read-only tools over the results this product computes. It runs as its own
App, `mcp-cql-databricks`, at `/mcp`. Any MCP client can use it, including Genie. The same package holds
the tool loop behind the Designer's assistant drawer.

## Components

| Path under `agent/` | What it does |
|---|---|
| `src/cql_databricks/agent/tools.py` | The data tools, as plain functions |
| `src/cql_databricks/agent/server.py` | Publishes the tools and skills on a FastMCP server |
| `src/cql_databricks/agent/mcp_app.py` | The App: serves only `/mcp` |
| `src/cql_databricks/agent/registry.py` | Tools another feature contributes, such as the Designer's |
| `src/cql_databricks/agent/skills.py`, `skills/` | The skills the server serves |
| `src/cql_databricks/agent/ask.py` | The assistant drawer's tool loop |
| `src/cql_databricks/agent/model.py`, `wiring.py` | Result-set types, and reads through the client (`cql_databricks.result`) |
| `deploy/` | The App's `app.yaml` and `requirements.txt` |

## Tools

| Tool | What it answers |
|---|---|
| `list_data_sources` | The data sources you can run against |
| `list_tables` | The output names in one complete result set |
| `get_output` | One define's output: a bounded preview, its row count and its address |
| `query_data` | One read-only `SELECT` or `WITH` statement over those outputs and the source tables |
| `get_freshness` | Whether a result exists and when it was computed. It doesn't track source data |

Result tools take an explicit result-set locator from the Designer or a publishing Job. No tool reads a
selection or conversation state, so an external client gets the same answers as the Designer. Inside the
Designer, the Designer also registers its own tools, such as `get_specification`
(`AGENT-TOOL-REGISTRY`). The agent never imports the Designer.

The tools only read. Tools that write or compute CQL probes are planned (`AGENT-CARE-GAPS` in the
[roadmap](../docs/roadmap.md)).

## Skills

`list_skills` and `get_skill(name)` serve longer guidance on demand. There is one skill,
`clinical-explain`: the contract for explaining a measure in clinical language. The Designer's narration
uses the same file, `src/cql_databricks/agent/skills/clinical-explain/SKILL.md`.

## Identity

Every tool call runs as the signed-in user, with their token, so Unity Catalog decides what they can read
(`SEC-OBO`). The App allows any origin for CORS, because MCP clients register it from a browser. The App
uses no cookies or ambient credentials. Every call carries the user's own token, which another page can't
mint.

## Assistant drawer

`ask.py` runs the drawer's tool loop. It generates the tool definitions from the same MCP server and calls
the tools in process. It calls a chat endpoint on Model Serving as the signed-in user. Set the bundle
variable `assistant_endpoint` to choose it; there is no default. Any chat endpoint works.

The drawer is read-only, like the tools. It overlays the Designer's reading column, so opening it doesn't
resize the document (`APP-STABLE-LAYOUT`).

## Build and test

```bash
.venv/bin/python -m pytest -q agent     # the tests (extra: [test])
scripts/run-app.sh --dev                # runs the MCP server locally on :3002/mcp, with the Designer
```

`scripts/test.sh` runs the agent tests. `scripts/deploy/stage-app.sh` stages the App into `agent/build/app` for
deploy.
