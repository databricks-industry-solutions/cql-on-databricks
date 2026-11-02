# Databricks notebook source
# MAGIC %md
# MAGIC # 4 · Compilation and inspection — DAG, ELM, explain
# MAGIC Notebook 3 ran a measure. This one never runs it at all.
# MAGIC
# MAGIC CQL compiles **once** into artifacts you can inspect before — and without — executing
# MAGIC anything: the neutral **define DAG**, the **ELM** interchange IR, and, at the end, the underlying
# MAGIC **Spark physical plan**. This is the surface an external pipeline or governance layer builds on, and
# MAGIC the reason a measure can be reviewed before it is trusted with data.
# MAGIC
# MAGIC All three are read off **one measure, the example measure notebook 3 runs** — and no row of clinical data
# MAGIC is read anywhere in this notebook. The physical plan needs the data *contract* rather than the data, so
# MAGIC each retrieve reads a **typed stub** and the plan is printed, never run.
# MAGIC
# MAGIC **Runs on serverless — no cluster to attach.** Just **Run All**.

# COMMAND ----------
# MAGIC %pip install __CQL_DATABRICKS_ARTIFACT_VOLUME__/wheels/cql_databricks-*.whl

# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
# MAGIC %load_ext cql_databricks.compiler

# COMMAND ----------
# Start clean, so Run All gives the same output however often it runs (CLIENT-SESSION-RESET).
from cql_databricks.compiler import reset

reset(spark)

# COMMAND ----------
# MAGIC %md
# MAGIC ## The measure under inspection
# MAGIC The example measure notebook 3 runs, `HypertensionBPControl`, read from the `measures/` folder published
# MAGIC beside this notebook. It includes `MeasureCommon`, which resolves from the same folder.
# MAGIC Nothing runs: the handle below is **lazy**, and when it compiles the result is memoized, so every section
# MAGIC shares one round trip. The measure has a default measurement period parameter, so the handle passes one.
# MAGIC
# MAGIC **Expected output:** `HypertensionBPControl` and `MeasureCommon`, each with its CQL size.

# COMMAND ----------
import pathlib

from cql_databricks.compiler import connect, translate

_nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
MEASURES = (pathlib.Path("/Workspace") / pathlib.PurePosixPath(_nb).parent.relative_to("/")
            / "measures")
if not MEASURES.is_dir():
    raise FileNotFoundError(f"no measures folder at {MEASURES} — it is published beside this notebook")

client = connect(spark, library_path=str(MEASURES))
SOURCES = {name: (MEASURES / f"{name}.cql").read_text() for name in ("HypertensionBPControl",
                                                                      "MeasureCommon")}
for name, cql in SOURCES.items():
    print(f"{name} — {len(cql)} chars of CQL")
q = client.cql(library="HypertensionBPControl").with_measurement_period("2026-01-01", "2026-12-31")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. The neutral define DAG (`dag()` / `describe()`)
# MAGIC What an orchestrator reads to build its own dataset graph *before* committing to any execution
# MAGIC engine: the defines, the dependency edges between them, the cost-chosen materialization boundaries,
# MAGIC the auto-detected population set, and the compiler version pin that makes a result reproducible.
# MAGIC
# MAGIC It is **neutral** in the sense that matters: nothing in it names Spark.
# MAGIC
# MAGIC **Expected output:**
# MAGIC ```
# MAGIC defines             : [... approximately 14 defines including population definitions,
# MAGIC                        hypertension checks and blood pressure criteria ...]
# MAGIC dependencies        : {'Initial Population': [...],
# MAGIC                        'Numerator': ['Most Recent Diastolic', 'Most Recent Systolic'],
# MAGIC                        'Denominator': ['Initial Population'], ...}
# MAGIC populations         : ['Initial Population', 'Denominator', 'Denominator Exclusions', 'Numerator']
# MAGIC materialized_defines: [...]   # the barriers the cost model chose
# MAGIC boundaries          : [...]
# MAGIC compiler_version    : '...'   # the pin the run is reproducible against
# MAGIC ```
# MAGIC The graph shows the hypertension measure's population defines at the top level, with blood pressure reads,
# MAGIC condition checks, and encounter eligibility flowing in. The included libraries' defines are named with their
# MAGIC library prefix: the includes are part of the graph.

# COMMAND ----------
graph = q.dag()
print("defines             :", graph.defines)
print("dependencies        :", graph.dependencies)
print("populations         :", q.populations())
print("materialized_defines:", graph.materialized_defines)
print("boundaries          :", graph.boundaries)
print("compiler_version    :", graph.compiler_version)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. ELM — the interchange IR
# MAGIC `translate()` returns the ELM JSON the engine's front end produces from CQL — HL7's standard
# MAGIC interchange form for CQL logic.
# MAGIC
# MAGIC The library translated here is `MeasureCommon`, one the measure includes. Its only include is
# MAGIC FHIRHelpers, which is content like any library, so its text is passed along with it.
# MAGIC
# MAGIC **Expected output:** a byte count of several thousand (the library has multiple functions and queries),
# MAGIC the statement names, and the first 600 characters of one define's expression tree — showing the `Query` and
# MAGIC filter logic over FHIR retrieves, which is the CQL you read, spelled out in ELM.
# MAGIC ```
# MAGIC ELM bytes  : ...
# MAGIC statements : ['Patient', 'Age At End Of Period', 'Qualifying Encounters', ...]
# MAGIC ```
# MAGIC (`Patient` is the implicit context statement the translator adds.)

# COMMAND ----------
import json
elm = translate(SOURCES["MeasureCommon"], includes=[(MEASURES / "FHIRHelpers.cql").read_text()])
elm_obj = json.loads(elm)
print("ELM bytes  :", len(elm))
print("statements :", [s["name"] for s in elm_obj["library"]["statements"]["def"]])
print("\nELM for 'Qualifying Encounters':")
encounters = next(s for s in elm_obj["library"]["statements"]["def"] if s["name"] == "Qualifying Encounters")
print(json.dumps(encounters["expression"], indent=2)[:600], "...")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. The Spark physical plan (`explain`) — of this measure
# MAGIC Under all of it, the compiled SQL is ordinary Spark, and the measure's OWN plan is readable without
# MAGIC data: a plan needs the data **contract**, not the rows. So each retrieve reads a **typed stub**: a
# MAGIC DataFrame on the contract every data view returns, `(__subject_id, __resource)`, its `__resource` the struct
# MAGIC the compiler's FHIR model defines (`client.resource_schema(type)`), with a single all-null row instead of a
# MAGIC clinical record. A `DataFrameDataResolver` hands them to a second client; value sets still come from the
# MAGIC environment's `knowledge.valueset` view.
# MAGIC
# MAGIC One row rather than none, because an empty relation is something the optimizer is entitled to fold
# MAGIC away — the plan would collapse to nothing and prove nothing. The row is a **plan placeholder** and is
# MAGIC never read: `explain` plans, it does not execute.
# MAGIC
# MAGIC **Expected output:** the stubbed types — the hypertension measure reads Patient, Encounter, Condition (for
# MAGIC hypertension and other diagnoses), Observation (for blood pressure readings), and Procedure (for
# MAGIC dialysis/transplant).
# MAGIC ```
# MAGIC stubs: ['Condition', 'Coverage', 'Encounter', 'Observation', 'Patient', 'Procedure']
# MAGIC ```

# COMMAND ----------
from pyspark.sql.types import StringType, StructField, StructType

from cql_databricks.resolvers import DataFrameDataResolver


def stub(fhir_type):
    """The (__subject_id, __resource) contract with a plan placeholder behind it: one all-null row
    carrying the compiler's struct for the type. Read from the model, never inferred: there are no rows to
    infer a schema from, which is the sharpest version of why the contract is written down."""
    contract = StructType([StructField("__subject_id", StringType()),
                           StructField("__resource", client.resource_schema(fhir_type).struct)])
    return spark.createDataFrame([(None, None)], contract)


STUBS = {t: stub(t) for t in ("Condition", "Coverage", "Encounter", "Observation", "Patient", "Procedure")}
planned = connect(spark, library_path=str(MEASURES), data=DataFrameDataResolver(STUBS, name="stubs"))
print("stubs:", sorted(STUBS))

# COMMAND ----------
# MAGIC %md
# MAGIC ### Read the plan
# MAGIC `exec()` runs the compiled script's statements — every one a `CREATE OR REPLACE TEMP VIEW`, including
# MAGIC the cost-chosen barriers — and hands back a **lazy** frame per population. Views are lazy, so this
# MAGIC defines the computation without performing it, and `.explain()` prints what Spark would do if asked.
# MAGIC
# MAGIC Every define's frame is independently `.explain()`-able, which is also how you read a materialized
# MAGIC barrier's full plan.
# MAGIC
# MAGIC **Expected output:** a `== Physical Plan ==` block for `Initial Population` — the `Encounter`, `Condition`,
# MAGIC and `Observation` retrieves with their filters through the `cql_*` UC functions, and the joins that flow
# MAGIC age, hypertension status, and encounter eligibility into the population. No private runtime anywhere in it,
# MAGIC and nothing your existing Spark tooling cannot read.

# COMMAND ----------
planned_q = planned.cql(library="HypertensionBPControl").with_measurement_period("2026-01-01", "2026-12-31")
pops = planned_q.exec()   # no targets -> the populations, one lazy frame each
pops["Initial Population"].explain(mode="formatted")

# COMMAND ----------
# MAGIC %md
# MAGIC ---
# MAGIC ### That is the tour
# MAGIC Three artifacts from one compile of one measure, none of them needing a row of data. Between them they
# MAGIC are what lets a measure be reviewed, scheduled, shared and audited as an asset rather than run as a
# MAGIC script.
# MAGIC
# MAGIC Back to **00 · Start here** for the index.

# COMMAND ----------
# Every cell above ran, so the notebook is green. There are no value assertions here by design: the
# narrative states each expected result, and a compile or runtime failure fails its own cell loudly
# rather than being collected into a summary at the end.
dbutils.notebook.exit(json.dumps({"ok": True, "notebook": "04_compilation_and_inspection"}))
