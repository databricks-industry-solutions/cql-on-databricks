# Compiler

The compiler translates Clinical Quality Language (CQL) to Spark SQL. Each CQL expression evaluates over
the whole population at once, grouped by context (Patient, Practitioner and so on), not one patient at a
time.

The compiler only emits SQL text. There is no Spark session in the compile path. The caller runs the SQL
in their own session, so the output can be read, diffed and reviewed before it runs.

`compiler/core` and `compiler/runtime/udf` take no Databricks dependency (`ARCH-PURITY`). This keeps them
testable on a local Spark server. `./gradlew checkNeutrality` fails the build if a Databricks artifact
reaches either of them.

## Components

| Directory | What it does |
|---|---|
| `core` | CQL to ELM to a typed IR to Spark SQL text. Also the static FHIR R4 model |
| `service` | `CompileService`, the compile boundary, and a local HTTP host for development and tests |
| `server` | The Compiler Service App (`cql-databricks-compiler`) that hosts `CompileService` on Databricks |
| `runtime/udf` | The `cql_*` Spark functions that emitted SQL calls (`EXEC-UDF-LIBRARY`) |
| `runtime/uc` | Registers those functions on a session or as Unity Catalog functions. See [its README](runtime/uc/README.md) |
| `client` | The `cql-databricks` Python package. See [its README](client/README.md) |
| `conformance` | The conformance suite: upstream operator tests, differentials against the reference engine, the measure corpus |
| `cqf` | A wrapper over the CQF reference engine, used by the conformance suite |

The client ships Data Bindings, which find data sources, value sets and libraries by Unity Catalog tag, and
durable calculations (`cql_databricks.result`).
See the [Data Bindings README](client/src/cql_databricks/data_bindings/README.md).

## Build and test

```bash
./gradlew checkAll                        # neutrality check, every JVM test, the client tests
./gradlew checkNeutrality                 # the no-Databricks-dependency rule only
./gradlew :compiler:conformance:test      # the conformance suite
./gradlew :compiler:service:runCompileHost   # the local compile host
./gradlew clientTest                      # the Python client tests (needs scripts/setup.sh)
```

The conformance suite runs the compiled SQL on a local Spark Connect server that the build starts. See
[getting-started.md](docs/getting-started.md).

## Docs

Read in this order.

| File | What it covers |
|---|---|
| [overview.md](docs/overview.md) | Scope and the trust model |
| [engine-design.md](docs/engine-design.md) | The passes: CQL to ELM to typed IR to SQL |
| [language-semantics.md](docs/language-semantics.md) | Three-valued logic, intervals, quantities, terminology |
| [execution-model.md](docs/execution-model.md) | Set-at-a-time evaluation and the `cql_*` function catalog |
| [data-binding.md](docs/data-binding.md) | How retrieves reach data: the `(__subject_id, __resource)` contract |
| [data-model.md](docs/data-model.md) | The static FHIR R4 element model |
| [data-format.md](docs/data-format.md) | Value types, list encoding, system columns |
| [list-null-tristate.md](docs/list-null-tristate.md) | Null versus empty lists |
| [testing-and-conformance.md](docs/testing-and-conformance.md) | Differentials against the reference engine and SQL snapshots |
| [transformation-catalog.md](docs/transformation-catalog.md) | How each CQL operator lowers to SQL |
| [compiler-service.md](docs/compiler-service.md) | The compile service and its hosts |
| [pipeline-runtime.md](docs/pipeline-runtime.md) | How compiled measures run |
| [pipeline-walkthrough.md](docs/pipeline-walkthrough.md) | One measure end to end |
| [modules.md](docs/modules.md) | Gradle projects and their dependencies |
| [getting-started.md](docs/getting-started.md) | Build and run the conformance suite locally |
| [technical-implementation.md](docs/technical-implementation.md) | Toolchain versions |
| [engineering-principles.md](docs/engineering-principles.md) | Code-quality rules |
| [glossary.md](docs/glossary.md) | Terms |

Requirements are in [docs/compiler/requirements.md](../docs/compiler/requirements.md).

## Known gaps

- CQL operator coverage is incomplete. Four of the sixteen upstream operator test files are vendored:
  logical, nullological, comparison and arithmetic.
- The measure corpus is the four sample measures written for this repository (`HypertensionBPControl`,
  `DiabetesHighOrMissingA1c`, `AdultFluImmunization`, `ColorectalScreening`), copies of those in `examples/`,
  over 48 synthetic patients with expected results per patient. They run under the differential
  (`CONF-GATE-MEASURE`) and the scorekey (`CONF-GATE-SCOREKEY`). The compiler's
  harder patterns are covered by small fixtures instead. Continuous-variable and encounter-based measures
  are not in the corpus.
- Where the compiler differs from the reference engine, the difference is documented and tested.
