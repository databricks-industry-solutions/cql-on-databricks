# Databricks notebook source
# MAGIC %md
# MAGIC # 3 · A clinical quality measure, end to end
# MAGIC A published **sample measure** — a folder of `.cql` files — compiled for Spark and run over FHIR resources,
# MAGIC showing the per-patient results and how to ask follow-up questions of the same helper library the measure uses.
# MAGIC
# MAGIC **Nothing is authored in this notebook.** The measure is not a string in a cell; it is a folder of
# MAGIC `.cql` files, and it runs **by name**. That is the whole point of this module: a measure is
# MAGIC content you publish and version, not code you paste. No CQL on Databricks App, no registry, no UI — the
# MAGIC compiler, a folder, and Spark.
# MAGIC
# MAGIC The sample measure is **deliberately small** — three definitions over twenty synthetic patients — so its
# MAGIC output is easy to read and verify. In a real measure everything is larger but the shape is the same.
# MAGIC
# MAGIC **Runs on serverless — no cluster to attach.** Just **Run All**.

# COMMAND ----------
# MAGIC %pip install __CQL_DATABRICKS_ARTIFACT_VOLUME__/wheels/cql_databricks-*.whl

# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
# MAGIC %load_ext cql_databricks.compiler

# COMMAND ----------
# Start clean, so Run All gives the same output however often it runs: drop any earlier mount and any
# library a `%%cql` cell declared (CLIENT-SESSION-RESET).
from cql_databricks.compiler import reset

reset(spark)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. The content beside this notebook
# MAGIC Two directories sit next to this notebook, holding everything one measure needs, kept apart from
# MAGIC the notebook that runs it:
# MAGIC
# MAGIC ```
# MAGIC measures/     the measure and the eligibility library it includes
# MAGIC data/         FHIR NDJSON, one file per resource type
# MAGIC ```
# MAGIC
# MAGIC In a real deployment the measures folder is a **Databricks Git Folder** — your measures repo, synced by the
# MAGIC platform — or a Unity Catalog Volume. It is an ordinary directory, which is why no
# MAGIC platform-specific code appears anywhere below.
# MAGIC
# MAGIC The paths are derived from this notebook's own location, so the same cell works in the repo, in a
# MAGIC bundle copy, and in `/Shared/CqlDatabricks/examples` with nothing baked in.
# MAGIC
# MAGIC **Expected output:** the resolved folder, then the two content directories — for example
# MAGIC ```
# MAGIC content : /Workspace/Shared/CqlDatabricks/examples
# MAGIC found   : ['data', 'measures']
# MAGIC ```

# COMMAND ----------
import pathlib

# The notebook's own workspace folder, under the /Workspace mount that makes workspace files ordinary
# files — the content directories are its siblings.
_nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
CONTENT = pathlib.Path("/Workspace") / pathlib.PurePosixPath(_nb).parent.relative_to("/")

# Fail loud with the path rather than later with a confusing empty result.
missing = [d for d in ("measures", "data") if not (CONTENT / d).is_dir()]
if missing:
    raise FileNotFoundError(
        f"{missing} not found beside this notebook in {CONTENT} — they are published alongside it; if you "
        f"copied the notebook on its own, copy them too, or point CONTENT at your own content directory")

print("content :", CONTENT)
print("found   :", sorted(d for d in ("data", "measures")))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. Mount the data
# MAGIC The engine lowers every `[Resource]` retrieve to a call on a **table-valued function**,
# MAGIC `retrieve_<Type>(<8 explicit args>)` — the one mount contract. The arguments
# MAGIC arrive as compile-time literals (the value set, the date window) so a real binding can prune before
# MAGIC it reads; a mount that ignores them and returns everything is the correct un-pruned superset, which
# MAGIC the plan re-filters above the call.
# MAGIC
# MAGIC `mount` builds those functions over the files, on this Spark session only: nothing is written to a
# MAGIC catalog, and the mount ends with the session. Two things happen in it and nowhere else, and they are
# MAGIC the entire adapter:
# MAGIC
# MAGIC - **The context key is promoted to a top-level column** — `__subject_id`, the resource id for
# MAGIC   `Patient` and the trailing segment of `subject.reference` for everything else. It is the grouping
# MAGIC   key every set-at-a-time operation joins on, so it is computed once at the boundary rather than
# MAGIC   parsed per row in the hot path.
# MAGIC - **The resource is packed into one struct**, `__resource`, carrying `__resource_type` /
# MAGIC   `__resource_id` for export lineage.
# MAGIC
# MAGIC The read schema is **the compiler's, not inferred**: each resource is parsed against the struct the
# MAGIC compiler's FHIR model defines (`client.resource_schema("Condition")` shows it). A schema read off the
# MAGIC rows changes when the rows do, and an element absent from every row would silently disappear rather
# MAGIC than read as null. In a real deployment these are your governed FHIR tables, and the functions are
# MAGIC precreated Unity Catalog objects in a tagged data source — the CQL does not change.
# MAGIC
# MAGIC **Expected output:**
# MAGIC ```
# MAGIC Condition    16 rows -> retrieve_Condition__cqlmount
# MAGIC Encounter    29 rows -> retrieve_Encounter__cqlmount
# MAGIC Patient      20 rows -> retrieve_Patient__cqlmount
# MAGIC ```

# COMMAND ----------
from cql_databricks.compiler import mount

mounted = mount(spark, str(CONTENT / "data"))
for t, fn in sorted(mounted.items()):
    rows = spark.sql(f"SELECT count(*) AS n FROM `{fn}`()").first()["n"]
    print(f"{t:12} {rows} rows -> {fn}")

# COMMAND ----------
# MAGIC %md
# MAGIC ### Is the mount live?
# MAGIC Before running a measure over it, one `%%cql` cell asks the data a direct question. This is what
# MAGIC cells are for: self-contained CQL, no dependencies, answered immediately.
# MAGIC
# MAGIC **Expected output:** twenty rows, one per patient, each with true or false for whether they have any condition
# MAGIC on record — a direct check to verify the data is readable.

# COMMAND ----------
# MAGIC %%cql
# MAGIC library MountCheck version '1.0.0'
# MAGIC using FHIR version '4.0.1'
# MAGIC
# MAGIC context Patient
# MAGIC
# MAGIC define "Has A Condition": exists ([Condition])

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Mount the libraries — a folder of CQL as the library source
# MAGIC This is the piece that makes a *published* measure runnable rather than a pasted one.
# MAGIC
# MAGIC The sample measure `include`s one helper library, and the compile service resolves **none** of them
# MAGIC — by design. It is a dumb endpoint: the caller resolves the `include` closure and ships every
# MAGIC dependency; the service only merges what it is handed. That purity boundary
# MAGIC is why the service reads no filesystem, no registry and no network — and why the resolver lives here,
# MAGIC next to the files.
# MAGIC
# MAGIC `library_path` mounts one or more directories as the session's library source. Roots are **ordered
# MAGIC and earlier wins**, so an override directory shadows a vendored content repo:
# MAGIC
# MAGIC ```python
# MAGIC connect(spark, library_path=["/Workspace/Users/me/wip", "/Volumes/acme/content/ecqm"])
# MAGIC ```
# MAGIC
# MAGIC A client this notebook does not build itself — the `%%cql` magic builds its own — reads the same
# MAGIC folder from `CQL_DATABRICKS_LIBRARY_PATH` or `spark.cqldatabricks.libraryPath` instead.
# MAGIC
# MAGIC **Expected output:**
# MAGIC ```
# MAGIC libraries at /Workspace/…/examples/measures
# MAGIC   BasicEncounterAndCondition|1.0.0
# MAGIC   EncounterDuringPeriod|1.0.0
# MAGIC ```

# COMMAND ----------
from cql_databricks.compiler import connect

MEASURES = str(CONTENT / "measures")

client = connect(spark, library_path=MEASURES)
print("libraries at", MEASURES)
for ref in client.libraries("*"):
    print(" ", ref)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. Bind the measure by name, and read it before running it
# MAGIC `client.cql(library="BasicEncounterAndCondition")` addresses the measure by **name**. Resolving it walks
# MAGIC the `include` closure, so `EncounterDuringPeriod` is found and shipped with the compile without
# MAGIC being mentioned here.
# MAGIC
# MAGIC Two rules decide which source an `include` resolves to:
# MAGIC
# MAGIC - **Sibling-first.** An `include` resolves against the root's own directory before the wider search
# MAGIC   path — a content repo can ship a copy of its shared libraries *per measure*, so a flat scan sees
# MAGIC   several different copies of the same library.
# MAGIC - **Exact version.** An `include` that states a version resolves only to that version; a version
# MAGIC   nothing on the search path declares fails loud, naming the versions it did find.
# MAGIC
# MAGIC The handle is lazy and the compile is memoized, so inspecting and then running costs one round trip.
# MAGIC No data has been touched at this point.
# MAGIC
# MAGIC **Expected output:**
# MAGIC ```
# MAGIC populations : ['Initial Population']
# MAGIC defines     : ['Has Encounter During Period', 'Has Any Condition', 'Initial Population']
# MAGIC ```
# MAGIC `populations` finds `Initial Population` by its well-known name; it is what `exec()` runs when you name
# MAGIC no targets. The sample measure has no other population (it is deliberately minimal).
# MAGIC `Initial Population` is the one that matters: 16 of 20 patients true for the 2026-01-01 to 2026-12-31 period.

# COMMAND ----------
measure = client.cql(library="BasicEncounterAndCondition") \
                .with_measurement_period("2026-01-01", "2026-12-31")

print("populations :", measure.populations())
print("defines     :", measure.defines())

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5. Run it
# MAGIC `exec()` with the three defines named evaluates them together, compiling the shared graph
# MAGIC **once** — `"Has Encounter During Period"` and `"Has Any Condition"` both feed `"Initial Population"`, and
# MAGIC that shared work is computed once.
# MAGIC
# MAGIC Every define's result arrives with **identical schemas** (`__subject_id`, `__val`), because a define names the
# MAGIC object and never a column. The frames are presented side by side for each patient so you can
# MAGIC see the pattern: which patients meet each criterion, and which are in the initial population.
# MAGIC
# MAGIC **Expected output:** twenty rows, one per patient. 18 have an encounter during 2026, 16 have any condition,
# MAGIC and 16 meet both criteria (initial population). Four patients fail: pat-007 and pat-009 have encounters but no
# MAGIC conditions; pat-011 and pat-019 have no 2026 encounter at all.

# COMMAND ----------
from pyspark.sql import functions as F

from cql_databricks.compiler import VALUE_COLUMN

defines = measure.exec("Has Encounter During Period", "Has Any Condition", "Initial Population")
report = (defines["Has Encounter During Period"].withColumnRenamed(VALUE_COLUMN, "has_encounter")
          .join(defines["Has Any Condition"].withColumnRenamed(VALUE_COLUMN, "has_condition"), "__subject_id")
          .join(defines["Initial Population"].withColumnRenamed(VALUE_COLUMN, "initial_population"), "__subject_id")
          .orderBy("__subject_id"))
report.display()

# COMMAND ----------
# MAGIC %md
# MAGIC ## 6. Query the result as SQL
# MAGIC Every define also landed as a session temp view named after it, with spaces as underscores
# MAGIC (`Initial_Population`), so the headline number is one SQL cell away — no CQL, no Python, nothing to export.
# MAGIC
# MAGIC **Expected output:** one row — `total_patients = 20`, `in_initial_population = 16`.

# COMMAND ----------
# MAGIC %sql
# MAGIC SELECT (SELECT count(*) FROM Initial_Population) AS total_patients,
# MAGIC        (SELECT count_if(__val) FROM Initial_Population) AS in_initial_population

# COMMAND ----------
# MAGIC %md
# MAGIC ## 7. Ask a question the measure did not answer
# MAGIC A care-gap report always provokes a follow-up: *which* patients, and who is close to the threshold? The published
# MAGIC definitions in the helper library are addressable, so the follow-up can be asked of **the same library the measure uses**
# MAGIC rather than re-implemented in ad-hoc SQL — which is where consistency usually breaks, someone
# MAGIC rebuilding "has encounter" slightly differently and getting a number nobody can reconcile.
# MAGIC
# MAGIC The follow-up here asks the same helper library for encounters, over the same period. The sample
# MAGIC measure requires both an encounter *and* a condition, so this shows every patient with an encounter,
# MAGIC whether or not they have a condition.
# MAGIC
# MAGIC **Where an `include` is found.** A `%%cql` cell's `include` resolves against earlier cells, then the
# MAGIC folder in `spark.cqldatabricks.libraryPath`, then the knowledge tagged in your catalog scopes. This
# MAGIC notebook hands its folder to `connect()` only, so the magic's own client cannot see it; the follow-up
# MAGIC therefore lives in the folder and runs by name with `library=`. Set the conf to the folder and the
# MAGIC same question works as a `%%cql` cell.
# MAGIC
# MAGIC **Expected output:** twenty rows — 18 patients have an encounter during 2026 (true), 2 do not (false).
# MAGIC Patients pat-011 and pat-019 show false: they have no 2026 encounter, so they cannot be in any measure's
# MAGIC initial population for 2026.

# COMMAND ----------
followup = client.cql(library="EncounterDuringPeriod").with_measurement_period("2026-01-01", "2026-12-31")
followup.exec("Has Encounter During Period").orderBy("__subject_id").display()

# COMMAND ----------
# MAGIC %md
# MAGIC ## 8. The same measure, a different period
# MAGIC The measurement period is a `parameter`, and the handle is immutable-fluent — `with_measurement_period`
# MAGIC returns a NEW handle rather than rebinding this one. Nothing about the content changes; the question
# MAGIC is asked of a different year.
# MAGIC
# MAGIC Watch the population turn over completely. In 2025 only two patients have a qualifying encounter *and*
# MAGIC a condition: pat-003 and pat-006. The measure that reported 16 of 20 for 2026 reports 2 of 20 for 2025,
# MAGIC over a completely different cohort.
# MAGIC
# MAGIC That is worth sitting with: **a rate is meaningless without its period**, and the same content
# MAGIC honestly produces both numbers.
# MAGIC
# MAGIC A define is materialized as a session temp view named after it, so this run **rebinds**
# MAGIC `Initial_Population` to 2025 — the frames from section 5 are lazy over those same views.
# MAGIC That is why the numbers below are collected here and now, and why re-running section 5 after this
# MAGIC cell shows 2025 rows. (One session, one library, two parameterizations; `object_prefix` is the knob
# MAGIC for keeping several libraries' objects apart in one session.)
# MAGIC
# MAGIC **Expected output:**
# MAGIC ```
# MAGIC 2025: initial population = 2
# MAGIC ```

# COMMAND ----------
prior = measure.with_measurement_period("2025-01-01", "2025-12-31").exec()
initial_pop_2025 = prior["Initial Population"].collect()
count_2025 = sum(1 for r in initial_pop_2025 if r[VALUE_COLUMN])
print(f"2025: initial population = {count_2025}")

# COMMAND ----------
# MAGIC %md
# MAGIC ---
# MAGIC ### What just happened
# MAGIC A measure held as content in a folder, its dependencies resolved by name, its data read through one mount
# MAGIC contract — and a per-patient result, a SQL count, an ad-hoc follow-up asking the helper library the same
# MAGIC question, and the same measure re-parameterized to a different year, all producing honest numbers that
# MAGIC cannot disagree with each other. No application was involved.
# MAGIC
# MAGIC **04 · Compilation and inspection** takes a measure (different content) and never runs it at all: the define
# MAGIC graph, the ELM, and the Spark plan the compiler produces before any data is touched.

# COMMAND ----------
# Every cell above ran, so the notebook is green. There are no value assertions here by design: the
# narrative states each expected result, and a compile or runtime failure fails its own cell loudly
# rather than being collected into a summary at the end.
import json
dbutils.notebook.exit(json.dumps({"ok": True, "notebook": "03_clinical_measure"}))
