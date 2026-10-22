# Databricks notebook source
# MAGIC %md
# MAGIC # 1 · Getting started — the two front doors to CQL
# MAGIC This is the setup module. It installs the client, then shows the **two ways to run CQL** from a
# MAGIC notebook and how they hand results back to Python and SQL. Everything after this notebook — the
# MAGIC language tour, the clinical measure, the compile artifacts — uses one of these two doors.
# MAGIC
# MAGIC 1. **`%%cql` — the cell magic.** A cell whose first line is `%%cql` *is* CQL. No quoting,
# MAGIC    no Python wrapper, editor-width room to write real CQL. A cell with no `library` header joins
# MAGIC    the notebook's **session library**, so a later cell sees its defines, as a SQL cell sees a view.
# MAGIC 2. **`cql(...)` — the Python API.** A lazy query handle you can inspect, parameterize and reuse.
# MAGIC
# MAGIC They are the same execution path: the magic is a front door, not a second
# MAGIC engine. Use the magic when you are writing CQL; use the API when the CQL is a value your Python code
# MAGIC is manipulating.
# MAGIC
# MAGIC **Runs on serverless — nothing to attach, nothing to configure.** Just **Run All**. The one
# MAGIC prerequisite is a workspace where CQL on Databricks is deployed.

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. Install the client
# MAGIC One wheel from the CQL on Databricks runtime Volume: `cql_databricks`, the client — compile requests, result
# MAGIC frames, discovery, OAuth and the `%%cql` magic. The deploy writes your Volume's path into the published
# MAGIC copy of this notebook. Nothing is installed on the compute itself, so this works on serverless.

# COMMAND ----------
# MAGIC %pip install __CQL_DATABRICKS_ARTIFACT_VOLUME__/wheels/cql_databricks-*.whl

# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. Load the magic
# MAGIC One line. It registers `%%cql` on this notebook's IPython shell; everything else — the Compiler
# MAGIC Service endpoint, OAuth, the `cql_*` Unity Catalog function catalog — is discovered on first use.
# MAGIC
# MAGIC **Expected output:** nothing. `%load_ext` is silent on success; an error here means the client
# MAGIC wheels did not install.

# COMMAND ----------
# MAGIC %load_ext cql_databricks.compiler

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Your first CQL cell
# MAGIC The cell body is sent verbatim to the Compiler Service, and what it compiles runs on this Spark
# MAGIC session.
# MAGIC
# MAGIC **Expected output:** one table, one row.
# MAGIC
# MAGIC | `__subject_id` | `__val` |
# MAGIC |---|---|
# MAGIC | `*` | `42` |

# COMMAND ----------
# MAGIC %%cql
# MAGIC define "Answer": 6 * 7

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. The result shape — always the same two columns
# MAGIC Every define result carries the subject key `__subject_id` and the payload column `__val`.
# MAGIC **A define names the OBJECT — the view or table — never a column**, so you never have to guess
# MAGIC what your result column is called, and two defines always arrive with identical schemas.
# MAGIC
# MAGIC The define above declares no `context`, so it resolves to CQL's `Unfiltered` context: there is
# MAGIC no subject, `__subject_id` is the `'*'` seed, and the single row *is* the answer. Notebook 3 shows the same shape with one row per patient.
# MAGIC
# MAGIC A **list** is one value like any other, so a list-valued define is still one row per subject — the
# MAGIC whole list arrives as an array in `__val`. (A define whose result is a *stream of resources*, such as
# MAGIC a FHIR retrieve, lowers instead to one row per element plus an `__ordinal` column, because CQL lists
# MAGIC are ordered and the order has to be carried as data. You will see that shape in notebook 2.)
# MAGIC
# MAGIC ## 5. `--df` — hand the result to Python
# MAGIC `--df NAME` binds the result into the notebook namespace. After the next cell, `ages` is an ordinary
# MAGIC DataFrame: `.filter()`, `.join()`, `.write` — whatever you would do with any other frame. That
# MAGIC hand-off is the whole integration story, and it is what keeps CQL from being a walled garden.
# MAGIC
# MAGIC **Expected output:** the magic displays one row (`__subject_id = *`, `__val = [1990, 2010, 1975]`),
# MAGIC then the Python cell prints:
# MAGIC ```
# MAGIC DataFrame -> ['__subject_id', '__val']
# MAGIC BirthYears = [1990, 2010, 1975]
# MAGIC ```
# MAGIC Note the order is the order written, not sorted — CQL lists are ordered.

# COMMAND ----------
# MAGIC %%cql --df ages
# MAGIC define "BirthYears": { 1990, 2010, 1975 }

# COMMAND ----------
from cql_databricks.compiler import VALUE_COLUMN

print(type(ages).__name__, "->", ages.columns)
print("BirthYears =", ages.collect()[0][VALUE_COLUMN])

# COMMAND ----------
# MAGIC %md
# MAGIC ## 6. …and `%sql` can read it too
# MAGIC Every define also lands as a **session temp view named after the define**, so a plain SQL cell needs
# MAGIC nothing from CQL — it just queries the result. `Answer` from section 3 is still there; the value
# MAGIC inside it is, as always, the column `__val`.
# MAGIC
# MAGIC **Expected output:** one row — `subject = *`, `answer = 42`.

# COMMAND ----------
# MAGIC %sql
# MAGIC SELECT __subject_id AS subject, __val AS answer FROM Answer

# COMMAND ----------
# MAGIC %md
# MAGIC ## 7. `--target` — choose which defines to evaluate
# MAGIC With no `--target`, the magic runs the cell's auto-detected eCQM populations
# MAGIC if it has any, else **every** define the cell declares. `--target` names one explicitly and
# MAGIC repeats for several — useful for inspecting a supporting define rather than the headline one.
# MAGIC
# MAGIC With several targets, `--df` binds a `{define: DataFrame}` **dict** instead of a single frame.
# MAGIC
# MAGIC **Expected output:** two tables, headed `--- Screened ---` (`__val = 840`) and `--- Unscreened ---`
# MAGIC (`__val = 360`). `Eligible` is defined and used, but not requested, so it is not displayed. Then the
# MAGIC Python cell prints `dict_keys(['Screened', 'Unscreened'])`.

# COMMAND ----------
# MAGIC %%cql --df cohort --target Screened --target Unscreened
# MAGIC define "Eligible":   1200
# MAGIC define "Screened":   840
# MAGIC define "Unscreened": "Eligible" - "Screened"

# COMMAND ----------
print(cohort.keys())

# COMMAND ----------
# MAGIC %md
# MAGIC ## 8. One session library — later cells see earlier defines
# MAGIC Every cell so far had no `library` header, so each one joined the notebook's **session library**
# MAGIC (`SVC-NOTEBOOK-SESSION-SCOPE`). A later cell refers to an earlier define by name, with nothing to
# MAGIC import. Redefining a name **replaces** it, the way re-running a SQL cell's `CREATE OR REPLACE VIEW`
# MAGIC does, and every define that uses it follows.
# MAGIC
# MAGIC A cell shows only its own defines. Give a cell a `library` header when you want a separate,
# MAGIC includable library instead (notebook 5).
# MAGIC
# MAGIC **Expected output:** `Total` is `1200` (`Screened` + `Unscreened` from section 7). After `Eligible` is
# MAGIC redefined as `1500`, the cell shows `1500`, and the `%sql` cell reads `total = 1500`: `Unscreened`
# MAGIC is now `660`, so `Total` moved with it.

# COMMAND ----------
# MAGIC %%cql
# MAGIC define "Total": "Screened" + "Unscreened"

# COMMAND ----------
# MAGIC %%cql
# MAGIC define "Eligible": 1500

# COMMAND ----------
# MAGIC %sql
# MAGIC SELECT __val AS total FROM Total

# COMMAND ----------
# MAGIC %md
# MAGIC ## 9. The other front door — the Python API
# MAGIC `cql(source)` returns a **lazy query handle**. Nothing runs until you ask it for something, and the
# MAGIC compile is memoized — inspecting a library and then running it costs one round trip, not two.
# MAGIC
# MAGIC A string with no `library` header joins the same session library as the cells above. `cql()` checks
# MAGIC at once that the whole library still compiles. `exec()` with no targets runs only the string's own
# MAGIC defines, and `defines()` lists the whole library.
# MAGIC
# MAGIC This is the door to use when the CQL is a *value* your code holds: read from a file, resolved from a
# MAGIC registry, or looped over.
# MAGIC
# MAGIC **Expected output:**
# MAGIC ```
# MAGIC its defines : ['Answer2', 'Doubled']
# MAGIC library     : ['Answer', 'Answer2', 'BirthYears', 'Doubled', 'Eligible', 'Screened', 'Total', 'Unscreened']
# MAGIC populations : []
# MAGIC Doubled     = 84
# MAGIC ```
# MAGIC `populations` is empty because this library has no well-known population define, such as
# MAGIC `Initial Population`. Notebook 3 shows one that does.

# COMMAND ----------
from cql_databricks.compiler import cql

q = cql('''
define "Answer2": 6 * 7
define "Doubled": "Answer2" * 2
''', spark=spark)

print("its defines :", q.default_targets())
print("library     :", sorted(q.defines()))
print("populations :", q.populations())
print("Doubled     =", q.exec("Doubled").collect()[0][VALUE_COLUMN])

# COMMAND ----------
# MAGIC %md
# MAGIC ## 10. Parameters — the same handle, specialized
# MAGIC A CQL `parameter` with a `default` is the mechanism every measure uses for its measurement period.
# MAGIC The handle is **immutable-fluent**: `with_parameter` returns a NEW handle, so one bound library can
# MAGIC be reused and specialized rather than rebound.
# MAGIC
# MAGIC An override is CQL *expression text*, so a Python string passes through verbatim as CQL — which is
# MAGIC how a date, interval or quantity literal is bound (`"@2026-01-01"`, `"40 'mg'"`).
# MAGIC `with_measurement_period("2026-01-01", "2026-12-31")` is the shorthand for the ubiquitous one, and it
# MAGIC widens both bounds to instants so they compare at the precision real resource
# MAGIC timestamps carry — a detail notebook 2 shows the teeth of.
# MAGIC
# MAGIC **Expected output:**
# MAGIC ```
# MAGIC default threshold (10) : True
# MAGIC bound threshold  (100) : False
# MAGIC ```

# COMMAND ----------
scored = cql('''
parameter "Threshold" default 10
define "OverThreshold": 42 > "Threshold"
''', spark=spark)

print("default threshold (10) :", scored.exec("OverThreshold").collect()[0][VALUE_COLUMN])
print("bound threshold  (100) :",
      scored.with_parameter("Threshold", 100).exec("OverThreshold").collect()[0][VALUE_COLUMN])

# COMMAND ----------
# MAGIC %md
# MAGIC ## 11. Libraries from a folder — the Git Folder setup
# MAGIC Everything so far ran CQL you handed over as *text*. A real measure is not text you paste: it is a
# MAGIC library that `include`s one or more others, published and versioned in a repo.
# MAGIC
# MAGIC The compile service resolves **none** of those includes — deliberately. It is a dumb endpoint: the
# MAGIC caller resolves the closure and ships every dependency, and the service only merges what it is
# MAGIC handed. It reads no filesystem, no registry and no network. So the resolver
# MAGIC lives on the client, next to the files.
# MAGIC
# MAGIC `library_path` mounts one or more directories of `.cql` as the session's library source, and a
# MAGIC measure in them runs **by name**. In a real deployment that
# MAGIC directory is a **Databricks Git Folder** — your measures repo, synced by the platform — or a Unity
# MAGIC Catalog Volume. It is an ordinary directory either way, so there is no platform code involved:
# MAGIC
# MAGIC ```python
# MAGIC client = connect(spark, library_path="/Workspace/Users/me/my-measures/cql")
# MAGIC ```
# MAGIC
# MAGIC Roots are **ordered and earlier wins**, so a work-in-progress directory shadows a vendored content
# MAGIC repo: `library_path=["/Workspace/Users/me/wip", "/Volumes/acme/content/ecqm"]`.
# MAGIC
# MAGIC Here we point it at the `measures/` folder published beside this notebook — the content notebook 3
# MAGIC runs for real. The path is derived from this notebook's own location, so the cell works unchanged
# MAGIC wherever the folder was copied to.
# MAGIC
# MAGIC **Expected output:**
# MAGIC ```
# MAGIC library_path: /Workspace/…/examples/measures
# MAGIC   FHIRHelpers|4.0.1
# MAGIC   HypertensionBPControl|1.0.0
# MAGIC   SampleMeasureCommon|1.0.0
# MAGIC   SampleSupplementalData|1.0.0
# MAGIC populations of HypertensionBPControl: ['Initial Population', 'Denominator', 'Denominator Exclusions', 'Numerator']
# MAGIC ```
# MAGIC That last line is the whole feature. Binding by name resolved the measure, walked its `include`
# MAGIC closure to find the helper libraries, and shipped all of them to the compiler — and **no data was
# MAGIC touched**, because resolving and compiling a measure is not the same act as running it.

# COMMAND ----------
import pathlib

from cql_databricks.compiler import connect

_nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
MEASURES = (pathlib.Path("/Workspace") / pathlib.PurePosixPath(_nb).parent.relative_to("/")
            / "measures")
if not MEASURES.is_dir():
    raise FileNotFoundError(f"no measures folder at {MEASURES} — it is published beside this notebook")

client = connect(spark, library_path=str(MEASURES))
print("library_path:", MEASURES)
for ref in client.libraries("*"):
    print(" ", ref)

measure = client.cql(library="HypertensionBPControl").with_measurement_period("2026-01-01", "2026-12-31")
print("populations of HypertensionBPControl:", measure.populations())

# COMMAND ----------
# MAGIC %md
# MAGIC ## 12. What did *not* happen
# MAGIC No endpoint. No token. No cluster. No `%scala` cell. No catalog or schema named anywhere in this
# MAGIC notebook. A single deployment matching the naming convention resolves itself:
# MAGIC
# MAGIC | What | How it was resolved |
# MAGIC |---|---|
# MAGIC | Compiler Service endpoint | looked up in the workspace App registry by name |
# MAGIC | Authentication | your ambient identity, exchanged for an App token (RFC 8693) |
# MAGIC | The `cql_*` operator catalog | read out of `system.information_schema.routines` |
# MAGIC | FHIR data / value sets | the data source tagged in Unity Catalog, and the `valuesets` view beside the `cql_*` catalog — looked up only when the CQL retrieves data |
# MAGIC
# MAGIC Each is overridable (`connect(app_name=…, data_source=…, data_catalog=…, valueset_table=…)`), or
# MAGIC replaced for a session with `mount()` (notebook 3) — but you have to *want* to.
# MAGIC
# MAGIC The library source is the one thing you *do* name, and that is deliberate: which measures you mean
# MAGIC is a decision, not a convention.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Where next
# MAGIC - **02 · CQL language tour** — what the language can express: null handling, precision dates, UCUM
# MAGIC   units, timing relations, equality vs equivalence, terminology filtering, polymorphic choice types.
# MAGIC - **03 · A clinical quality measure** — that same folder run for real, over FHIR data, producing a
# MAGIC   care-gap report and a rate.
# MAGIC - **04 · Compilation and inspection** — the artifacts a compile produces before anything runs.

# COMMAND ----------
# Every cell above ran, so the notebook is green. There are no value assertions here by design: the
# narrative states each expected result, and a compile or runtime failure fails its own cell loudly
# rather than being collected into a summary at the end.
import json
dbutils.notebook.exit(json.dumps({"ok": True, "notebook": "01_getting_started"}))
