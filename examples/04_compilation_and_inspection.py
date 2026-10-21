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
# MAGIC All three are read off **one measure, the sample measure notebook 3 runs** — and no row of clinical data
# MAGIC is read anywhere in this notebook. The physical plan needs the data *contract* rather than the data, so
# MAGIC the retrieve functions are mounted as **typed stubs** and the plan is printed, never run.
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
# MAGIC The sample measure notebook 3 runs, `BasicEncounterAndCondition`, read from the `measures/` folder published
# MAGIC beside this notebook. It includes `EncounterDuringPeriod`, which resolves from the same folder. Nothing runs:
# MAGIC the handle below is **lazy**, and when it compiles the result is memoized, so every section shares one round
# MAGIC trip. The measure has no default measurement period, so the handle passes one.
# MAGIC
# MAGIC **Expected output:** `BasicEncounterAndCondition` and `EncounterDuringPeriod`, each with its CQL size.

# COMMAND ----------
import pathlib

from cql_databricks.compiler import connect, translate

_nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
MEASURES = (pathlib.Path("/Workspace") / pathlib.PurePosixPath(_nb).parent.relative_to("/")
            / "measures")
if not MEASURES.is_dir():
    raise FileNotFoundError(f"no measures folder at {MEASURES} — it is published beside this notebook")

client = connect(spark, library_path=str(MEASURES))
SOURCES = {name: (MEASURES / f"{name}.cql").read_text() for name in ("BasicEncounterAndCondition",
                                                                      "EncounterDuringPeriod")}
for name, cql in SOURCES.items():
    print(f"{name} — {len(cql)} chars of CQL")
q = client.cql(library="BasicEncounterAndCondition").with_measurement_period("2026-01-01", "2026-12-31")

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
# MAGIC defines             : ['EncounterDuringPeriod.Has Encounter During Period', 'Has Any Condition',
# MAGIC                        'Has Encounter During Period', 'Initial Population']
# MAGIC dependencies        : {'Has Encounter During Period': ['EncounterDuringPeriod.Has Encounter During Period'],
# MAGIC                        'Initial Population': ['Has Encounter During Period', 'Has Any Condition'], ...}
# MAGIC populations         : ['Initial Population']
# MAGIC materialized_defines: [...]   # the barriers the cost model chose
# MAGIC boundaries          : [...]
# MAGIC compiler_version    : '...'   # the pin the run is reproducible against
# MAGIC ```
# MAGIC The edges to read are `Initial Population -> [Has Encounter During Period, Has Any Condition]` and, behind
# MAGIC the first, the included library's own define, named with its library: the include is part of the graph.

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
# MAGIC The library translated here is `EncounterDuringPeriod`, the one the measure includes: it needs no library
# MAGIC but FHIRHelpers, which the compiler supplies, so its text translates on its own.
# MAGIC
# MAGIC **Expected output:** a byte count of a few thousand, the statement names, and the first 600 characters of
# MAGIC `Has Encounter During Period`'s expression tree — an `Exists` over a `Query` whose source is the
# MAGIC `Encounter` retrieve, which is the CQL you read, spelled out.
# MAGIC ```
# MAGIC ELM bytes  : ...
# MAGIC statements : ['Patient', 'Has Encounter During Period']
# MAGIC ```
# MAGIC (`Patient` is the implicit context statement the translator adds.)

# COMMAND ----------
import json
elm = translate(SOURCES["EncounterDuringPeriod"])
elm_obj = json.loads(elm)
print("ELM bytes  :", len(elm))
print("statements :", [s["name"] for s in elm_obj["library"]["statements"]["def"]])
print("\nELM for 'Has Encounter During Period':")
during = next(s for s in elm_obj["library"]["statements"]["def"] if s["name"] == "Has Encounter During Period")
print(json.dumps(during["expression"], indent=2)[:600], "...")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. The Spark physical plan (`explain`) — of this measure
# MAGIC Under all of it, the compiled SQL is ordinary Spark, and the measure's OWN plan is readable without
# MAGIC data: a plan needs the data **contract**, not the rows. So the retrieve functions are mounted as
# MAGIC **typed stubs** — the same `retrieve_<Type>(<8 args>)` contract notebook 3 mounts, over the struct
# MAGIC the compiler's FHIR model defines (`client.resource_schema(type)`), with a single all-null row behind
# MAGIC each one instead of a clinical record.
# MAGIC
# MAGIC One row rather than none, because an empty relation is something the optimizer is entitled to fold
# MAGIC away — the plan would collapse to nothing and prove nothing. The row is a **plan placeholder** and is
# MAGIC never read: `explain` plans, it does not execute.
# MAGIC
# MAGIC **Expected output:** the three stubbed functions, one per type the measure reads.
# MAGIC ```
# MAGIC Condition    -> retrieve_Condition__cqlmount
# MAGIC Encounter    -> retrieve_Encounter__cqlmount
# MAGIC Patient      -> retrieve_Patient__cqlmount
# MAGIC ```

# COMMAND ----------
from pyspark.sql.types import StringType, StructField, StructType

from cql_databricks.compiler import mount


def stub(fhir_type):
    """The (__subject_id, __resource) mount contract with a plan placeholder behind it: one all-null row
    carrying the compiler's struct for the type. Read from the model, never inferred: there are no rows to
    infer a schema from, which is the sharpest version of why the contract is written down."""
    contract = StructType([StructField("__subject_id", StringType()),
                           StructField("__resource", client.resource_schema(fhir_type).struct)])
    return spark.createDataFrame([(None, None)], contract)


# The sample measure filters on no value set, so no expansion table is mounted.
mounted = mount(spark, {t: stub(t) for t in ("Condition", "Encounter", "Patient")})
for t, fn in sorted(mounted.items()):
    print(f"{t:12} -> {fn}")

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
# MAGIC **Expected output:** a `== Physical Plan ==` block for `Initial Population` — the `Encounter` retrieve
# MAGIC and its period comparison through the `cql_*` UC functions, the `Condition` retrieve, and the join that
# MAGIC brings the two defines together. No private runtime anywhere in it, and nothing your existing Spark
# MAGIC tooling cannot read.

# COMMAND ----------
pops = q.exec()   # no targets -> the populations, one lazy frame each
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
