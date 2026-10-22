# Compiler service

The compiler runs as an HTTP service, separate from any UI. It is stateless, holds no data, and compiles
but never runs SQL.

## Hosts

| Host | Code | Use |
|---|---|---|
| The Compiler Service App | `compiler/server` (`CompilerServiceApp`) | The deployed service on Databricks, with bearer-token auth (`SVC-DEPLOY-BUNDLE`, `SVC-API-HOST`) |
| A Unity Catalog function | `compiler/server` (`UcCompiler`) | The same protocol inside a UC Scala UDF, called through the caller's SQL runner (`SVC-COMPILER-UC`) |
| The local compile host | `compiler/service` (`OssCompileHostMain`) | Local development and tests only. JDK `HttpServer`, no auth, binds to `127.0.0.1`. Has no terminology, so it rejects value-set membership |

Run the local host with `./gradlew :compiler:service:runCompileHost -Dcqldatabricks.hostPort=<port>`.

The App starts from `compiler/server/deploy/bootstrap.py`, which fetches the JRE and the public jars it
needs and starts the JVM. All three hosts call the same `CompileService`.

## Endpoints

Paths are lowercase kebab-case (`BUILD-URL-KEBAB`).

| Endpoint | Request | Response | Hosts |
|---|---|---|---|
| `POST /compile-sql` | `{elm or cql, deps?, target?, boundaries?, context?, overrides?, parameters?, sources?, …}` | `{statements: [{name, kind, sql}], target, defines}` (`EMIT-SQL-EXECUTE`) | all |
| `POST /compile-pipeline` | the same, plus `durable?`, `libraries?` | `{compilerVersion, datasets: [...]}` ([pipeline-runtime.md](pipeline-runtime.md)) | all |
| `POST /describe` | `{elm or cql, deps?, context?, …}` | the `LibraryGraph`: defines, dependencies, boundaries, versions, and every retrieved resource type (`EMIT-DESCRIBE-RETRIEVE-TYPES`) | all |
| `GET /capabilities` | | the compiler version and protocol | all |
| `GET /health` | | `{status: "ok"}` | App and local |
| `POST /translate` | `{cql, includes?}` | `{elm, diagnostics, ok}` | App and UC |
| `POST /resource-schema` | `{types: [...]}` or `{}` | per-type mount DDL and the same struct as Spark's JSON schema (`CLIENT-RESOURCE-SCHEMA`), or the list of modeled resource types (`SVC-RESOURCE-SCHEMA`) | App and UC |
| `POST /merge-cql` | `{fragments: [cql, ...]}`, header-less, in order | `{cql, defines}`: one library, the latest declaration of each name winning, and the defines of the last fragment (`SVC-NOTEBOOK-SESSION-SCOPE`) | all |

The caller resolves the CQL include closure and sends it in `deps` (`BUILD-COMPILE-LINK`). The service
links what it's given and does no library lookup. A missing dependency fails the compile.

`sources` is the retrieve binding table ([data-binding.md](data-binding.md)). The Python client builds the
request in `CompileRequest.to_json` (`compiler/client/src/cql_databricks/compiler/providers.py`).

## Properties

- Stateless: no session, no workspace, no catalog query and no SQL execution. Schema comes from the static
  FHIR model (`SVC-STATELESS`).
- Deterministic: the same request gives the same output (`CORE-DETERMINISM`).
- Cached (`SVC-COMPILE-CACHE`): the App keeps an in-process LRU of successful compile responses, at most
  128 entries and 64 MiB. The key is the compiler version, the operation and the whole request body.
- Credentials: a Job calls the App with its own identity, through the token exchange, which retries
  `invalid_grant` (`SVC-EXCHANGE-RETRY`).

## Retrieves

The SQL calls `retrieve_<Type>(library, value_set, code_property, codes, date_property, date_low,
date_high, parameters)`. The runtime binds these functions over its data before running the script. The
compiler doesn't know where data lives. The same compiled plan runs against the conformance corpus or
your own tables. See [data-binding.md](data-binding.md).

## Clients

The Python client reaches the service through a `CompileProvider`. `RemoteProvider` is HTTP. The client
picks the endpoint from explicit arguments, Spark conf or environment, or discovers the App in a
notebook. See the [client README](../client/README.md).

A compile response doesn't depend on data, so the client can reuse it across runs.
