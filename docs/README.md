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
| [pr-checks.md](pr-checks.md) | The checks every pull request must pass, including customer compatibility |
| [requirements.md](requirements.md) | Requirements that span features, and where each feature's registry is |
| [decisions/](decisions/README.md) | An index of decision IDs cited in code |
| [roadmap.md](roadmap.md) | The plan, open decisions and known compiler gaps |

## Conventions

- File names are kebab-case. Only `README.md`, `LICENSE.md`, `NOTICE.md` and similar are upper case.
- Every ID is a descriptive mnemonic, `<TYPE>-<SLUG>`, never a number (`PROC-DESCRIPTIVE-ID`). Order goes
  in an "after" column, never in the name.
- A requirement that stops being true is deleted from its registry, in the same change that removes every
  citation of it (`PROC-CURRENT-DEFINITION`).
- `DEC-<SLUG>` is a decision. Open ones are in the [roadmap](roadmap.md). Settled ones amend a requirement
  and are listed in [decisions/](decisions/README.md).
