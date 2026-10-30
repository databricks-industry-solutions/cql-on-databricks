"""SVC-MCP-TOOLS, SVC-MCP-SKILLS, BUILD-TAXONOMY — the agent's server: the MCP surface and the App (`agent/README.md`).

``mcp.py`` publishes every tool of the service (``cql_databricks.agent.service``) and the skill pair on one FastMCP
server, with the standing instructions (``instructions.py``). ``app.py`` is the App that serves it at ``/mcp``.
``wiring.py`` binds the service's seams to the signed-in user's session (`SEC-OBO`), ``registry.py`` takes the
tools another feature contributes (`AGENT-TOOL-REGISTRY`), and ``ask.py`` is the Designer drawer's tool loop over
the same server.
"""
