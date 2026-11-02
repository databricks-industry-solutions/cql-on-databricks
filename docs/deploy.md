# Deploy

Everything deploys as one Asset Bundle, `databricks.yml`, with one command, `scripts/deploy/deploy.sh`.

## Before you start

In the workspace:

- A Unity Catalog catalog for the product, `uc_catalog`. The deploy creates the `runtime` and `designer`
  schemas, the default environment's schemas and a Volume in it, and grants the Apps and users on it, so the deployer
  must own the catalog or hold `MANAGE` on it.
- A serverless SQL warehouse.
- These workspace settings are on: Databricks Apps; user authorization for Apps, so the Apps act as the
  signed-in user (without it the Apps start, and every data request fails); and Unity Gateway. Set
  `enable_databricks_apps`, `enable_apps_user_authorization` and `enable_unity_gateway` to `true` and the deploy
  turns them on, as a workspace admin (`DEPLOY-WORKSPACE-SETTINGS`). Each defaults to `false`, which leaves the
  setting as it is.
- Workspace admin rights for the first deploy: it creates a maintenance service principal.
- To connect Genie One: account admin rights, through an account-level CLI profile. The deploy creates the
  agent's OAuth client in the account ([below](#the-agent-in-genie-one)). Without them, everything else deploys.
- For Genie One to load the agent's skills, "Unity Gateway skills in Genie" is on (Previews page). UC skills are
  in Beta. `enable_genie_uc_skills: true` turns it on the same way.

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
| `uc_catalog` | The catalog for the runtime schema and the Volume. The deploy builds the default environment in it: its schemas, the example measures and patients, its tags (`DEPLOY-DEFAULT-ENVIRONMENT`). Required |
| `warehouse_id` | The serverless SQL warehouse. The deploy registers the runtime functions with it, and the Apps run on it. Required |
| `app_users_group` | The account group allowed to use the Apps. It gets read access to the default environment and `CREATE TABLE` on its `draft_results`. Unity Catalog grants take account groups only, so a workspace-local group such as `users` fails the deploy. Required |
| `admins` | Optional. The project's admins besides you: users by email, service principals by application ID, or groups, as a list. Every deploy adds each to the workspace and its `admins` group (`DEPLOY-PROJECT-ADMINS`), and lets each tag a catalog an environment or the default (`DEPLOY-ENVIRONMENT-TAG-POLICIES`). Empty by default |
| `govern_environment_tags` | Optional, `true` by default: the deploy makes the environment tags governed tags (`DEPLOY-ENVIRONMENT-TAG-POLICIES`). An account holds at most 1,000 governed tags; if yours has no room for three more, set `false`, and the deploy warns that anyone who can tag a catalog can make it an environment |
| `workspace_users_group` | The workspace group that may read the workspace skills folder Genie Code loads, such as the workspace's `users`. A workspace folder takes workspace groups; Unity Catalog takes account groups only, so the other groups can't serve. Required |
| `run_log_readers_group` | The group allowed to read the Designer run log. Required |
| `designer_catalog` | Optional. The catalog of the Designer's shared `designer` schema. Defaults to `uc_catalog` |
| `assistant_endpoint` | The Model Serving chat endpoint the assistant drawer calls. Check it exists in your workspace. Required |
| `canonical_base` | Optional. The base of the canonical URL every library and measure created in the Designer gets: `<base>/Library/<Name>`, `<base>/Measure/<Name>` (`DOC-OPEN-SAVE`). Defaults to `http://example.org/fhir`, the examples' base. Set your organization's own before anyone creates a library, since the URL is an identity a library keeps |
| `agent_skills_schema` | The `catalog.schema` the agent's skills and its MCP Service are in. Created if missing. Required |
| `agent_users_group` | The account group that may use the agent from Genie One. Unity Catalog grants take account groups only, so `app_users_group` may not do. Required |
| `agent_account_profile` | An account admin's account-level CLI profile. The deploy creates the agent's OAuth client with it ([below](#the-agent-in-genie-one)). Optional: needed only to create or repoint the agent's Genie One connection |
| `enable_databricks_apps`, `enable_apps_user_authorization`, `enable_unity_gateway`, `enable_genie_uc_skills` | `true` turns that workspace setting on, as a workspace admin (`DEPLOY-WORKSPACE-SETTINGS`). Optional, each default `false`, which leaves the setting as it is |

`scripts/deploy/deploy.sh` stops if a required setting is empty. `databricks.yml` holds no workspace settings.
Never commit host names, profiles or tokens.

## Content

The deploy builds the default environment in `uc_catalog` and loads the example measures and their patients from
`examples/` into its stores itself (step 11 below, `DEPLOY-DEFAULT-ENVIRONMENT`, `CONTENT-EXAMPLE-PRELOAD`, with
`scripts/deploy/shipped_sources`); there is nothing to import first. To import other measures, ask Genie Code or
Genie One: the deploy publishes the agent's `import-quality-measures` skill (step 10 below, `CONTENT-IMPORT-SKILL`).

An environment is a catalog (`CLIENT-ENVIRONMENT`, [client README](../compiler/client/README.md#environments)). The
deploy tags `uc_catalog` as the default environment only if no catalog the deployer can see is the default already.
To add an environment over your own data, create a catalog with the same schemas and views, and tag it.

## Deploy

```bash
scripts/deploy/deploy.sh -t default       # the target in databricks.local.yml; -p <profile> overrides its profile
```

It runs from any directory and stops on an unknown argument.

The script, in order:

1. Adds each of `admins` to the workspace and its `admins` group (`scripts/deploy/project_admins.py`,
   `DEPLOY-PROJECT-ADMINS`).
2. Creates or reuses the maintenance service principal and grants it `USE CATALOG` on `uc_catalog`
   (`scripts/deploy/grant-maintenance-access.sh`, `RUN-CLIENT-MAINTENANCE`). Its application ID becomes the
   `cql-databricks-designer-maintenance` Job's run-as. The deployer gets the Service Principal: User role on it,
   which a Job's run-as needs.
3. Builds and stages each App.
4. Runs `databricks bundle deploy`, which creates the runtime schema and Volume, the Designer's shared
   schema, the default environment's schemas and their schema grants (`DEPLOY-DEFAULT-ENVIRONMENT`,
   `DEPLOY-APP-GRANTS`), the Apps and their permissions, and the `cql-databricks-designer-maintenance` Job.
   `app_users_group` gets `USE SCHEMA` and `SELECT` on `data`, `knowledge` and `results`, and
   `USE SCHEMA` and `CREATE TABLE` on `draft_results`; the Designer's service principal gets `USE SCHEMA` and
   `SELECT` on `knowledge`. The Job's daily schedule deploys paused: unpause it after one run has
   been checked on the workspace.
5. Uploads the compiler's JRE and jars, the runtime function jar, and the client and agent wheels to the Volume.
6. Registers the `cql_*` runtime functions in Unity Catalog (`scripts/deploy/register-uc-udfs.sh`).
7. Grants each App's service principal `USE CATALOG` on `uc_catalog`, and revokes the catalog-wide `USE SCHEMA` and
   `SELECT` an earlier deploy granted (`scripts/deploy/grant-app-access.sh`).
8. Creates the Designer run log tables, whose schema grants the bundle makes (`scripts/deploy/provision-run-log.py`).
9. Publishes the example notebooks to a shared workspace folder (`scripts/deploy/publish-examples.sh`).
10. Starts every App.
11. Publishes every skill from one definition (`scripts/deploy/publish-skills.sh`, `DEPLOY-AGENT-SKILLS`): as Unity
    Catalog skills, which Genie One loads, and to the workspace skills folder `/Workspace/.assistant/skills/`, which
    Genie Code loads. It creates that folder and lets `app_users_group` read it. Creating it needs a workspace
    admin; for any other deployer the skills go to `/Shared/CqlDatabricks/skills`, and the deploy says Genie Code
    won't load them from there. It then registers the agent under Unity Gateway (`scripts/deploy/register_agent_mcp.py`,
    `DEPLOY-AGENT-REGISTRATION`). See [The agent in Genie One](#the-agent-in-genie-one).
12. Makes the three environment tags governed tags (`scripts/deploy/environment-tag-policies.sh`,
    `DEPLOY-ENVIRONMENT-TAG-POLICIES`), so only you and `admins` can tag a catalog an environment or the default.
    Creating a tag policy needs a workspace admin, unless an account admin changed who may.
13. Fills the default environment's schemas (`scripts/deploy/provision-examples.py`, `DEPLOY-DEFAULT-ENVIRONMENT`):
    the example measures and value sets in `knowledge_store`, through the import; the
    synthetic small population in `data_store`; the views over them; and the catalog tags. Then the two grants the
    bundle can't make: `USE CATALOG` on `uc_catalog` to `app_users_group`, and `USE SCHEMA` and `MANAGE` on
    `draft_results` to the maintenance principal.

Every step can be run again. If a step fails, fix the cause and run the whole script again.

To check the deploy, open the Designer App, or run `scripts/test.sh integration` from your checkout. It
runs the compiler smoke and every example notebook against the workspace `databricks.local.yml` names,
as one-time serverless runs. `deploy.sh` never runs it.

## Load synthetic data

Optional. The deploy loads the synthetic population small, about 1,000 patients per example measure, as the
default environment (`synthetic-population-small`). A large synthesized population with expected results per patient
is an environment of its own, in its own catalog, never `uc_catalog` (`DATA-SYNTH-LARGE`): generate and land a cohort
per measure after the deploy with `scripts/generate-large-data.sh --catalog <catalog> --users-group <group>
--designer-app <app>` ([synthetic data](../loader/docs/synthetic-data.md)).

## Proxies and mirrors

Set these on the machine you deploy from. None of them goes in `databricks.local.yml`.

| What | Fetched from | To use a mirror or proxy |
|---|---|---|
| JVM build dependencies, and the Compiler App's three large jars | Maven Central | Set `mavenRepoUrl=<url>` in `~/.gradle/gradle.properties`, or pass `-PmavenRepoUrl=<url>`. A mirror that needs auth reads `MAVEN_REPO_USER` and `MAVEN_REPO_TOKEN`. A proxy goes in the same file: `systemProp.https.proxyHost` and `systemProp.https.proxyPort`. The deploy copies the large jars from this build (`SVC-STAGE-LOCAL-JARS`) |
| The Compiler App's JRE | GitHub (Eclipse Temurin) | Set `HTTPS_PROXY`, or set `CQL_DATABRICKS_JRE_URL` to a mirror's copy. Keep the file name: the App looks the file up in the Volume by name |
| Designer packages | npm | npm's own `registry` and `https-proxy` settings |
| Python packages | PyPI | Set `HTTPS_PROXY`. For a PyPI mirror, set pip's `PIP_INDEX_URL` before `scripts/setup.sh` |

If an artifact can't be fetched or uploaded, the deploy stops.

## Redeploy

Run the script again.

The bundle's grants on an environment schema are that schema's whole grant list: a grant you make on one by hand is
revoked by the next deploy. Put it in `databricks.yml` instead. A schema that existed before the bundle managed it, from
an earlier deploy, is bound to the bundle once: `databricks bundle deployment bind environment_<schema>
<uc_catalog>.<schema>`. Also redeploy after loading a new schema into a catalog that holds Designer results
too: in that case the Apps are granted schema by schema, so they don't see a new schema until
`scripts/deploy/grant-app-access.sh` runs again.

## The agent in Genie One

Genie One calls only MCP servers connected through Unity Gateway. The deploy connects the agent App per user, so
each tool call runs as the person asking (`SEC-OBO`):

1. A Unity Catalog HTTP connection, `mcp_cql_databricks_<workspace id>`, to the App's `/mcp`. Each user signs
   in to it once, as themselves.
2. An MCP Service, `<agent_skills_schema>.cql_databricks`, over that connection. `agent_users_group` gets
   `EXECUTE` on it, never `USE CONNECTION`.
3. The UC skills `population_question`, `experiment`, `explain_cql`, `cql_authoring` and `import_quality_measures`, in
   the same schema. `agent_users_group` gets `READ VOLUME` on each. `clinical-explain` is not published: it is the Designer's
   narration contract.

The connection signs users in through an account OAuth app integration, `mcp_cql_databricks_<workspace id>_u2m`.
The workspace offers no dynamic client registration, so the deploy creates the integration through the account
API, and only an account admin may. **The first deploy that connects Genie One is run by an account admin.**
Sign in to the account once, with an account-level CLI profile:

```bash
# AWS. Azure: https://accounts.azuredatabricks.net. GCP: https://accounts.gcp.databricks.com
databricks auth login --host https://accounts.cloud.databricks.com --account-id <account id> --profile <account profile>
```

Set that profile's name as `agent_account_profile` in `databricks.local.yml`. The deploy checks it early and
stops if it can't list the account's OAuth clients. The client's secret goes straight from the account API into
the connection: nobody sees it or stores it.

After that, redeploys need no account admin and no account profile. A redeploy that finds the connection
pointing at the App changes nothing and makes no account call, so changing the agent's code or tools is a normal
deploy. Only a connection that must be created or repointed needs the account admin again: a new workspace, or
an App with a new URL. It gets a new client, and every user signs in again. Without `agent_account_profile`,
the deploy leaves such a connection alone and says Genie One is not connected.

Then each user signs in once: Catalog, External data, Connections, the connection, Sign in. In Genie One they
add the MCP Service as a connector. Genie One shows the tools' results as tables and text; it does not draw MCP
App views.

Unity Gateway can't register a Databricks App as your own MCP server, so the deploy reaches the App through
this HTTP connection instead.

## What it creates

| Resource | Name |
|---|---|
| Compiler App | `cql-databricks-compiler` |
| Designer App | `cql-databricks-designer` |
| MCP App | `mcp-cql-databricks` |
| Skills and MCP Service | Five UC skills and `cql_databricks` in `<agent_skills_schema>`; the same skills in `/Workspace/.assistant/skills` |
| Agent connection | `mcp_cql_databricks_<workspace id>` |
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

The Apps act as the signed-in user, so users need their own grants. The deploy grants `app_users_group`
`CAN_USE` on the Apps and, in the default environment, `USE CATALOG`, `SELECT` on `data`, `knowledge` and
`results`, and `USE SCHEMA` and `CREATE TABLE` on `draft_results` (`DEPLOY-DEFAULT-ENVIRONMENT`). No user gets
`SELECT` on `draft_results`: each reads only the tables they own (`RUN-CLIENT-DRAFT-RESULTS`). Grant the same group:

| On | Grant | Why |
|---|---|---|
| The SQL warehouse | `CAN_USE` | Every App query runs on it as the user |
| `<uc_catalog>.runtime` | `USE SCHEMA`, `EXECUTE` | The `cql_*` functions |
| Volume `<uc_catalog>.runtime.artifacts` | `READ VOLUME` | Notebooks install the client wheel from it |
| Each other environment's catalog | The same grants the deploy makes in the default one | Its data, value sets, libraries and results |
| `assistant_endpoint` | `CAN QUERY` | The assistant drawer |

## Official results

An environment's `results` is official: every run reuses what is there, whoever wrote it, after the user's own
results (`RUN-CLIENT-RESULT-OWNERSHIP`). So decide who may write it, and grant `CREATE TABLE` and `MODIFY` on it to
those principals only, typically one service principal that runs your publishing Jobs through
`EnvironmentResolver.official_store` (`RUN-CLIENT-PUBLISHED`). Two rules the client doesn't check:

- A writer of `results` must read the data unfiltered. A result computed through a row filter or column mask is a
  partial result that every reader reuses.
- Put on the result tables the row filters and column masks of the data they derive from, so each reader sees only
  their own rows of a shared result.

The deploy makes the environment tags governed where the account allows it (`DEPLOY-ENVIRONMENT-TAG-POLICIES`), so
only the principals it names can make a catalog an environment.

Each user consents once to an App's user API scopes. Adding a scope later doesn't prompt again. Until the
user consents again, calls that need the new scope fail. Restarting the App doesn't help.

## App service principals

Each App has its own service principal. Everything the Apps read about a user's data and results, they read as the
user. The Designer's principal reads only the environments' knowledge it holds in memory (`KNOWLEDGE-LAKE-DIRECT`).
The deploy grants both principals `USE CATALOG` on `uc_catalog`, and the bundle grants the Designer's `SELECT` on its
`knowledge` (`DEPLOY-APP-GRANTS`), and never a catalog-wide `SELECT`, which `draft_results` would inherit. It doesn't
grant them:

- `CAN_USE` on the SQL warehouse. Grant it, unless the warehouse already allows `users`.
- Anything in another environment's catalog. Grant the Designer's principal `USE CATALOG` there and `SELECT` on
  `knowledge`, or the Designer can't show that environment's libraries.

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
