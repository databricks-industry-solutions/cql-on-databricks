# Repository structure

The repository is split into **features**. A feature has a name you can say to a user ("use the
compiler", "open the Designer"), its own `README.md` and its own requirement family (`BUILD-TAXONOMY`).
What each feature owns is in [`systems.md`](systems.md).

## Levels

| Level | What it is | Directory |
|---|---|---|
| Deployable group | What ships as one unit at one version | none |
| Feature | A unit with its own scope, interface, docs and requirements | top level |
| Component | A build unit inside a feature, named by its role | one level down, only when a feature has more than one |
| Module | A file or package | as the language dictates |

**Foundation** is the deployable group the apps are built on: the compiler and its client, which holds Data
Bindings and the results. It ships at one version.

## Rules

1. A feature has a name you can say to a user. `service/` or `server/` at the top level fails this test.
2. A feature ships as whatever fits it: an App, a Job or a library.
3. A component gets a directory only when its feature has more than one.
4. A component is named by its role, after its feature: `compiler/service`, never `service/compiler`.
5. Dependencies between features go one way, down the table below. HTTP counts as a dependency.
6. The build enforces layer rules, not directories. `./gradlew checkNeutrality` keeps Databricks out of
   `compiler/core` and `compiler/runtime/udf`.
7. "Module" means a file, as in Python and TypeScript.

Shared roles mean the same thing in every feature:

| Role | Means |
|---|---|
| `core` | A logic library with no Databricks dependency |
| `service` | Logic that's host-agnostic |
| `server` | The Databricks App that hosts the service |
| `client` | The Python package callers import |

## Features

Lowest first. A feature may depend only on features above it.

| Feature | Group | Components | Ships as |
|---|---|---|---|
| loader | tools | `synthesizer`, `scripts` | Jobs run by a user |
| compiler | Foundation | `core`, `service`, `server`, `runtime/{udf,uc}`, `client`, `conformance`, `cqf` | App, library |
| agent | app | `service`, `server`, `skills` | App (MCP) |
| designer | app | `web`, `server`, `assistant` | App |

The apps use Foundation only through its public interface: its clients and HTTP API.

## Compiler and client

The compiler turns CQL into SQL. It also ships what that SQL needs (`runtime`, the `cql_*` functions) and
what proves it right (`conformance`, and `cqf`, the reference engine). The runtime is part of the
compiler because the functions and the SQL that calls them are one versioned contract.

The client (`compiler/client`, the `cql-databricks` wheel) runs that SQL as the caller and keeps the results
(`RESULT-INTO-CLIENT`):

- `cql_databricks.compiler`: `cql()`, compiling through the stateless compiler, and binding a run.
- `cql_databricks.resolvers`: everything a run reads and writes through: the resolvers, the environments, and the
  value-set digest (`CLIENT-PACKAGE-SPLIT`).
- `cql_databricks.data_bindings`: finding knowledge, terminology and data sources by UC tag, and the knowledge
  views.
- `cql_databricks.result`: durable bulk calculations on a SQL warehouse, reading results, and the result views.

## Agent

The agent mirrors the compiler's split. `agent/service` holds the tools as plain functions over the client. It
imports nothing that hosts it, and a test enforces that. `agent/server` publishes them over MCP and is the App
that serves them. `agent/skills` holds one folder per skill, each a `SKILL.md` that the server serves and the
deploy publishes to Genie One and Genie Code.

## `scripts/`

`scripts/` holds dev tooling (`setup.sh`, `test.sh`, `run-app.sh`, `with-local-env.sh`, `lib/`), the deploy
(`scripts/deploy/`) and Job entry points the bundle deploys (`scripts/jobs/`). Other scripts live with their
feature: the data loaders in `loader/scripts/` (`SCRIPTS-BY-OWNER`).

## `common/`

`common/` (`cql_databricks_common`) is not a feature. It holds server code that two or more apps share:
config, workspace clients, the compiler App's HTTP client, App auth, profile lookup and local environment
loading. The client does not depend on it.

## Docs

- Each feature folder has a `README.md`: what it does, its components, how to build and test it, and
  links to its other docs. Longer feature docs sit beside it in `<feature>/docs/`.
- `docs/` holds only what covers the whole repository: the map, this file, `systems.md`, coding
  standards, the deploy guide, the cross-cutting requirements and `decisions/`.
- A feature's requirements are in `<feature>/docs/requirements.md`. A requirement two or more features
  implement, or that no feature owns, is in `docs/requirements.md`.

## Names

- Packages live under the `cql_databricks` namespace (`cql_databricks.compiler`,
  `cql_databricks.result`, `cql_databricks.agent.server`).
- Distributions are named `cql-databricks-<name>`. The client is `cql-databricks`.
- The JVM namespace is `com.databricks.cql`. Spark configuration keys are `spark.cqldatabricks.*`.
- Runtime functions keep their `cql_*` names and live in a `cql` schema.
