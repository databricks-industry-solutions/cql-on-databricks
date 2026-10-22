# Agent requirements

Requirements the agent owns. Requirements that span features, and the rule for where a requirement
lives, are in [`docs/requirements.md`](../../docs/requirements.md). Each ID is stable, unique across
every registry, and cited from code (`PROC-CITE-REQ`). A requirement that stops being true moves to a
Retired section here with a one-line reason, so old citations still resolve.

## MCP server and tools

The agent's MCP App, its tools and its skills.

| ID | Requirement |
|---|---|
| `APP-AUTHORING-SKILL` | A pluggable, attributed MCP skill capturing CQL authoring guidance — when to use fluent functions, reusable defines, temporal operators, value-set vs. direct-code membership, and population-define conventions. |
| `SVC-AGENTS` | Package CQL and measurement capabilities as composable agents for Genie, custom apps, and third-party systems. |
| `SVC-MCP` | Expose CQL and measurement capabilities as MCP tools for consumption by Genie agents and custom apps. |
| `SVC-MCP-CONTRIBUTED-TOOLS` | Contributors add to the standing tool instructions via the tool registry. |
| `SVC-MCP-SKILLS` | Standing guidance shared by all MCP clients, defining tool and agent behavior on the same terms. |
| `SVC-MCP-TOOLS` | Standalone MCP app serves the agent's tools; registration contract allows multiple transports. |
| `SVC-MCP-TRANSPORT` | Transport is a registration contract, not a deployment detail; three properties determine client transport. |
