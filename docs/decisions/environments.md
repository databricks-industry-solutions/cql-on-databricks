# Environments (proposed)

Status: agreed in review. It overlays
[providers.md](providers.md): what that record says about role interfaces, relations, the compile request,
identity and overlay stands, except where this record replaces it. The requirements it settles are written
in their registries; which steps are built is in the [roadmap](../roadmap.md).

## Problem

The client finds data, terminology and knowledge by schema tag, within a list of catalog scopes the caller sets.
A workspace with two tagged data sources makes every notebook and tool name one, and business users in Genie
and the Designer are asked to choose among sources they should never see. Published data is read through
`retrieve_<Type>` SQL functions whose parameters are read from `information_schema` on every run, and a user's
results need a schema of their own.

## Decision

Everything runs in an **environment**. An environment is one Unity Catalog catalog holding every dependency of
a run in standard schemas: data, knowledge (which holds terminology) and results. A client with nothing named runs in the
default environment, so no business user is ever asked which one.

### Terms

| Term | Means |
|---|---|
| Environment | A catalog tagged as one, with the standard schemas below |
| Resolver | Anything that answers a role or picks an environment. The role interfaces are `DataResolver`, `TerminologyResolver`, `KnowledgeResolver` and `ResultResolver`; every implementation also ends in `Resolver`. `EnvironmentResolver` finds an environment and returns its four role resolvers. "Provider" is retired as a term |
| Published | The read side: views that runs bind to. Business users and Genie read these |
| Store | The write side: tables that publishing writes to. A published view selects its store by default |
| Draft | A Designer library in a workspace folder, as now (`DOC-AUTHOR-IN-FOLDERS`, `DOC-AUTO-SAVE`) |

### The environment catalog

| Tag or property | Holds |
|---|---|
| `cql_databricks_environment` | The environment's name, shown in the Designer's picker |
| `cql_databricks_environment_version` | The layout version. The client refuses an environment whose layout it does not support, naming both versions. It is not part of any result's identity |
| `cql_databricks_environment_default` | `true` on the default environment |
| Catalog comment | The description. Keep it short |

The deploy makes the three tags governed tags, which only the project's admins can assign (`DEPLOY-ENVIRONMENT-TAG-POLICIES`),
so owning a catalog does not make it an environment or the default. An account with no room for the policies can turn
this off, with a warning.

Environments are found with one query of `system.information_schema.catalog_tags`. Only catalogs the caller can
see are found. Two visible defaults fail, naming both; no visible default fails, naming the tag.

| Schema | Holds | Who writes |
|---|---|---|
| `data` | Published views: one per FHIR type, named for the type in lower case (`condition`), returning the resource as a `resource` struct and optionally `patient_id`, `min_date` and `max_date` (`BIND-CONTRACT`, `DATA-SUBJECT-KEY`, `BIND-PREFILTER`), and `change_log` | The customer; the deploy for the default environment |
| `data_store` | Store tables a view selects by default | Publishing, the synthetic loader |
| `knowledge` | The published views `library`, `measure` and `valueset(valueset_id, system, code, version)` | As `data` |
| `knowledge_store` | Their store tables | Publishing; the Designer's Publish writes `library` and `measure` |
| `results` | Official results, reused by every identity: runs that bind only the environment's own resolvers | Whoever the implementer grants: publishing Jobs |
| `draft_results` | Each user's own results, in one shared schema | The Designer, as the user; the agent's tools read it |

Terminology lives in `knowledge`, not in schemas of its own. A schema is the grant boundary, and value sets have
the same readers and writers as libraries and measures, and no patient data. Patient data (`data`) and results,
which derive from it and are written per user, stay apart. Where value sets have writers of their own, grant
`MODIFY` on the store's tables rather than on the schema.

A store schema is optional. A customer may define every view over tables of their own and have no store. A run
never checks a store. A run binds the published views by name and reads no `information_schema`; a view that is
missing fails in the warehouse, naming it.

Publishing writes to the store, then reads the written ids back through the published view, once per publish.
If the view does not show them, the publish fails, saying the view does not select the store.

### Results

A result's identity is what it computes (`RUN-CLIENT-RESULT-IDENTITY`). A run finds results through its result
resolver, in two layers, in one lookup (`RUN-CLIENT-RESULT-OWNERSHIP`, `CLIENT-RESULT-RESOLVER`):

- **Its own.** The writer, a user or a service principal, owns each table it creates, and the name carries its
  digest: `<library>_<define>_<16 fingerprint hex>_<8 writer hex>`. In its own schemas a run reuses only tables its
  own identity owns. Unity Catalog shows a caller only what it holds a privilege on, and a view can return different
  rows to different callers, so a fingerprint match across identities proves nothing about the rows.
- **The official ones**, in `results`, whoever wrote them. `results` is the authority, trusted by construction like
  the published views: the implementer decides who may write it, through grants, as an existing batch process may
  already do. Whatever is there is official and read first after the caller's own. Its tables carry the row filters
  and column masks of the data they derive from, a deployment concern, so each reader sees its own rows. A writer
  of `results` must read the data unfiltered. Only results this client wrote, with its fingerprint, are reused;
  another engine's are read under grants. Making the environment tags governed (`DEPLOY-ENVIRONMENT-TAG-POLICIES`)
  keeps anyone else from making a catalog an environment.
- None found: the run computes, and a store writes the result to its own destination.

The resolver owns the chain; a run never composes layers. `EnvironmentResolver` builds them: `results(env)` reads
both layers and writes nothing, for the agent's tools and inline runs; `draft_store(env)` also writes the caller's
own into `draft_results`, for the Designer; `official_store(env)` writes `results`, for publishers. A notebook may
pass a store over a schema of its own. The client manages no other sandbox.

- Users get `USE SCHEMA` and `CREATE TABLE` on `draft_results`, and no `SELECT` or `MODIFY` on the schema. The
  maintenance Job (`RUN-CLIENT-MAINTENANCE`) holds `MANAGE` on it and expires its tables.
- `draft_results` is one schema. Retention keeps it under the 10,000-table limit per schema; a create refused
  because the schema is full fails, saying to shorten retention or run the cleanup.
- `official_store` writes a run only when it binds nothing but the environment's own resolvers: every library from
  its knowledge, every value set from its terminology, every retrieve from its data. CQL text, `%%cql` cells, a
  folder library, value sets from a volume, a DataFrame or a custom resolver is refused before it computes, naming
  what it bound. `%%cql` and `cql()` keep nothing.
- A value set enters a kept result's identity as a digest of its codes, read in one query per kept run, so a value
  set edited in a volume or republished in place recomputes and an unchanged one is reused
  (`CLIENT-RESOLVER-IDENTITY`). The compile response lists each recipe's value sets and the client completes the
  fingerprint from the relations the binding created. Data enters by name.
- Concurrent writers of one result write the same rows to the same table, so a collision costs duplicate work or
  one failed writer, never a wrong result. The writer looks the result up again right before writing and after a
  conflict, and reuses it if it is done.
- Per-user schemas (`cql_private_<digest>`) and `designer_results_catalog` go.

### Binding at the call site

`describe` reports every call site: each data retrieve, each value set reference and each library include, with
the library (name and version) and define it is in, and its request (`BIND-RETRIEVE-REQUEST`). A resolver binds
each site to a relation by its id, so a custom resolver can choose per site. Relations are tables, views, volume
folders and DataFrames. SQL functions are no longer a relation kind: no named arguments, no parameter
discovery.

The compiler stays pure: it never calls a resolver or reads the catalog. It reports sites and takes answers.
Each role in the compile request carries a rule, a map by site id, or both; for each site the compiler uses the
map entry if there is one, else the rule. The environment's rule is `<catalog>.data.<type>` for data,
`<catalog>.knowledge.valueset` for terminology and `<catalog>.knowledge` for knowledge. A run whose resolvers
bind by rule makes one compiler call. Only a resolver that binds per site calls `describe` first, then compiles
with its map: two calls, the CQL translated twice. The fingerprint covers the relation each site was bound to,
whether it came from the rule or the map.

### Deployment

- The runtime schema lives in the catalog the deployer chooses. In the default deployment that catalog is
  `uc_catalog`, and it is also the default environment.
- The deploy creates the default environment's schemas, views and stores, loads the synthetic population and the
  example measures into the stores, and tags the catalog. It sets `cql_databricks_environment_default = true`
  only if no visible catalog already has it.
- The large synthetic population becomes a second environment in its own catalog.

### Who picks the environment

| Caller | Environment |
|---|---|
| Designer | The one the user picks; the data-source picker goes |
| Agent tools in the Designer's assistant drawer | The one picked in the Designer, passed by the Designer; never asked |
| Agent tools from Genie or MCP | The default, always. No tool takes an environment argument |
| Notebooks and Jobs | The default, or one named in `connect()`, or resolvers passed in |

## What this replaces

| Replaced | By |
|---|---|
| Schema tags `cql_databricks_data_source`, `_terminology`, `_knowledge`; `TaggedResolver` | Catalog tags and `EnvironmentResolver` |
| `catalog_scopes`; the scope as result trust boundary (`DEC-RESULT-SCOPE`) | The environment. Results are read from the environment's `results` or the caller's own `draft_results` tables |
| `retrieve_<Type>` functions, `FunctionRelation`, named arguments, the layout and parameter queries | Published views bound by name |
| `publish_data_source` writing functions (`BIND-PUBLISH`) | It writes the mapping as a published view |
| Per-user result schemas | `draft_results` with object isolation |
| The function branch in `DEC-PROVIDER-RELATION` | Views only for the Apps; per-site choice through a custom resolver in notebooks and Jobs |
| `DEC-NOTEBOOK-DEFAULT-RESOLVER` | Settled: the default environment |
| `DEC-PUBLISHED-STORE-LOCATION` | Settled: the environment's standard schemas |
| Provider names (`DataProvider`, `TerminologySource`, `KnowledgeSource`, `ResultStore`, `CLIENT-*-PROVIDER` IDs) | `*Resolver` names; the old IDs marked SUPERSEDED, naming the new |

Kept from commit `c78e8e7`: the role interfaces (renamed), the relation kinds except functions, `Layered`
terminology, the compile request by role, the identity of bound relations in the fingerprint, overlays, the
volume and DataFrame reshape, the Delta result store.

## Requirements

With the renamed role IDs `CLIENT-DATA-RESOLVER`, `CLIENT-TERMINOLOGY-RESOLVER`, `CLIENT-KNOWLEDGE-RESOLVER`,
`CLIENT-RESULT-RESOLVER`, `CLIENT-RESOLVER-READ-WRITE`, `CLIENT-RESOLVER-OVERLAY` and `CLIENT-RESOLVER-IDENTITY`.

| ID | Says |
|---|---|
| `CLIENT-ENVIRONMENT` | An environment is a catalog with the tags and schemas above; every run is in exactly one |
| `CLIENT-ENVIRONMENT-RESOLVER` | `EnvironmentResolver` finds environments by tag, picks the default or a named one, refuses an unsupported layout version, and fails on two defaults or none |
| `CLIENT-RESOLVERS` | The role interfaces and their naming |
| `DATA-ENVIRONMENT-LAYOUT` | The standard schemas, the published views and their optional stores |
| `BIND-PUBLISHED-VIEW` | Published data, terminology and knowledge are bound by view name with no catalog queries; publishing writes the store and reads it back through the view once |
| `BIND-CALL-SITE` | `describe` reports every call site with its context and request; each role in the compile request binds by a rule, a map by site id, or both, the map winning |
| `RUN-CLIENT-DRAFT-RESULTS` | The shared `draft_results` schema, its grants, its expiry, and published-only writes to `results` |
| `RUN-CLIENT-RESULT-OWNERSHIP` | A result belongs to the identity that computed it, and only that identity reuses it |
| `DEPLOY-DEFAULT-ENVIRONMENT` | The deploy builds and loads the default environment and tags it default only if none is |
| `DOC-ENVIRONMENT-PICKER` | The Designer picks an environment, not a data source |
| `AGENT-DEFAULT-ENVIRONMENT` | The agent's tools run in the default environment and never ask for one |

## Plan

| Step | Does | After |
|---|---|---|
| `CLIENT-ENVIRONMENT` | Write the requirements above; mark what they replace SUPERSEDED; list the settled decisions | — |
| `CLIENT-RESOLVERS` | Rename the roles and shipped implementations | `CLIENT-ENVIRONMENT` |
| `BIND-CALL-SITE` | Call sites in `describe`; rules and maps in the request; fix the empty retrieve `parameters`; remove function relations and named arguments; conformance fixtures | `CLIENT-ENVIRONMENT` |
| `CLIENT-ENVIRONMENT-RESOLVER` | Discovery, layout version check, binding by view name (`DATA-ENVIRONMENT-LAYOUT`, `BIND-PUBLISHED-VIEW`) | `CLIENT-RESOLVERS`, `BIND-CALL-SITE` |
| `RUN-CLIENT-DRAFT-RESULTS` | The shared draft schema, ownership and reuse by identity (`RUN-CLIENT-RESULT-OWNERSHIP`), table names, grants, maintenance, the value-set code digest: measure its query on the warehouse before building on it, and report | `CLIENT-ENVIRONMENT-RESOLVER` |
| `DEPLOY-DEFAULT-ENVIRONMENT` | Deploy the default environment; move the workspace's large population to its own catalog | `CLIENT-ENVIRONMENT-RESOLVER` |
| `DOC-ENVIRONMENT-PICKER` | The Designer's picker, draft runs, Publish to the knowledge store; the agent's default (`AGENT-DEFAULT-ENVIRONMENT`) | `RUN-CLIENT-DRAFT-RESULTS`, `DEPLOY-DEFAULT-ENVIRONMENT` |
| `CLIENT-ENVIRONMENT-REMOVALS` | Remove everything in "What this replaces", and what `CLIENT-PROVIDER-REMOVALS` listed | `DOC-ENVIRONMENT-PICKER` |
| `CLIENT-ENVIRONMENT-DOCS` | Client README, notebooks, deploy docs | `CLIENT-ENVIRONMENT-REMOVALS` |
