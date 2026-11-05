# Databricks notebook source
# MAGIC %md
# MAGIC <img src="./start-here-hero.png" width="820" alt="CQL on Databricks example notebooks — Start here">
# MAGIC
# MAGIC **CQL on Databricks** compiles CQL — HL7's Clinical Quality Language — to run natively on Spark.
# MAGIC The notebooks below take it from a one-line expression to applied scenarios over FHIR data. Each
# MAGIC is self-contained and runs on serverless with nothing to attach or install, so **Run All** is the
# MAGIC whole instruction.
# MAGIC
# MAGIC **Before you start.** Open a module, hit **Run All**, then read it top to bottom. Every section says
# MAGIC what it is about to show and **exactly what the next cell should print**, so a module is worth reading
# MAGIC even before you run it — and worth comparing against once you have. Everything runs in your own
# MAGIC session against temporary views: nothing is written to a catalog, and no state is left behind. The one
# MAGIC prerequisite is that CQL on Databricks is deployed in this workspace — the **CQL on Databricks** base
# MAGIC environment each module runs on, the `cql_*` functions in Unity Catalog, and the Compiler Service.
# MAGIC
# MAGIC **Start with Getting Started / Overview.** The rest assume you have seen it.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ## Getting Started
# MAGIC
# MAGIC ### Overview
# MAGIC *Setup · the two front doors · libraries · and it's inspectable*
# MAGIC
# MAGIC The two ways to run CQL from a notebook, how each hands its results back to Python and SQL, how a library is written in cells and published, and how a compiled measure is read without running it.
# MAGIC
# MAGIC - `%%cql` — a cell whose first line is `%%cql` *is* CQL; it declares, like a Python `def`, and runs nothing
# MAGIC - `cql(...)` — the Python API: a lazy handle you can inspect, parameterize and run; `exec()` returns DataFrames
# MAGIC - Every define also lands as a session temp view, so a plain `%sql` cell can query it
# MAGIC - A cell with a `library` header is a library a later cell can `include`; write it to a folder and it runs by name
# MAGIC - And it's inspectable: the define graph, the ELM and the Spark plan, with no data read
# MAGIC
# MAGIC <a href="$./getting_started/01_overview">Open the notebook →</a>
# MAGIC
# MAGIC ### CQL Language Tour
# MAGIC *Mostly data-free · one idea per cell*
# MAGIC
# MAGIC What the language can express, and why it is not SQL with clinical vocabulary.
# MAGIC
# MAGIC - Null handling and three-valued logic; equality (`=`) and equivalence (`~`)
# MAGIC - Dates that carry their own precision, and timing expressions — `during`, `overlaps`, `before`
# MAGIC - Quantities with UCUM units, lists, parameters, and terminology filtering
# MAGIC - Polymorphic choice types — FHIR `value[x]` and `Choice<Encounter, Condition>`
# MAGIC
# MAGIC <a href="$./getting_started/02_cql_language_tour">Open the notebook →</a>
# MAGIC
# MAGIC ### Data and Knowledge Resolution
# MAGIC *Tables, files in a Volume, DataFrames, cells and folders · one answer from each*
# MAGIC
# MAGIC Every role a run reads, from each place it can come from: the environment's tables, the same content as files in its volumes, the notebook itself, or a workspace folder.
# MAGIC
# MAGIC - Data and value sets from the tables, from FHIR NDJSON and `ValueSet` JSON files, and from DataFrames
# MAGIC - A measure's libraries from the environment, from a Volume folder, or from a workspace folder
# MAGIC - `use_resolvers` sets a source for this notebook's `cql()` calls and `%%cql` cells; the rest stay the environment's
# MAGIC - Results kept for the session only, in temporary tables, and reused by the next run
# MAGIC
# MAGIC <a href="$./getting_started/03_data_and_knowledge_resolution">Open the notebook →</a>
# MAGIC
# MAGIC ### Mapping Your Data
# MAGIC *Your own table · SQL views to FHIR · checked against the source*
# MAGIC
# MAGIC Map patients and visits in an EHR's own shape to the FHIR resources CQL reads, run CQL over the mapping, and check it: nothing unmapped or lost, and the same answers as an independent SQL query over the source.
# MAGIC
# MAGIC - One SQL view per FHIR type returns the `resource` struct, with a crosswalk table from local codes to SNOMED CT
# MAGIC - `use_resolvers` binds the mapped views as DataFrames, so nothing is written to a catalog
# MAGIC - Completeness checks: unmapped local codes, empty required fields, rows the mapping dropped
# MAGIC - The answers check fails, naming the patients, wherever CQL and the source disagree
# MAGIC
# MAGIC <a href="$./getting_started/04_mapping_your_data">Open the notebook →</a>
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ## Scenarios
# MAGIC
# MAGIC ### Quality Measurement
# MAGIC *Synthetic FHIR · 4,000 patients · nothing authored in the notebook*
# MAGIC
# MAGIC A published example measure run by name from the environment's knowledge.
# MAGIC
# MAGIC - The measure `include`s a helper library, and the client resolves that closure from the environment
# MAGIC - The per-patient results, then the rate as one SQL cell
# MAGIC - A follow-up question asked of the *same* library the measure uses
# MAGIC - The same measure over a different measurement period
# MAGIC
# MAGIC <a href="$./scenarios/quality_measurement">Open the notebook →</a>
# MAGIC
# MAGIC ### Clinical Research
# MAGIC *One CQL cell · SQL · Python · the default environment, nothing loaded*
# MAGIC
# MAGIC A research question across three languages: do age, gender, palliative care and dialysis predict whether blood pressure is controlled?
# MAGIC
# MAGIC - One `%%cql` cell defines the cohort, the predictors and the outcome as standard definitions
# MAGIC - A `%sql` cell shapes the result into an analysis table
# MAGIC - A Python cell fits a logistic regression, scores it on held-out patients and bootstraps confidence intervals
# MAGIC
# MAGIC <a href="$./scenarios/clinical_research">Open the notebook →</a>
