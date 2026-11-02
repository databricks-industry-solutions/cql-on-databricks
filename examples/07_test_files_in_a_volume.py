# Databricks notebook source
# MAGIC %md
# MAGIC # 7 · Test files in a Volume
# MAGIC Every other notebook reads its data from the environment: views in the `data` schema over governed tables.
# MAGIC While you test a measure, the data is often a folder of files instead — FHIR NDJSON exported from a test
# MAGIC server, and the value sets the measure names. This notebook runs CQL over such a folder in a Unity Catalog
# MAGIC **Volume**, without loading a table or publishing anything.
# MAGIC
# MAGIC `use_resolvers` tells this notebook's `cql()` calls and `%%cql` cells where to read:
# MAGIC
# MAGIC | Role | Resolver | Reads |
# MAGIC |---|---|---|
# MAGIC | Data | `VolumeDataResolver` | one `<Type>.ndjson` file per FHIR type, one resource per line |
# MAGIC | Terminology | `FolderTerminologyResolver` | FHIR `ValueSet` JSON files, each with its expansion |
# MAGIC | Knowledge | not set, so the environment's | the published libraries, `FHIRHelpers` and the example measures among them |
# MAGIC
# MAGIC The files are read with `read_files` at each run, so a file you change is read on the next cell. A run over
# MAGIC them keeps nothing.
# MAGIC
# MAGIC **Set the `volume` widget** to a folder in a Volume you can write, such as
# MAGIC `/Volumes/<catalog>/<schema>/<volume>/cql_test_files`. The first cell after the setup writes the example
# MAGIC files there; skip it if your own files are in place.
# MAGIC
# MAGIC **Runs on serverless — no cluster to attach.**

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

dbutils.widgets.text("volume", "", "Volume folder for the test files")
FOLDER = dbutils.widgets.get("volume").rstrip("/")
if not FOLDER.startswith("/Volumes/") or FOLDER.count("/") < 5:
    raise ValueError(f"set the volume widget to a folder in a Volume, /Volumes/<catalog>/<schema>/<volume>/<folder>; "
                     f"it is {FOLDER!r}")
print("test files:", FOLDER)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. Write the example files to the Volume
# MAGIC The example patients and value sets are published beside this notebook (`data/`, `valuesets/`). This cell
# MAGIC copies them into the folder in the shape the resolvers read:
# MAGIC
# MAGIC - `fhir/<Type>.ndjson` — the FHIR resources of one type, one per line, as a FHIR server's bulk export writes them
# MAGIC - `valuesets/<name>.json` — one FHIR `ValueSet` per file, its codes in `expansion.contains`. The engine expands
# MAGIC   nothing, so a value set with only a `compose` is refused, naming it
# MAGIC
# MAGIC **Expected output:** six FHIR files and twenty value sets.

# COMMAND ----------
import collections
import json
import pathlib
import shutil

_nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
EXAMPLES = pathlib.Path("/Workspace") / pathlib.PurePosixPath(_nb).parent.relative_to("/")
for d in ("data", "valuesets"):
    if not (EXAMPLES / d).is_dir():
        raise FileNotFoundError(f"no {d} folder at {EXAMPLES / d} — it is published beside this notebook")

fhir_dir, valueset_dir = pathlib.Path(FOLDER) / "fhir", pathlib.Path(FOLDER) / "valuesets"
fhir_dir.mkdir(parents=True, exist_ok=True)
valueset_dir.mkdir(parents=True, exist_ok=True)

for f in sorted((EXAMPLES / "data").glob("*.ndjson")):
    shutil.copyfile(f, fhir_dir / f.name)

# The example value sets are rows of (valueset_id, system, code, display); a FHIR ValueSet holds them as its expansion.
for f in sorted((EXAMPLES / "valuesets").glob("*.ndjson")):
    rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    codes = collections.defaultdict(list)
    for r in rows:
        codes[r["valueset_id"]].append({"system": r["system"], "code": r["code"], "display": r.get("display")})
    for url, contains in codes.items():
        value_set = {"resourceType": "ValueSet", "url": url, "name": f.stem, "status": "active",
                     "expansion": {"timestamp": "2026-01-01T00:00:00Z", "contains": contains}}
        (valueset_dir / f"{f.stem}.json").write_text(json.dumps(value_set, indent=1))

print("FHIR files:", sorted(p.name for p in fhir_dir.glob("*.ndjson")))
print("value sets:", len(list(valueset_dir.glob("*.json"))))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. Read the folder in this notebook's cells
# MAGIC One call, and every later `cql()` and `%%cql` cell reads its data and value sets from the folder. Nothing is
# MAGIC read yet: the next cell builds its client with these resolvers. `use_resolvers()` with nothing goes back to
# MAGIC the environment, and so does `reset(spark)`.
# MAGIC
# MAGIC **Expected output:** the six types the folder holds.

# COMMAND ----------
from cql_databricks.compiler import use_resolvers
from cql_databricks.resolvers import FolderTerminologyResolver, VolumeDataResolver

data = VolumeDataResolver(str(fhir_dir))
use_resolvers(data=data, terminology=FolderTerminologyResolver(str(valueset_dir)))
print("types:", sorted(data.types()))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. A `%%cql` cell over the files
# MAGIC A question asked straight of the test data: who has a colonoscopy on record? `[Procedure: "Colonoscopy"]`
# MAGIC reads `fhir/Procedure.ndjson` and the codes of `valuesets/colonoscopy.json`.
# MAGIC
# MAGIC **Expected output:** one row per patient, 4,000 rows. `HadColonoscopy` is `true` for 189 of them.

# COMMAND ----------
# MAGIC %%cql --df colonoscopy
# MAGIC using FHIR version '4.0.1'
# MAGIC
# MAGIC valueset "Colonoscopy": 'http://example.org/fhir/ValueSet/colonoscopy'
# MAGIC
# MAGIC context Patient
# MAGIC
# MAGIC define "HadColonoscopy": exists ([Procedure: "Colonoscopy"])

# COMMAND ----------
# MAGIC %md
# MAGIC **Expected output:** `189 of 4000 patients had a colonoscopy`.

# COMMAND ----------
had = colonoscopy.filter("`__val`").count()
print(f"{had} of {colonoscopy.count()} patients had a colonoscopy")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. A published measure over the files
# MAGIC The measure is the environment's: `ColorectalScreening`, with `MeasureCommon` and `FHIRHelpers`, as the deploy
# MAGIC imported them. Only its data and value sets come from the folder. This is how a measure is tested against
# MAGIC known cases before its results are trusted: the same CQL, the test patients.
# MAGIC
# MAGIC **Expected output:** the measure's populations, one row each, with the number of patients in it.

# COMMAND ----------
from cql_databricks.compiler import cql

populations = (cql(library="ColorectalScreening")
               .with_measurement_period("2026-01-01", "2026-12-31")
               .exec())
for name, frame in populations.items():
    print(f"{name:24} {frame.filter('`__val`').count()}")

# COMMAND ----------
# MAGIC %md
# MAGIC ---
# MAGIC ### Where next
# MAGIC Once the measure gives the numbers you expect, publish the data as views in an environment
# MAGIC (`publish_data_source`) and the value sets to its terminology store, and every user reads them by name.
# MAGIC Back to **00 · Start here** for the index.

# COMMAND ----------
# Every cell above ran, so the notebook is green. The narrative states each expected result; a compile or runtime
# failure fails its own cell.
use_resolvers()
dbutils.notebook.exit(json.dumps({"ok": True, "notebook": "07_test_files_in_a_volume"}))
