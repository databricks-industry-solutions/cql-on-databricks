# Durable calculations

How `materialize()` decides what to compute and where results go (`RUN-CLIENT-DURABLE`).

## What runs

The client compiles the query. The compiler returns one definition per define, and per materialized
retrieve, each with its fingerprint and whether it may be reused. The client runs only the closure of the defines you ask
for. Each define starts as soon as the defines it reads are done, up to 4 at once by default
(`parallelism=`), so a long define holds up only the defines that read it (`RUN-CLIENT-PARALLEL-DEFINES`). On a SQL
warehouse they run side by side in your one session; on Spark, one at a time. The first failed write cancels every
statement still running, starts nothing more, and is never recorded as complete.

On a SQL warehouse, hold one `warehouse_session(...)` the way you hold a Spark session, pass it as `run=`,
and close it when you are done (`RUN-CLIENT-SESSION-REUSE`). The runtime functions' sandbox starts once per
session, so only the first statement that calls them pays that start; later statements, runs and measures
run warm. The session's statements share one connection. The background tag writes use its
side connections and never wait on it. An App holds one session per signed-in user for its process
(`cql_databricks_common.warehouse`).

Every statement sent to a SQL warehouse goes through that session; nothing calls the Statement Execution API
(`RUN-CLIENT-WAREHOUSE-CONNECTOR`). Rows come back typed, as Spark's do. ARRAY, STRUCT and MAP values come
back as JSON text. A statement still running after 30 minutes fails.

## Fingerprints and reuse

The compiler computes each definition's fingerprint, a SHA-256 over (`RUN-CLIENT-RESULT-IDENTITY`):

- an identity format version;
- the define's emitted SQL;
- the values of the parameters it uses;
- the fingerprints of the defines it reads;
- the address (`catalog.schema`) of the bound data source.

Nothing else is part of it: not the destination, your identity, the compiler version, the library and define
names or a population label (`DEC-RESULT-IDENTITY`). So one calculation has one fingerprint, and one table
name, in every schema and for every user. A durable compile that retrieves data must bind a tagged data
source; an explicit per-type binding or the session mount fails.

In `skip_if_computed` mode, the default, a completed table with the define's name is reused when its
`cql_fingerprint` tag matches the whole fingerprint. A table with that name and another fingerprint fails
loud. The results it read are not checked: freshness is the data source's. A non-deterministic define, and
every define that reads one, is never reused. `recompute` mode always computes. `computed_since` sets a
cutoff: a result completed before it is recomputed.

The client doesn't watch your source data. Refresh when your data, value sets or runtime functions change
(`RUN-CLIENT-REFRESH`).

## Tables and tags

A result table is named `<library>_<define>_<16 hex digits of the fingerprint>`
(`RUN-CLIENT-TABLE-NAMES`). The schema says whose results they are.

Each table carries four Unity Catalog tags, never CQL, parameters or patient values: `cql_result_format`,
`cql_result_state`, `cql_computed_at` and `cql_fingerprint`. A table is marked complete only after its write
succeeds. Later sessions, and other users with grants, find a result by its table name and these tags,
in the destination and then in each `catalog.schema` result scope (`RUN-CLIENT-CATALOG-CHECKPOINTS`,
`CLIENT-DISCOVERY-SCOPE`).

## Private and shared results

- Private: a schema owned by one user. `ensure_private_destination()` creates it in the catalog an admin
  provides (`RUN-CLIENT-PRIVATE-PROVISIONING`).
- Published: a Job writes to a shared schema under its run-as identity. Readers call `resolve_results()`,
  which reads with their own grants and never computes on a miss (`RUN-CLIENT-PUBLISHED`).

The caller serializes overwrites of a result table. There is no distributed lock.

## Cleanup

Results stay until removed. `RUN-CLIENT-MAINTENANCE` describes the maintenance Job for private results.
