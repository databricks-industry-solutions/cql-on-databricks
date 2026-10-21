# Databricks notebook source
# MAGIC %md
# MAGIC # 5 · Authoring a measure in the notebook
# MAGIC Notebook 3 ran a **published** measure: two `.cql` files in a folder, resolved by name, with nothing
# MAGIC authored on the page. This is the other half — **drafting** one. The measure below exists only in
# MAGIC this notebook: a cell of shared logic about patient visits, and a second cell that `include`s it to
# MAGIC define an initial population.
# MAGIC
# MAGIC That is how a measure is actually written. You do not start with a content repo; you start with a
# MAGIC question, split the parts that other measures will want to share into their own library, and publish
# MAGIC once the numbers hold up. Until now the only way to split logic across cells was to publish it to a
# MAGIC folder first, which is a strange thing to have to do to a draft.
# MAGIC
# MAGIC **A `%%cql` cell that declares a `library` header is a library.** A later cell can `include` it by
# MAGIC name, exactly as it would include something out of a Git Folder. The last section publishes the draft
# MAGIC to a folder and runs it from there unchanged, which is the whole point of drafting this way.
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
# library a `%%cql` cell declared (CLIENT-SESSION-RESET). This notebook redefines a library in place, so
# a stale draft from an earlier run would otherwise answer the first include.
from cql_databricks.compiler import reset

reset(spark)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. The data beside this notebook
# MAGIC Just one directory — **no `measures/`** — that is the difference from notebook 3. The logic comes
# MAGIC from the cells below; only the data is read from disk:
# MAGIC
# MAGIC ```
# MAGIC data/         FHIR NDJSON, one file per resource type
# MAGIC ```
# MAGIC
# MAGIC It is the same twenty-patient dataset notebook 3 uses, so the numbers at the end are comparable
# MAGIC to the ones there.
# MAGIC
# MAGIC **Expected output:** the resolved folder, then the data directory — for example
# MAGIC ```
# MAGIC content : /Workspace/Shared/CqlDatabricks/examples
# MAGIC found   : ['data']
# MAGIC ```

# COMMAND ----------
import pathlib

_nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
CONTENT = pathlib.Path("/Workspace") / pathlib.PurePosixPath(_nb).parent.relative_to("/")

# Fail loud with the path rather than later with a confusing empty result.
missing = [d for d in ("data",) if not (CONTENT / d).is_dir()]
if missing:
    raise FileNotFoundError(
        f"{missing} not found beside this notebook in {CONTENT} — they are published alongside it; if you "
        f"copied the notebook on its own, copy them too, or point CONTENT at your own content directory")

print("content :", CONTENT)
print("found   :", sorted(d for d in ("data",)))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. Mount the data
# MAGIC Unchanged from notebook 3, and deliberately so: every `[Resource]` retrieve lowers to a
# MAGIC `retrieve_<Type>` table-valued function you bind to your own tables. Authoring the logic in cells
# MAGIC changes nothing about how data reaches it — that is the seam holding still while everything above it moves.
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
# MAGIC ## 3. Connect — with nothing mounted as a library source
# MAGIC No `library_path` this time. There is no folder of CQL to point at, because the libraries are about
# MAGIC to be written in the next two cells.
# MAGIC
# MAGIC **Expected output:** `libraries declared so far: []` — an empty session, which is the honest state
# MAGIC of a notebook before anything has been authored.

# COMMAND ----------
from cql_databricks.compiler import connect

client = connect(spark)
print("libraries declared so far:", [str(r) for r in client.library_provider.declared()])

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. The shared library — `--library` declares without running
# MAGIC A helper library first: what does it mean for a patient to have visits during the measurement period?
# MAGIC It goes in its own library because "had an encounter" is not specific to one measure — the next measure
# MAGIC you write wants the same definition to mean the same thing.
# MAGIC
# MAGIC `--library` registers the cell and stops. There is nothing to run here: evaluating the define on its
# MAGIC own would compile a query, hit the data and produce a column nobody asked for. The cell is a
# MAGIC definition, not a question.
# MAGIC
# MAGIC **Expected output:** `declared NotebookVisits|1.0.0 — includable by later cells`

# COMMAND ----------
# MAGIC %%cql --library
# MAGIC library NotebookVisits version '1.0.0'
# MAGIC
# MAGIC using FHIR version '4.0.1'
# MAGIC
# MAGIC include FHIRHelpers version '4.0.1' called FHIRHelpers
# MAGIC
# MAGIC parameter "Measurement Period" Interval<DateTime>
# MAGIC   default Interval[@2026-01-01T00:00:00.000, @2026-12-31T23:59:59.999]
# MAGIC
# MAGIC context Patient
# MAGIC
# MAGIC define "Has Visit During Period":
# MAGIC   exists ([Encounter] E
# MAGIC     where E.period during "Measurement Period")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5. The measure — a second cell that `include`s the first
# MAGIC `include NotebookVisits` resolves against **this session**. The cell above is the library it
# MAGIC finds, and its text ships with the compile as a dependency, exactly as a file out of a Git Folder
# MAGIC would. Nothing was written to disk in between.
# MAGIC
# MAGIC This cell has no `--library`, so it runs. It defines `Initial Population` and other checks, which
# MAGIC will be executed when you call `exec()`.
# MAGIC
# MAGIC **Expected output:** one frame of twenty rows, one per patient. `Initial Population` is true for 16 of 20.

# COMMAND ----------
# MAGIC %%cql
# MAGIC library NotebookMeasure version '1.0.0'
# MAGIC
# MAGIC using FHIR version '4.0.1'
# MAGIC
# MAGIC include FHIRHelpers version '4.0.1' called FHIRHelpers
# MAGIC include NotebookVisits version '1.0.0' called Visits
# MAGIC
# MAGIC parameter "Measurement Period" Interval<DateTime>
# MAGIC   default Interval[@2026-01-01T00:00:00.000, @2026-12-31T23:59:59.999]
# MAGIC
# MAGIC context Patient
# MAGIC
# MAGIC define "Has Visit":
# MAGIC   Visits."Has Visit During Period"
# MAGIC
# MAGIC define "Has Any Condition":
# MAGIC   exists ([Condition])
# MAGIC
# MAGIC define "Initial Population":
# MAGIC   "Has Visit" and "Has Any Condition"

# COMMAND ----------
# MAGIC %md
# MAGIC ## 6. The draft is addressable by name
# MAGIC Both cells are libraries the session can list and bind, so the Python API reaches them the same way
# MAGIC it reaches published content — including the parts the magic has no syntax for, like binding the
# MAGIC measurement period to a different year.
# MAGIC
# MAGIC Watch the population turn over: in 2025 only two patients (pat-003 and pat-006) have both a visit and
# MAGIC a condition. The measure that reports 16 of 20 for 2026 reports 2 of 20 for 2025, over a completely
# MAGIC different cohort — the same content, honestly producing both numbers.
# MAGIC
# MAGIC **One thing to know before you draft a second measure in the same notebook.** Every define is
# MAGIC materialized as a session temp view named after it, so two measures that both define `Initial Population`
# MAGIC overwrite each other's view and the older frame quietly starts returning the newer measure's rows.
# MAGIC Set `spark.conf.set("spark.cqldatabricks.objectPrefix", "auto")` before connecting to keep each
# MAGIC library's objects apart — worth doing the moment a session holds more than one measure, which is
# MAGIC exactly what authoring in a notebook invites.
# MAGIC
# MAGIC **Expected output:**
# MAGIC ```
# MAGIC declared: ['NotebookVisits|1.0.0', 'NotebookMeasure|1.0.0']
# MAGIC 2026: initial_population=16
# MAGIC 2025: initial_population=2
# MAGIC ```

# COMMAND ----------
from cql_databricks.compiler import VALUE_COLUMN

print("declared:", [str(r) for r in client.libraries()])

measure = client.cql(library="NotebookMeasure")

for year in (2026, 2025):
    pops = measure.with_measurement_period(f"{year}-01-01", f"{year}-12-31").exec()
    initial_pop = pops["Initial Population"].collect()
    count = sum(1 for r in initial_pop if r[VALUE_COLUMN])
    print(f"{year}: initial_population={count}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 7. Editing a definition
# MAGIC Re-running a cell **replaces** the library it declared. Make a simple edit to the visits library and
# MAGIC the measure that includes it answers differently on the next run — no re-import, no restart, no publish step.
# MAGIC
# MAGIC That is also the one hazard worth naming out loud: **the session is the source of truth here, not the
# MAGIC page.** A cell you edited but never re-ran is not the definition the next compile sees. If a number
# MAGIC ever surprises you mid-draft, re-run the library cell before believing it.
# MAGIC
# MAGIC The edit below widens the visits check to count a visit up to a year after the period ends. Run for 2025,
# MAGIC the measure counted 2 patients in section 6 (pat-003 and pat-006, the two with a 2025 visit and a
# MAGIC condition). With the edit it also counts the 2026 visits, and every patient with a condition had one.
# MAGIC
# MAGIC **Expected output:** `widened_2025: initial_population=16`

# COMMAND ----------
# The same library, redeclared from Python rather than a cell: `register_cell` is what `%%cql` calls, so a
# generated or parameterized variant of a definition is a first-class way to declare one.
from pyspark.sql import functions as F

from cql_databricks.compiler import register_cell

draft = client.library_provider.source("NotebookVisits").cql
# Edit: count a visit up to a year after the period ends. Fail loud if the text to edit is not there.
ORIGINAL = 'E.period during "Measurement Period"'
WIDENED = 'E.period during Interval[start of "Measurement Period", end of "Measurement Period" + 1 year]'
if ORIGINAL not in draft:
    raise ValueError(f"NotebookVisits no longer contains {ORIGINAL!r}; update this edit")
register_cell(draft.replace(ORIGINAL, WIDENED))

edited_measure = client.cql(library="NotebookMeasure").with_measurement_period("2025-01-01", "2025-12-31")
initial_pop = edited_measure.exec("Initial Population").collect()
count = sum(1 for r in initial_pop if r[VALUE_COLUMN])
print("widened_2025: initial_population=", count, sep="")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 8. Publishing the draft
# MAGIC A published library is the same text in a file. Writing the two cells out and mounting that directory
# MAGIC gives exactly what notebook 3 ran — and the measure resolves by name from the folder with no change
# MAGIC to a single line of CQL.
# MAGIC
# MAGIC In a real workflow the directory is your measures repo as a **Databricks Git Folder**, and publishing
# MAGIC is a commit. The point here is only that nothing about the draft was notebook-shaped: the cell was
# MAGIC always the file.
# MAGIC
# MAGIC **You publish the draft you have.** The previous section left `NotebookVisits` sitting with the
# MAGIC widened-window edit, and writing the session out now would publish exactly that — so the experiment
# MAGIC is put back first. Worth doing deliberately rather than discovering it in a file: the notebook holds
# MAGIC one current definition per name, not a history you can pick a version out of.
# MAGIC
# MAGIC Cells still resolve ahead of the folder, so `clear()` is what hands the names back to the published
# MAGIC copies once both exist.
# MAGIC
# MAGIC The reverse never happens. A measure resolved **from the folder** has its whole `include` closure
# MAGIC resolved there too, so a same-named cell cannot change what a published measure means — overriding is
# MAGIC something you opt into by writing the measure as a cell, never something that happens to a measure you
# MAGIC merely ran. A notebook that could quietly redefine published content would make every number out of it
# MAGIC impossible to cite.
# MAGIC
# MAGIC **Expected output:**
# MAGIC ```
# MAGIC published: ['NotebookMeasure-1.0.0.cql', 'NotebookVisits-1.0.0.cql']
# MAGIC from the folder: initial_population=16
# MAGIC ```

# COMMAND ----------
import tempfile

# Put the original NotebookVisits definition back; the widened window was an experiment.
register_cell(client.library_provider.source("NotebookVisits").cql.replace(WIDENED, ORIGINAL))

published = pathlib.Path(tempfile.mkdtemp(prefix="cql_databricks-draft-"))
for ref in client.library_provider.declared():
    (published / f"{ref.name}-{ref.version}.cql").write_text(
        client.library_provider.source(ref.name).cql)
print("published:", sorted(p.name for p in published.glob("*.cql")))

# Mount the folder, then drop the drafts so the published copies are what the names resolve to.
from_folder = connect(spark, library_path=str(published))
from_folder.library_provider.clear()

pops = from_folder.cql(library="NotebookMeasure").exec("Initial Population")
print(f"from the folder: initial_population={pops.filter(F.col(VALUE_COLUMN)).count()}")

# COMMAND ----------
# MAGIC %md
# MAGIC ---
# MAGIC ### What just happened
# MAGIC A helper library written in one cell, a measure that includes it in a second cell, both run over FHIR data,
# MAGIC re-parameterized to a second year, one of them edited in place and re-run to see the change, and finally
# MAGIC both written to a folder where they run by name unchanged. No registry, no application, and no publish
# MAGIC step until the last section — which is the order you actually want: publish what already works.
# MAGIC
# MAGIC **03 · A clinical quality measure** is the same shape from the other end: content that already exists,
# MAGIC run from a folder with nothing authored on the page.

# COMMAND ----------
import json
dbutils.notebook.exit(json.dumps({"ok": True, "notebook": "05_authoring_in_the_notebook"}))
