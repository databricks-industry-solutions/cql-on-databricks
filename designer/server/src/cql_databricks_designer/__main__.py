"""`python -m cql_databricks_designer` — run the Designer's App backend with uvicorn.

Local dev: `PORT=3000 python -m cql_databricks_designer` (the Vite dev server proxies /api here). Requires a
running CQL Compiler Service (CQL_COMPILER_ENDPOINT); `scripts/run-app.sh` starts both.
"""
from __future__ import annotations

from cql_databricks_designer.serve import serve


def main() -> None:
    serve("cql_databricks_designer:create_app", default_port=3000, reload_dir="cql_databricks_designer")


if __name__ == "__main__":
    main()
