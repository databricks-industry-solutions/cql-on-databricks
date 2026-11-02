# Agent requirements

Requirements the agent owns. Requirements that span features, and the rule for where a requirement
lives, are in [`docs/requirements.md`](../../docs/requirements.md). Each ID is stable, unique across
every registry, and cited from code (`PROC-CITE-REQ`). A requirement that stops being true is
deleted, with every citation of it (`PROC-CURRENT-DEFINITION`).

## MCP server and tools

The agent's MCP App, its tools and its skills.

| ID | Requirement |
|---|---|
| `AGENT-CQL-REVIEW` | Any CQL the agent writes or edits is shown to the user, or offered, before it runs, and its results say the agent wrote it. The standing instructions and every research skill carry the rule, and `check_cql`, `run_cql` and `compare_cql` return the exact CQL that ran and its diff against the baseline, so the user reviews what executed. Clinical authority stays with the user (`APP-CLINICAL-BOUNDARY`). |
| `AGENT-DEFINITION-DISCOVERY` | `list_definitions` lists every library in the environment's knowledge with its title and description; the model matches the user's words to them by meaning and asks the user to pick one or write a new one, never choosing silently. `get_definition` returns a library's CQL as written, with its declared scoring, so a criterion is explained from the logic that runs. |
| `AGENT-DEFAULT-ENVIRONMENT` | The agent's tools run in the default environment (`CLIENT-ENVIRONMENT-RESOLVER`), or, called from the Designer's assistant drawer, in the environment the Designer passes (`DOC-ENVIRONMENT-PICKER`). No tool takes an environment argument the model fills in, and the agent never asks a user for one. Its runs write and reuse as draft runs (`RUN-CLIENT-DRAFT-RESULTS`). |
| `AGENT-INLINE-EXPERIMENTS` | `check_cql` translates a whole library and reports its diagnostics. `run_cql` runs one inline and `compare_cql` runs a baseline and an edit inline in one call (`CLIENT-INLINE-WAREHOUSE`): a define already computed, the caller's own in `draft_results` or an official one in `results`, is read from its table, and the rest from the environment's data as it is now. They take CQL text, not references, save nothing, count populations as written, score with `RUN-CLIENT-MEASURE-SCORE`, report who entered and left each define as counts only, and label every figure `experiment`, never the measure's official result. |
| `AGENT-SAVED-RESULTS` | `find_results` says whether a calculation of exactly one library, environment and period is saved, the caller's own in the environment's `draft_results`, else an official one in its `results` (`RUN-CLIENT-RESULT-OWNERSHIP`), and when it was computed. It never computes; the model asks the user whether to reuse it. `get_population_summary` reports a saved selection's populations as written and its score (`RUN-CLIENT-MEASURE-SCORE`), reopened through the environment its address names and the schema it was written to (`CLIENT-ENVIRONMENT-RESOLVER`). |
| `AGENT-VALUE-SET-LOOKUP` | `list_value_sets` lists every value set the environment's libraries declare, with the names authors declared it under and the libraries using it; `value_sets_with_code` lists the value sets in the terminology that contain a code, optionally in one code system. A declared name is a value set's only human label. |
| `APP-AUTHORING-SKILL` | A pluggable, attributed MCP skill, `cql-authoring`, capturing CQL authoring guidance — when to use fluent functions, reusable defines, temporal operators, value-set vs. direct-code membership, and population-define conventions. |
| `SVC-AGENTS` | Package CQL and measurement capabilities as composable agents for Genie, custom apps, and third-party systems. |
| `SVC-MCP` | Expose CQL and measurement capabilities as MCP tools for consumption by Genie agents and custom apps. |
| `SVC-MCP-CONTRIBUTED-TOOLS` | Contributors add to the standing tool instructions via the tool registry. |
| `SVC-MCP-SKILLS` | Standing guidance shared by all MCP clients, defining tool and agent behavior on the same terms. The standing instructions are the server's (`agent/server/src/cql_databricks/agent/server/instructions.py`): every client gets them at `initialize`, and they are the drawer's system prompt. A skill is a folder in `agent/skills/` holding one `SKILL.md`, with `name` and `description` frontmatter over its body, and the folder name is the skill's name. Every such folder is served, and `get_skill` returns the file verbatim. |
| `SVC-MCP-TOOLS` | A standalone MCP App serves the agent's tools; a registration contract allows multiple transports. The tools may compute inline as the caller (`AGENT-INLINE-EXPERIMENTS`) but never save anything: no table, tag, schedule, binding or measure change. The surface is three named groups, the data tools, the research tools and the skill pair, and a new question shape goes in none of them. |
| `SVC-MCP-TRANSPORT` | Transport is a registration contract, not a deployment detail; three properties determine client transport. |
