"""Shared server infrastructure: what more than one feature's server or tool needs and none of them owns.

`COMMON-LIBRARY`, `DEC-COMMON-HOME`. Not part of Foundation's public interface, and the
client (`cql_databricks.*`) does not depend on it. A module belongs here only when two or more features
import it; a module one feature uses lives in that feature (the Designer's `serve`, `cache_stamp`,
`data_bindings`, `dto` and `specification.model` live in
`cql_databricks_designer`).

| module | who uses it |
|---|---|
| `config.py` | the Designer and the agent: catalogs, endpoints, the warehouse |
| `workspace.py` | the Designer, the agent and the loader scripts: one process-wide Databricks client and the forwarded-identity context (`SEC-OBO`) |
| `compiler.py` | the Designer and the agent: the compiler App's HTTP client |
| `app_auth.py` | `compiler.py`: which credential a compile endpoint takes (`SEC-APP-TO-APP`) |
| `databricks_profile.py` | every local tool: the profile from `databricks.local.yml` (stdlib only, run as a script) |
| `local_env.py` | every local command, through `scripts/lib/local-env.sh` (`SVC-LOCAL-CONFIG-BUNDLE`) |
"""
