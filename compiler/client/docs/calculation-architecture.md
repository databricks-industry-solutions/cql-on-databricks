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
- the identities of the data and terminology resolvers bound.

The client then folds in a digest of the codes of each value set the define uses, read in one query
(`CLIENT-RESOLVER-IDENTITY`). Nothing else is part of it: not the destination, your identity, the compiler version,
the library and define names or a population label (`DEC-RESULT-IDENTITY`). A durable compile that retrieves data
must bind a reusable data resolver.

In `skip_if_computed` mode, the default, a completed result whose `cql_fingerprint` tag matches the whole
fingerprint is reused. A table with the name and another fingerprint fails loud. The results it read are not
checked: freshness is the data source's. A non-deterministic define, and every define that reads one, is never
reused. `recompute` mode always computes. `computed_since` sets a cutoff: a result completed before it is passed
over.

The client doesn't watch your source data. Refresh when your data, value sets or runtime functions change
(`RUN-CLIENT-REFRESH`).

## Where results are found and written

Every result is found, and written, through the result resolver or store you pass (`CLIENT-RESULT-RESOLVER`). The
shipped ones read one schema each, in one statement:

| Resolver | Reuses | Writes |
|---|---|---|
| `SchemaResultResolver(schema)` | your own: tables named with your digest that you own | nothing |
| `SchemaResultResolver(schema, reuse="official")` | anyone's, the latest first | nothing |
| `SchemaResultStore(schema)` | your own | your own, as you |

`Overlay(first, then)` layers them (`CLIENT-RESOLVER-OVERLAY`): `then` is asked only for what `first` did not find,
and an overlay over a store writes through it. An environment's are two layers (`RUN-CLIENT-RESULT-OWNERSHIP`):

1. **Your own**, in `draft_results` or the schema a store writes.
2. **The official ones**, in the environment's `results`: anyone's. That schema is trusted by construction; the
   implementer decides who may write it (`RUN-CLIENT-PUBLISHED`).

What isn't found is computed and written to the store's schema, as you. `resolve_results()` and inline runs read
through a resolver and never compute on a miss.

| Store | Destination | Who uses it |
|---|---|---|
| `EnvironmentResolver.draft_store(env)` | the environment's `draft_results`, shared, each user's tables their own | the Designer |
| `EnvironmentResolver.official_store(env)` | the environment's `results`, only for the environment's own content | the implementer's publishing Jobs |
| `Overlay(SchemaResultStore((catalog, schema)), ...)` | a schema of your own | notebooks and Jobs |

## Tables and tags

A result table is named `<library>_<define>_<16 hex digits of the fingerprint>_<8 hex digits of the writer's
digest>` (`RUN-CLIENT-TABLE-NAMES`), so two identities computing one calculation write two tables.

Each table carries four Unity Catalog tags, never CQL, parameters or patient values: `cql_result_format`,
`cql_result_state`, `cql_computed_at` and `cql_fingerprint`. A table is marked complete only after its write
succeeds, and marked pending before it is replaced (`RUN-CLIENT-CATALOG-CHECKPOINTS`).

Two runs of yours computing one result write the same table. Each looks the result up again just before it writes,
and again if its write fails, and reuses it if the other run finished it. There is no distributed lock.

## Cleanup

Results stay until removed. `RUN-CLIENT-MAINTENANCE` describes the maintenance Job that expires `draft_results`.
