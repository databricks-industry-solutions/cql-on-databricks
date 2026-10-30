# Common

`cql-databricks-common` (`cql_databricks_common`) is the shared server library (`COMMON-LIBRARY`). It
holds what more than one feature's server or local tool needs and no single feature owns. The Designer
server, the agent, the loader and the scripts in `scripts/` use it. The client (`cql_databricks.*`) does
not depend on it.

A module belongs here only when two or more features import it.

## Modules

| Module | What it does | Used by |
|---|---|---|
| `config.py` | Reads settings from the environment: the compiler endpoint and token, catalogs, the warehouse, the local Spark remote | Designer, agent, scripts |
| `workspace.py` | One process-wide Databricks SDK client, and the forwarded user identity (`SEC-WS-CLIENT-SHARED`, `SEC-OBO`) | Designer, agent, scripts |
| `compiler.py` | HTTP client for the Compiler Service App (`/translate`, `/capabilities`, `/health`) | Designer, agent, scripts |
| `app_auth.py` | Chooses the credential for a compile endpoint: the process identity for a deployed App, a static token for a local host (`SEC-APP-TO-APP`) | `compiler.py` |
| `databricks_profile.py` | Finds the CLI profile from `--profile`, then `databricks.local.yml`, then `DATABRICKS_CONFIG_PROFILE`. Standard library only | Loader, scripts, `scripts/lib/databricks-profile.sh` |
| `local_env.py` | Builds the environment for a local run from `databricks.local.yml` and `.env` (`SVC-LOCAL-CONFIG-BUNDLE`) | Designer, `scripts/lib/local-env.sh` |

## Build and test

`scripts/setup.sh` installs it into the root venv with its `test` extra. Then:

```bash
scripts/test.sh                       # includes the common tests
.venv/bin/python -m pytest -q common  # these tests only
```
