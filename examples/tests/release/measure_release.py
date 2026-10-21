# Databricks notebook source
# MAGIC %md
# MAGIC # Release check: a sample measure from the built wheels
# MAGIC `CLIENT-RELEASE-TESTS`. `examples/tests/test_release_live.py` submits this notebook as a one-time
# MAGIC serverless run for each sample measure in `examples/`, filling in the measure's name, version and model,
# MAGIC and passing its expected results per patient. It needs no folder of `.cql` files:
# MAGIC
# MAGIC 1. Install the client from the deployment's Volume.
# MAGIC 2. Discover by tag in the deployment's catalog scopes: the measure and its includes from tagged knowledge
# MAGIC    (`CLIENT-KNOWLEDGE-INCLUDES`, `CONTENT-SAMPLE-PRELOAD`), and the sample patients by their data source's
# MAGIC    name (`CLIENT-EXEC-DISCOVERY`, `DATA-SAMPLE-SOURCE`).
# MAGIC 3. Compute the measure durably into your private results schema (`RUN-CLIENT-DURABLE`).
# MAGIC 4. Compare each population with the expected results, per patient.
# MAGIC 5. Run it again from a new client and check every define is reused (`RUN-CLIENT-RESUME`).
# MAGIC 6. Run it into a scratch schema with your results schema as a result scope, and check every define is
# MAGIC    reused from there and nothing is written (`CLIENT-DISCOVERY-SCOPE`).
# MAGIC 7. Write a library in a `%%cql` cell that includes the measure and adds one define. Run it, and check every
# MAGIC    measure define is reused from step 3's tables and only the new define is computed
# MAGIC    (`CLIENT-INCLUDED-REUSE`, `EMIT-ROOT-INDEPENDENT`).

# COMMAND ----------
# MAGIC %pip install __CQL_DATABRICKS_ARTIFACT_VOLUME__/wheels/cql_databricks-*.whl

# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
# MAGIC %load_ext cql_databricks.compiler

# COMMAND ----------
import json

dbutils.widgets.text("catalog_scopes", "")
dbutils.widgets.text("results_catalog", "")
dbutils.widgets.text("expected_results", "")
SCOPES = dbutils.widgets.get("catalog_scopes").strip()
RESULTS_CATALOG = dbutils.widgets.get("results_catalog").strip()
EXPECTED_RESULTS = dbutils.widgets.get("expected_results").strip()
missing = [n for n, v in (("catalog_scopes", SCOPES), ("results_catalog", RESULTS_CATALOG),
                          ("expected_results", EXPECTED_RESULTS)) if not v]
if missing:
    raise ValueError(f"set the {missing} parameters (the test passes them)")

# CLIENT-DISCOVERY-SCOPE — the deployment's scopes, passed to connect(): serverless refuses a custom Spark conf key.
SCOPES = [entry.strip() for entry in SCOPES.split(",")]

# The measure's own expected results file from `examples/data/expected-results/` (the test passes it as JSON).
EXPECTED = json.loads(EXPECTED_RESULTS)
MEASURE = EXPECTED["measure"]
if MEASURE != "__MEASURE__":
    raise ValueError(f"the expected results are for {MEASURE}, but this notebook was filled in for __MEASURE__")
PERIOD = (EXPECTED["measurementPeriod"]["start"], EXPECTED["measurementPeriod"]["end"])
#: Population define -> its population code, as the expected results name them.
POPULATIONS = EXPECTED["populations"]
# DATA-SAMPLE-SOURCE — the sample patients every sample measure runs over.
DATA_SOURCE = "sample-patients"

# COMMAND ----------
from cql_databricks.compiler import connect, reset
from cql_databricks.data_bindings.runner import spark_runner
from cql_databricks.result import ensure_private_destination, materialize

reset(spark)
destination = ensure_private_destination(RESULTS_CATALOG, run=spark_runner(spark))


def query(library=MEASURE):
    """A new client each time and no folder: the library and its includes are discovered by tag."""
    return (connect(spark, catalog_scopes=SCOPES, data_source=DATA_SOURCE).cql(library=library)
            .with_measurement_period(*PERIOD))


first = materialize(query(), *POPULATIONS, spark=spark, destination=destination, mode="recompute")
computed = sorted(o.definition for o in first.outcomes if o.decision == "computed")
print(f"computed {len(computed)} of {len(first.outcomes)} defines into {'.'.join(destination.destination)}")
if len(computed) != len(first.outcomes):
    raise AssertionError(f"recompute reused results: {[o.definition for o in first.outcomes if o.decision != 'computed']}")

# COMMAND ----------
# Each population, per patient, against the expected value (1 true, 0 false or null).
patients = EXPECTED["patients"]
if not patients:
    raise AssertionError(f"no expected results for {MEASURE}")
mismatches = []
for define, code in POPULATIONS.items():
    members = {r["__subject_id"] for r in first.read(define).where("__val").select("__subject_id").collect()}
    unknown = members - set(patients)
    if unknown:
        mismatches.append(f"{define}: patients with no expected result: {sorted(unknown)}")
    for subject, values in sorted(patients.items()):
        got, want = int(subject in members), int(values[code])
        if got != want:
            mismatches.append(f"{subject} {define}: computed {got}, expected {want}")
print(f"{len(patients)} patients, {len(POPULATIONS)} populations, {len(mismatches)} mismatches")
if mismatches:
    raise AssertionError(f"{MEASURE} differs from its expected results:\n" + "\n".join(mismatches))

# COMMAND ----------
# RUN-CLIENT-RESUME — a new client and query, nothing kept from the run above: every define is reused.
second = materialize(query(), *POPULATIONS, spark=spark, destination=destination)
not_reused = [f"{o.definition}: {o.reason}" for o in second.outcomes if o.decision != "reused"]
if not_reused:
    raise AssertionError("the second run recomputed: " + "; ".join(not_reused))
print(f"reused {len(second.outcomes)} of {len(second.outcomes)} defines")

# COMMAND ----------
# CLIENT-DISCOVERY-SCOPE — another destination, with the results schema listed as a result scope: every define
# is reused from that scope, and the scratch schema stays empty.
import uuid

scratch = (RESULTS_CATALOG, f"cql_release_scope_{uuid.uuid4().hex[:12]}")
spark.sql(f"CREATE SCHEMA `{scratch[0]}`.`{scratch[1]}`")
try:
    third = materialize(query(), *POPULATIONS, spark=spark, destination=scratch,
                        catalog_scopes=[".".join(destination.destination)])
    elsewhere = [f"{o.definition}: {o.reason}" for o in third.outcomes
                 if o.decision != "reused" or o.result.artifact.table[:2] != destination.destination]
    if elsewhere:
        raise AssertionError("not reused from the results scope: " + "; ".join(elsewhere))
    written = spark.sql(f"SHOW TABLES IN `{scratch[0]}`.`{scratch[1]}`").count()
    if written:
        raise AssertionError(f"the scoped run wrote {written} tables into {'.'.join(scratch)}")
    print(f"reused {len(third.outcomes)} defines from {'.'.join(destination.destination)} into a new destination")
finally:
    spark.sql(f"DROP SCHEMA `{scratch[0]}`.`{scratch[1]}` CASCADE")

# COMMAND ----------
# MAGIC %md
# MAGIC CLIENT-INCLUDED-REUSE — a library that includes the measure and adds one define. Registered as a session
# MAGIC library (`SVC-NOTEBOOK-CELL-LIBRARY`); the measure resolves from tagged knowledge.

# COMMAND ----------
# MAGIC %%cql --library
# MAGIC library ReleaseCheck version '0.1.0'
# MAGIC __MODEL__
# MAGIC include FHIRHelpers version '4.0.1' called FHIRHelpers
# MAGIC include __MEASURE__ version '__VERSION__' called Measure
# MAGIC parameter "Measurement Period" Interval<DateTime>
# MAGIC context Patient
# MAGIC define "Release Check": Measure."__POPULATION__" is not null

# COMMAND ----------
# Every measure define is reused from the first run's tables; only ReleaseCheck's own define is computed.
fourth = materialize(query("ReleaseCheck"), "Release Check", spark=spark, destination=destination)
tables = {o.definition: o.result.artifact.table for o in first.outcomes}
origins = {d.key: json.loads(d.origin_json)["library"] for d in fourth.selection.definitions}
own = [o for o in fourth.outcomes if origins[o.definition] == "ReleaseCheck"]
included = [o for o in fourth.outcomes if origins[o.definition] != "ReleaseCheck"]
if [o.decision for o in own] != ["computed"]:
    raise AssertionError(f"ReleaseCheck's own define was not computed once: {[(o.definition, o.decision) for o in own]}")
not_reused = [f"{o.definition}: {o.decision}, {o.reason}" for o in included
              if o.decision != "reused" or tables.get(o.definition) != o.result.artifact.table]
if not included or not_reused:
    raise AssertionError(f"the including library did not reuse {MEASURE}'s tables: " + "; ".join(not_reused))
print(f"reused {len(included)} {MEASURE} defines from the first run; computed {len(own)} new define")

# COMMAND ----------
dbutils.notebook.exit(json.dumps({"ok": True, "notebook": "measure_release", "measure": MEASURE,
                                  "patients": len(patients), "computed": len(computed),
                                  "reused": len(second.outcomes), "reused_from_scope": len(third.outcomes),
                                  "included_reused": len(included)}))
