# Runtime UC

`compiler/runtime/uc` is the Databricks wrapper around the runtime functions in `compiler/runtime/udf`.

| File | What it does |
|---|---|
| `CqlDatabricks.scala` | `CqlDatabricks.init(spark)` registers the `cql_*` functions (`EXEC-UDF-LIBRARY`) on the current session. It calls `CqlRuntimeUdfs.registerAll`. |
| `UcCatalog.scala`, `UcCatalogTool.scala` | Generate the `CREATE FUNCTION` DDL that registers the functions as Unity Catalog Scala functions (`SVC-RUNTIME-UDF-UC`). |

## How a session gets the functions

- Default: the deploy registers the functions in Unity Catalog, and the client qualifies every emitted call
  against that schema (`SVC-NOTEBOOK-RUNTIME-BIND`). This works on serverless. The notebooks in
  `examples/` use it and have no `%scala` cell.
- Single-user classic cluster: the client registers the functions over py4j.
- Shared classic cluster with no Unity Catalog functions: run one `%scala` cell first. `init` is
  idempotent.

```scala
%scala
com.databricks.cql.platform.CqlDatabricks.init(spark)
```

## Build and test

```bash
./gradlew :compiler:runtime:uc:jar          # the jar
./gradlew :compiler:runtime:uc:test         # CqlDatabricksSpec, UcCatalogSpec
./gradlew -q :compiler:runtime:uc:printUcDdl --args="--catalog <c> --schema <s> --jar <path>"   # the DDL, as JSON
```

`scripts/deploy/deploy.sh` runs `scripts/deploy/deploy-runtime-artifacts.sh`, which uploads this jar and the runtime jar
to the runtime Volume, then `scripts/deploy/register-uc-udfs.sh`, which registers the functions from
`printUcDdl`. See the [deploy guide](../../../docs/deploy.md).
