# Documentation

Each feature's docs sit next to its code. Start with the feature you need.

| Feature | What it covers |
|---|---|
| [compiler/](../compiler/README.md) | CQL to Spark: semantics, IR, execution, the binding contract, conformance |
| [compiler/client/](../compiler/client/README.md) | Using the `cql-databricks` Python client in notebooks and Jobs |
| [Data Bindings](../compiler/client/src/cql_databricks/data_bindings/README.md) | Finding data sources, value sets and libraries by Unity Catalog tag |
| [Durable calculations](../compiler/client/docs/calculation-architecture.md) | How the client's durable calculations store, tag and reuse results; [data sources](../compiler/client/docs/data-sources.md) |
| [loader/](../loader/README.md) | Importing eCQM content and generating synthetic patients |
| [designer/](../designer/README.md) | The Designer App |
| [agent/](../agent/README.md) | MCP tools and skills |
| [common/](../common/README.md) | The shared server library |
| [examples/](../examples/README.md) | Example notebooks |

## Across features

| File | What it covers |
|---|---|
| [deploy.md](deploy.md) | Deploying with the Asset Bundle, grants, local runs |
| [structure.md](structure.md) | How the repository is organized, and naming |
| [systems.md](systems.md) | What each feature owns and the dependency rules |
| [coding-standards.md](coding-standards.md) | Code conventions. Read before writing code |
| [requirements.md](requirements.md) | Requirements for every feature except the compiler |
| [compiler/requirements.md](compiler/requirements.md) | The compiler's requirements |
| [roadmap.md](roadmap.md) | What's next, open decisions, what's deferred |
| [decisions/](decisions/README.md) | An index of decision IDs cited in code |

## Conventions

- File names are kebab-case. Only `README.md`, `LICENSE`, `NOTICE` and similar are upper case.
- Every ID is a descriptive mnemonic, `<TYPE>-<SLUG>`, never a number (`PROC-DESCRIPTIVE-ID`). Order goes
  in an "after" column, never in the name.
- A requirement that stops being true becomes a one-line "Retired" row. It's never deleted, so old
  citations still resolve.
- `DEC-<SLUG>` is a decision. Open ones are in [roadmap.md](roadmap.md). Settled ones amend a requirement
  and are listed in [decisions/](decisions/README.md).
