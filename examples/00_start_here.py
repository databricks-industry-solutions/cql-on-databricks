# Databricks notebook source
# MAGIC %md
# MAGIC <img src="./start-here-hero.png" width="820" alt="CQL on Databricks example notebooks — Start here">
# MAGIC
# MAGIC **CQL on Databricks** compiles CQL — HL7's Clinical Quality Language — to run natively on Spark.
# MAGIC The modules below take it from a one-line expression to a quality measure over FHIR data. Each
# MAGIC is self-contained and runs on serverless with nothing to attach or install, so **Run All** is the
# MAGIC whole instruction.
# MAGIC
# MAGIC **Before you start.** Open a module, hit **Run All**, then read it top to bottom. Every section says
# MAGIC what it is about to show and **exactly what the next cell should print**, so a module is worth reading
# MAGIC even before you run it — and worth comparing against once you have. Everything runs in your own
# MAGIC session against temporary views: nothing is written to a catalog, and no state is left behind. The one
# MAGIC prerequisite is that CQL on Databricks is deployed in this workspace — the client libraries, the `cql_*` functions
# MAGIC in Unity Catalog, and the Compiler Service.
# MAGIC
# MAGIC **Start with 01.** The rest assume you have seen it.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### 01 · Getting started
# MAGIC *Setup · the two front doors · 10 minutes*
# MAGIC
# MAGIC Install the client, then learn the two ways to run CQL from a notebook and how each hands its results
# MAGIC back to Python and SQL. Everything in the other modules uses one of them.
# MAGIC
# MAGIC - `%%cql` — a cell whose first line is `%%cql` *is* CQL: no quoting, no Python wrapper
# MAGIC - Cells with no `library` header share one session library: a later cell sees earlier defines, and
# MAGIC   redefining a name replaces it
# MAGIC - `--df NAME` binds the result into the notebook as an ordinary DataFrame — or a dict of them, one
# MAGIC   per define — and `--target` picks which defines to evaluate
# MAGIC - Every define also lands as a session temp view, so a plain `%sql` cell can query it
# MAGIC - `cql(...)` — the Python API, for when the CQL is a value your code holds: a lazy handle you can
# MAGIC   inspect, parameterize and reuse
# MAGIC - Point it at a folder of published CQL — a Git Folder, a Volume — and run a measure by name
# MAGIC - What you never had to supply: no endpoint, no token, no cluster, no catalog name
# MAGIC
# MAGIC <a href="$./01_getting_started">Open the notebook →</a>
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### 02 · CQL language tour
# MAGIC *Mostly data-free · one idea per cell*
# MAGIC
# MAGIC What the language can express, and why it is not SQL with clinical vocabulary. Each section states a
# MAGIC rule that measures depend on, then shows the engine keeping it.
# MAGIC
# MAGIC - Null handling and three-valued logic — divide by zero is `null`, not a crashed job, and a null
# MAGIC   condition takes the `else` branch
# MAGIC - Equality (`=`) and equivalence (`~`) — two operators that differ exactly where it matters
# MAGIC - Dates that carry their own precision: comparing `@2023` to a full timestamp answers `null` rather
# MAGIC   than inventing precision it does not have
# MAGIC - Timing expressions — `during`, `overlaps`, `before`, `includes` — and what naming the precision
# MAGIC   (`during day of`) rescues
# MAGIC - Quantities with UCUM units, ordered lists, parameters
# MAGIC - Terminology filtering: what `[Condition: "Diabetes"]` actually does
# MAGIC - Polymorphic choice types — FHIR `value[x]`, `Choice<Encounter, Condition>`, and the discriminated
# MAGIC   struct underneath both
# MAGIC
# MAGIC <a href="$./02_cql_language_tour">Open the notebook →</a>
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### 03 · A clinical quality measure, end to end
# MAGIC *Synthetic FHIR · 4,000 patients · nothing authored in the notebook*
# MAGIC
# MAGIC A published example measure run from folders beside this page: the CQL and the data
# MAGIC read from `measures/` and `data/`. The measure checks for patients with an encounter during
# MAGIC the period who also have a condition on record.
# MAGIC No application, no registry, no UI. The measure is not a string in a cell; it runs **by name**.
# MAGIC
# MAGIC - The measure `include`s a helper library, and the client resolves that closure out of the folder —
# MAGIC   the setup a real measure needs, since a published measure is rarely one file
# MAGIC - The one data contract: every `[Resource]` retrieve reads the environment's view of that type, which
# MAGIC   selects your own governed tables
# MAGIC - The per-patient results, then a SQL count — one join and one SQL cell away
# MAGIC - A `%%cql` follow-up question that `include`s the *same* library the measure uses, so an ad-hoc
# MAGIC   answer cannot quietly disagree with the report
# MAGIC - The same measure over a different measurement period, turning the population over completely
# MAGIC
# MAGIC <a href="$./03_clinical_measure">Open the notebook →</a>
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### 04 · Compilation and inspection
# MAGIC *Compile only · three artifacts · no data*
# MAGIC
# MAGIC The same measure as 03, never run. CQL compiles once into artifacts you can inspect before —
# MAGIC and without — executing anything. This is what lets a measure be reviewed before it is trusted.
# MAGIC
# MAGIC - The neutral define DAG: dependency edges, the cost-chosen materialization boundaries, and the
# MAGIC   compiler version pin
# MAGIC - ELM — HL7's standard interchange form for CQL logic
# MAGIC - The measure's own Spark physical plan, read over typed stub DataFrames — proof that the whole path is
# MAGIC   ordinary Spark underneath, and that a plan needs the data contract rather than the data
# MAGIC
# MAGIC <a href="$./04_compilation_and_inspection">Open the notebook →</a>
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### 05 · Authoring a measure in the notebook
# MAGIC *The same measure, written instead of read · two cells, one `include`*
# MAGIC
# MAGIC 03 runs a measure that already exists. This one drafts a measure from nothing: a cell of shared
# MAGIC eligibility logic, a second cell that `include`s it, and a folder to publish to once the numbers hold.
# MAGIC
# MAGIC - A `%%cql` cell that declares a `library` header **is** a library — a later cell can `include` it
# MAGIC - `--library` declares without running, for a cell of definitions there is nothing to evaluate in
# MAGIC - Drafts resolve ahead of any mounted folder, so a cell is how you override published content
# MAGIC - The draft is addressable by name, so the Python API can re-run it over a different year
# MAGIC - Publishing is writing the same text to a file — the cell was always the file
# MAGIC
# MAGIC <a href="$./05_authoring_in_the_notebook">Open the notebook →</a>
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### 06 · Clinical Research: Predicting Blood Pressure Control
# MAGIC *One CQL cell · SQL · Python · the default environment, nothing loaded*
# MAGIC
# MAGIC A research question across three languages: do age, gender, palliative care and dialysis predict whether
# MAGIC a patient with hypertension has controlled blood pressure?
# MAGIC
# MAGIC - The client comes from the notebook's environment, and one collapsible cell starts the engine
# MAGIC - One `%%cql` cell defines the cohort, the predictors and the outcome as standard definitions
# MAGIC - A `%sql` cell shapes the result into an analysis table
# MAGIC - A Python cell fits a logistic regression, scores it on held-out patients and bootstraps confidence intervals
# MAGIC
# MAGIC <a href="$./06_clinical_research">Open the notebook →</a>
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### 07 · Test files in a Volume
# MAGIC *FHIR NDJSON and value sets in a Volume · nothing loaded or published*
# MAGIC
# MAGIC Run CQL over a folder of test files instead of the environment's data: the way a measure is checked against
# MAGIC known cases before its results are trusted.
# MAGIC
# MAGIC - `use_resolvers` points this notebook's `cql()` calls and `%%cql` cells at the folder, for data and value sets
# MAGIC - FHIR NDJSON, one file per type, read with `read_files` at each run
# MAGIC - FHIR `ValueSet` JSON with its expansion
# MAGIC - A published measure from the environment's knowledge, run over the test patients
# MAGIC
# MAGIC <a href="$./07_test_files_in_a_volume">Open the notebook →</a>
