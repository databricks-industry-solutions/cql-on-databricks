# Deploy

Everything deploys as one Asset Bundle, `databricks.yml`, with one command, `scripts/deploy/deploy.sh`.

## Before you start

In the workspace:

- A Unity Catalog catalog for the product, `uc_catalog`. The deploy creates the `runtime` and `designer`
  schemas and a Volume in it, and grants the Apps on it, so the deployer must own the catalog or hold
  `MANAGE` on it.
- A catalog for the Designer's private results. An admin creates it and grants Designer users
  `USE CATALOG` and `CREATE SCHEMA`. It may be the same catalog. The deployer must own it or hold `MANAGE`.
- A serverless SQL warehouse.
- A workspace admin has turned on user authorization for Apps, so the Apps can act as the signed-in user.
  The deploy doesn't check this. Without it the Apps start, and every data request fails.
- Workspace admin rights for the first deploy: it creates a maintenance service principal.

On the machine you deploy from:

- The Databricks CLI, signed in with OAuth: `databricks auth login --profile <profile>`.
- The root venv from `scripts/setup.sh` (Python 3.12), `python3.11`, JDK 17 and Node 22 or later. The
  deploy builds the Apps from this repository.
- Internet access to Maven Central, GitHub, npm and PyPI for the build. The Apps install their Python
  packages from PyPI when they start.
  Behind a proxy or a mirror, see [Proxies and mirrors](#proxies-and-mirrors).

## Configure

Copy the template and fill it in. The file is gitignored.

```bash
cp databricks.local.yml.example databricks.local.yml
```

| Setting | What it is |
|---|---|
| `workspace.host`, `workspace.profile` | Your workspace and CLI profile. Scripts read the profile from here |
| `uc_catalog` | The catalog for the runtime schema, the Volume and the imported content. Required |
| `warehouse_id` | The serverless SQL warehouse. The deploy registers the runtime functions and binds the knowledge views with it, and the Apps run on it. Required |
| `designer_results_catalog` | The catalog that holds each Designer user's private results. Required |
| `catalog_scopes` | The ordered catalog scopes the Apps discover content in (`CLIENT-DISCOVERY-SCOPE`). Required |
| `app_users_group` | The group allowed to use the Apps. Required |
| `run_log_readers_group` | The group allowed to read the Designer run log. Required |
| `designer_catalog` | Optional. The catalog of the Designer's shared `designer` schema. Defaults to `uc_catalog` |
| `assistant_endpoint` | The Model Serving chat endpoint the assistant drawer calls. Check it exists in your workspace. Required |

`scripts/deploy/deploy.sh` stops if a required setting is empty. `databricks.yml` holds no workspace settings.
Never commit host names, profiles or tokens.

## Content

The deploy loads the sample measures and their patients from `examples/` itself (step 10 below,
`CONTENT-SAMPLE-PRELOAD`); there is nothing to import first. To run an official measure, import its eCQI package
yourself after the deploy, behind its terms ([importer README](../loader/importer/README.md)):

```bash
cp .env.example .env      # sets DATABRICKS_SERVERLESS=1; add your UMLS_API_KEY for the value sets
scripts/with-local-env.sh .venv/bin/python -m content_ingest ecqi <package-file-name-or-zip>
```

## Deploy

```bash
scripts/deploy/deploy.sh -t default       # the target in databricks.local.yml; -p <profile> overrides its profile
```

It runs from any directory and stops on an unknown argument.

The script, in order:

1. Creates or reuses the maintenance service principal and grants it access to the private results catalog
   (`scripts/deploy/grant-maintenance-access.sh`, `RUN-CLIENT-MAINTENANCE`). Its application ID becomes the
   `cql-databricks-designer-maintenance` Job's run-as. The deployer gets the Service Principal: User role on it,
   which a Job's run-as needs.
2. Builds and stages each App.
3. Runs `databricks bundle deploy`, which creates the runtime schema and Volume, the Designer's shared
   schema, the Apps and their permissions, and the `cql-databricks-designer-maintenance` Job. The Job's daily
   schedule deploys paused: unpause it after one run has been checked on the workspace.
4. Uploads the compiler's JRE and jars, the runtime function jar and the client wheel to the Volume.
5. Registers the `cql_*` runtime functions in Unity Catalog (`scripts/deploy/register-uc-udfs.sh`).
6. Grants each App's service principal access to the catalogs (`scripts/deploy/grant-app-access.sh`).
7. Creates and grants the Designer run log tables (`scripts/deploy/provision-run-log.py`).
8. Publishes the example notebooks to a shared workspace folder (`scripts/deploy/publish-examples.sh`).
9. Starts every App.
10. Loads the sample measures and patients from `examples/` and creates the knowledge views
    (`scripts/deploy/provision-samples.py`, `CONTENT-SAMPLE-PRELOAD`).

Every step can be run again. If a step fails, fix the cause and run the whole script again.

To check the deploy, open the Designer App, or run `scripts/test.sh integration` from your checkout. It
runs the compiler smoke and every example notebook against the workspace `databricks.local.yml` names,
as one-time serverless runs. `deploy.sh` never runs it.

## Load synthetic data

Optional. The deploy loads the synthetic population small, about 1,000 patients per sample measure
(`synthetic-population-small`). For a large synthesized population with expected results per patient, generate
and land a cohort per measure after the deploy
([synthetic data](../loader/docs/synthetic-data.md)).

## Proxies and mirrors

Set these on the machine you deploy from. None of them goes in `databricks.local.yml`.

| What | Fetched from | To use a mirror or proxy |
|---|---|---|
| JVM build dependencies, and the Compiler App's three large jars | Maven Central | Set `mavenRepoUrl=<url>` in `~/.gradle/gradle.properties`, or pass `-PmavenRepoUrl=<url>`. A mirror that needs auth reads `MAVEN_REPO_USER` and `MAVEN_REPO_TOKEN`. A proxy goes in the same file: `systemProp.https.proxyHost` and `systemProp.https.proxyPort`. The deploy copies the large jars from this build (`SVC-STAGE-LOCAL-JARS`) |
| The Compiler App's JRE | GitHub (Eclipse Temurin) | Set `HTTPS_PROXY`, or set `CQL_DATABRICKS_JRE_URL` to a mirror's copy. Keep the file name: the App looks the file up in the Volume by name |
| Designer packages | npm | npm's own `registry` and `https-proxy` settings |
| Python packages | PyPI | Set `HTTPS_PROXY`. For a PyPI mirror, set pip's `PIP_INDEX_URL` before `scripts/setup.sh` |
| An eCQI package you import yourself | `ecqi.healthit.gov`, and VSAC (`cts.nlm.nih.gov`) for its value sets | Set `HTTPS_PROXY` |

If an artifact can't be fetched or uploaded, the deploy stops.

## Redeploy

Run the script again. Also redeploy after loading a new schema into a catalog that holds Designer results
too: in that case the Apps are granted schema by schema, so they don't see a new schema until
`scripts/deploy/grant-app-access.sh` runs again.

## What it creates

| Resource | Name |
|---|---|
| Compiler App | `cql-databricks-compiler` |
| Designer App | `cql-databricks-designer` |
| MCP App | `mcp-cql-databricks` |
| Runtime schema and Volume | `<uc_catalog>.runtime`, Volume `artifacts` |
| Designer shared schema | `<designer_catalog>.designer` |
| Example notebooks | `/Shared/CqlDatabricks/examples` |

No App uses a database. Data sources, content and results are Unity Catalog tables found by tag.

## Runtime functions

The compiled SQL calls `cql_*` runtime functions by name (`SVC-RUNTIME-UDF`). The deploy registers them as
Unity Catalog Scala functions in `<uc_catalog>.runtime`, through `warehouse_id` (`SVC-RUNTIME-UDF-UC`).
They then resolve on every compute type, including serverless. `scripts/deploy/register-uc-udfs.sh` run on its
own skips registration when `warehouse_id` is empty; `scripts/deploy/deploy.sh` stops instead.
[compiler/runtime/uc](../compiler/runtime/uc/README.md) covers the other ways a session gets them.

## Users

The Apps act as the signed-in user, so users need their own grants. The deploy grants `CAN_USE` on the
Apps to `app_users_group`, and nothing else. Grant the same group:

| On | Grant | Why |
|---|---|---|
| The SQL warehouse | `CAN_USE` | Every App query runs on it as the user |
| `uc_catalog` | `USE CATALOG` | Reach the schemas below |
| `<uc_catalog>.runtime` | `USE SCHEMA`, `EXECUTE`, `SELECT` | The `cql_*` functions and the knowledge views |
| Volume `<uc_catalog>.runtime.artifacts` | `READ VOLUME` | Notebooks install the client wheel from it |
| `<uc_catalog>.sample_measures`, `synthetic_population_small` | `USE SCHEMA`, `EXECUTE`, `SELECT` | The sample measures and their data source |
| Any schema you import content into (`ecqi_measures`, `vsac` by default) | `USE SCHEMA`, `SELECT` | Measures you imported yourself |
| Each data source's catalog and schema | `USE CATALOG`, `USE SCHEMA`, `SELECT` | The patients a measure runs over |
| `designer_results_catalog` | `USE CATALOG`, `CREATE SCHEMA` | Each user's private results |
| `assistant_endpoint` | `CAN QUERY` | The assistant drawer |

Each user consents once to an App's user API scopes. Adding a scope later doesn't prompt again. Until the
user consents again, calls that need the new scope fail. Restarting the App doesn't help.

## App service principals

Each App has its own service principal. The Designer and MCP Apps read the tagged content as that
principal, not as the user. The deploy grants them `USE CATALOG`, `USE SCHEMA` and `SELECT` on
`uc_catalog` only (`DEPLOY-APP-GRANTS`). It doesn't grant them:

- `CAN_USE` on the SQL warehouse. Grant it, unless the warehouse already allows `users`.
- Anything in a catalog in `catalog_scopes` other than `uc_catalog`. Grant them `USE CATALOG`,
  `USE SCHEMA` and `SELECT` there, or the Apps find no content in that catalog. There is no error.

`databricks apps get <app> -o json` shows an App's `service_principal_client_id`.

## Run locally

```bash
scripts/setup.sh          # once
scripts/run-app.sh --dev  # compiler, MCP server and Designer, with hot reload
```

It reads the same `databricks.local.yml`. Calculations run on the configured SQL warehouse. Local-run
settings and secrets go in the optional root `.env` (`.env.example`).

## Tests against a workspace

The conformance suite runs on a local Spark Connect server by default. To point it at a Databricks Spark
Connect endpoint, pass `-Dcqldatabricks.sparkRemote=sc://<host>...` to Gradle
(`compiler/conformance/build.gradle` shows the form).

The scale specs (`FixtureScaleSpec`, `MeasureScaleSpec`) are opt-in. They skip unless
`-Dcqldatabricks.scale=true`. They replicate small test decks on a local server, so their timings are
directional only, not benchmarks.
