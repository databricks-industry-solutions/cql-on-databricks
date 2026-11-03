# Databricks notebook source
# MAGIC %md
# MAGIC # 2 · CQL language tour
# MAGIC What the language can express, one `%%cql` cell per idea. CQL is not SQL with clinical vocabulary —
# MAGIC it has its own answers for nulls, for dates that only know their own year, for units, for codes, and
# MAGIC for values whose type is not decided until you look at the row. **Getting any of those subtly wrong
# MAGIC produces plausible-but-wrong rates that fail certification silently**, so each section below states
# MAGIC the rule and then shows the engine keeping it.
# MAGIC
# MAGIC **Read it without running it.** The markdown above every cell says exactly what that cell should
# MAGIC print. Run All and compare.
# MAGIC
# MAGIC Sections 1–8 are **data-free** — no data is read, and every define resolves to CQL's `Unfiltered`
# MAGIC context, so `__subject_id` is the `'*'` seed and the single row is the answer.
# MAGIC Section 9 brings a handful of FHIR resources, and sections 10–11 need them.
# MAGIC
# MAGIC **Runs on serverless — no cluster to attach.** Just **Run All**. If `%%cql` is unfamiliar, start with
# MAGIC notebook 1.

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
# MAGIC ## 1. Arithmetic and the numeric tower
# MAGIC The ordinary part, and the baseline for everything after it. CQL distinguishes `/` (decimal
# MAGIC division) from `div` (truncated integer division) and carries decimals at a fixed scale rather than
# MAGIC as floats — a rate is not a floating-point value.
# MAGIC
# MAGIC Each define is displayed as its own table, headed by its name.
# MAGIC
# MAGIC | define | `__val` |
# MAGIC |---|---|
# MAGIC | `Sum` | `5` |
# MAGIC | `Product` | `42` |
# MAGIC | `Quotient` | `3.14285714` (a decimal, not `3`) |
# MAGIC | `IntegerQuotient` | `3` |
# MAGIC | `Remainder` | `1` |
# MAGIC | `PoweredUp` | `1024` |

# COMMAND ----------
# MAGIC %%cql
# MAGIC define "Sum":             2 + 3
# MAGIC define "Product":         6 * 7
# MAGIC define "Quotient":        22 / 7
# MAGIC define "IntegerQuotient": 22 div 7
# MAGIC define "Remainder":       22 mod 7
# MAGIC define "PoweredUp":       2 ^ 10

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. Null handling — three-valued logic
# MAGIC A CQL boolean is `true | false | null`, and **null propagation is the single most common source of
# MAGIC subtle bugs in a measure**. Two rules do most of the work:
# MAGIC
# MAGIC - **An unknown answer is `null`, not a failure.** Divide by zero is a per-row null, not a crashed job
# MAGIC   — one patient's anomaly cannot fail a population run.
# MAGIC - **A null condition is "not true"**, so it takes the `else` branch, and a null `where` predicate
# MAGIC   *excludes* the row. Everything routes through a `cqlTrue(x)` test that is true only when `x` is
# MAGIC   exactly `true` — never Spark's own boolean defaults, which differ.
# MAGIC
# MAGIC | define | `__val` | why |
# MAGIC |---|---|---|
# MAGIC | `DivideByZero` | `null` | spec-defined result, not an error |
# MAGIC | `NullPropagates` | `null` | `1 + null` |
# MAGIC | `TrueAndNull` | `null` | could still be either |
# MAGIC | `FalseAndNull` | `false` | already decided — null cannot rescue it |
# MAGIC | `TrueOrNull` | `true` | already decided |
# MAGIC | `NotNull` | `null` | negating unknown is unknown |
# MAGIC | `NullImpliesTrue` | `true` | `implies` is `not A or B` |
# MAGIC | `Coalesced` | `2` | first non-null |
# MAGIC | `IsNullOnNull` | `true` | the explicit test, which is never itself null |
# MAGIC | `NullConditionGoesElse` | `'else-branch'` | null is not-true |

# COMMAND ----------
# MAGIC %%cql
# MAGIC define "DivideByZero":          1 / 0
# MAGIC define "NullPropagates":        1 + null
# MAGIC define "TrueAndNull":           true and null
# MAGIC define "FalseAndNull":          false and null
# MAGIC define "TrueOrNull":            true or null
# MAGIC define "NotNull":               not null
# MAGIC define "NullImpliesTrue":       null implies true
# MAGIC define "Coalesced":             Coalesce(null, 2, 3)
# MAGIC define "IsNullOnNull":          IsNull(null)
# MAGIC define "NullConditionGoesElse": if null then 'then-branch' else 'else-branch'

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Equality (`=`) and equivalence (`~`)
# MAGIC Two different operators that a reader coming from SQL will assume are one. They differ **exactly
# MAGIC where it matters most**: around nulls and around the sloppiness of real data.
# MAGIC
# MAGIC - **`=` is truth-functional.** If either side is unknown, the answer is unknown — `1 = null` is
# MAGIC   `null`, and so is `null = null`.
# MAGIC - **`~` asks "are these the same value?"** and always answers `true` or `false`. Two nulls are
# MAGIC   equivalent; a value and a null are not. Strings compare case- and whitespace-insensitively.
# MAGIC
# MAGIC The two-null cases below are written `null as Integer` because CQL is **statically typed and a bare
# MAGIC `null` carries no type**. In `1 = null` the comparison takes its type from the other side; with null
# MAGIC on both sides there is no other side, and the compiler rejects the expression rather than guess which
# MAGIC `=` you meant. The cast names the type — the value is still null, and the answers are the same
# MAGIC whichever type you name.
# MAGIC
# MAGIC This is why `distinct` in a CQL query deduplicates on **equivalence**, not on SQL's `DISTINCT`,
# MAGIC and why code matching is written with `~`: `'Diabetes'` and `'diabetes'` are
# MAGIC the same clinical string, and a value set full of nulls should not silently collapse into unknowns.
# MAGIC
# MAGIC | define | `__val` |
# MAGIC |---|---|
# MAGIC | `IntEqual` | `true` |
# MAGIC | `EqualityWithNull` | `null` |
# MAGIC | `EquivalenceWithNull` | `false` |
# MAGIC | `NullEqualsNull` | `null` |
# MAGIC | `NullEquivNull` | `true` |
# MAGIC | `CaseSensitiveEqual` | `false` |
# MAGIC | `CaseInsensitiveEquiv` | `true` |

# COMMAND ----------
# MAGIC %%cql
# MAGIC define "IntEqual":             1 = 1
# MAGIC define "EqualityWithNull":     1 = null
# MAGIC define "EquivalenceWithNull":  1 ~ null
# MAGIC define "NullEqualsNull":       (null as Integer) = (null as Integer)
# MAGIC define "NullEquivNull":        (null as Integer) ~ (null as Integer)
# MAGIC define "CaseSensitiveEqual":   'Diabetes' = 'diabetes'
# MAGIC define "CaseInsensitiveEquiv": 'Diabetes' ~ 'diabetes'

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. Dates carry their own precision
# MAGIC `@2023` is not "the first instant of 2023". It is **the year 2023, and nothing finer is known**. A
# MAGIC CQL date/time carries its precision as data (physically: the length of its ISO string), and a
# MAGIC comparison that the precision cannot decide returns **`null`** — the engine will not invent
# MAGIC precision it does not have.
# MAGIC
# MAGIC Spark's own `DATE`/`TIMESTAMP` carry no precision metadata at all, which is why none of this lowers
# MAGIC onto native Spark date operators.
# MAGIC
# MAGIC | define | `__val` | why |
# MAGIC |---|---|---|
# MAGIC | `YearOnly` | `2023` | a year-precision value |
# MAGIC | `FullDate` | `2023-06-15` | a day-precision value |
# MAGIC | `SameDay` | `true` | same precision on both sides — decidable |
# MAGIC | `YearVsDay` | `null` | equal as far as the coarse side knows; that is not "equal" |
# MAGIC | `DecidedByYear` | `true` | 2022 < 2023 is settled at year precision, so precision never comes up |
# MAGIC | `NextDay` | `2013-02-01` | `successor of` steps one unit **at the value's own precision** |
# MAGIC
# MAGIC `successor of` is also where the tour first calls the engine's runtime: it emits `cql_temporal_step`,
# MAGIC a Unity Catalog Scala function — which is why this whole notebook runs on
# MAGIC serverless with nothing installed on the compute.

# COMMAND ----------
# MAGIC %%cql
# MAGIC define "YearOnly":      @2023
# MAGIC define "FullDate":      @2023-06-15
# MAGIC define "SameDay":       @2023-06-15 = @2023-06-15
# MAGIC define "YearVsDay":     @2023 = @2023-06-15
# MAGIC define "DecidedByYear": @2022 < @2023-06-15
# MAGIC define "NextDay":       successor of @2013-01-31

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5. Timing expressions — the interval algebra
# MAGIC Almost every clinical criterion is a timing question: *did this encounter happen during the
# MAGIC measurement period? does this coverage span overlap that one? was the diagnosis before the
# MAGIC procedure?* CQL answers them with first-class intervals and the full set of Allen relations —
# MAGIC `during`, `overlaps`, `before` / `after`, `includes` / `included in`, `meets`, `starts`, `ends`,
# MAGIC `same X as` — all implemented precision-aware in the engine's own library rather than on Spark's
# MAGIC date operators.
# MAGIC
# MAGIC Intervals are typed, and each boundary is independently open or closed. An open bound is normalized
# MAGIC to a closed one by discrete successor/predecessor before the relations run, so `Interval(1, 10]`
# MAGIC starts at `2`.
# MAGIC
# MAGIC | define | `__val` |
# MAGIC |---|---|
# MAGIC | `Overlaps` | `true` |
# MAGIC | `PointDuring` | `true` |
# MAGIC | `Before` | `true` |
# MAGIC | `Includes` | `true` |
# MAGIC | `Width` | `9` |
# MAGIC | `OpenStart` | `2` |
# MAGIC | `InMeasurementPeriod` | `true` |

# COMMAND ----------
# MAGIC %%cql
# MAGIC define "Overlaps":    Interval[1, 5] overlaps Interval[3, 8]
# MAGIC define "PointDuring": 5 during Interval[1, 10]
# MAGIC define "Before":      @2012-01-15 before @2012-03-20
# MAGIC define "Includes":    Interval[1, 10] includes Interval[3, 4]
# MAGIC define "Width":       width of Interval[1, 10]
# MAGIC define "OpenStart":   start of Interval(1, 10]
# MAGIC define "InMeasurementPeriod":
# MAGIC   @2026-06-15 during Interval[@2026-01-01, @2026-12-31]

# COMMAND ----------
# MAGIC %md
# MAGIC ### 5a. Naming the precision a relation compares at
# MAGIC Section 4's rule and section 5's relations meet here, and the meeting has teeth.
# MAGIC
# MAGIC A measurement period is usually written at **day** precision (`Interval[@2026-01-01, @2026-12-31]`),
# MAGIC while a real encounter period starts at a **millisecond** instant (`2026-01-01T00:00:00.000`). Ask
# MAGIC whether one is `during` the other and the bounds tie — equal as far as the coarse side knows — so
# MAGIC the honest answer is `null`, the row fails its `where`, and the denominator quietly comes back empty
# MAGIC with no error at all. That is a real defect this engine shipped once, in a measure run: **71
# MAGIC qualifying encounters, zero rows, no message.**
# MAGIC
# MAGIC The fix is in the language, not in the data: **name the precision on the relation**. `during day of`
# MAGIC says "stop caring below the day", and because the named precision belongs to the *comparison* rather
# MAGIC than to either operand, **both sides truncate to it** before comparing.
# MAGIC
# MAGIC | define | `__val` | why |
# MAGIC |---|---|---|
# MAGIC | `Unqualified` | `null` | day bound vs millisecond value — undecidable, and silently so |
# MAGIC | `AtDayPrecision` | `true` | both sides truncated to the day |
# MAGIC | `OutsideAtDayPrecision` | `false` | truncation *decides* the tie — it does not blanket-approve |

# COMMAND ----------
# MAGIC %%cql
# MAGIC // The same encounter window, asked the same question, twice.
# MAGIC define "Unqualified":
# MAGIC   Interval[@2026-01-01T00:00:00.000, @2026-01-01T01:00:00.000]
# MAGIC     during Interval[@2026-01-01, @2026-12-31]
# MAGIC
# MAGIC define "AtDayPrecision":
# MAGIC   Interval[@2026-01-01T00:00:00.000, @2026-01-01T01:00:00.000]
# MAGIC     during day of Interval[@2026-01-01, @2026-12-31]
# MAGIC
# MAGIC // The negative control: a window genuinely outside the period stays false.
# MAGIC define "OutsideAtDayPrecision":
# MAGIC   Interval[@2027-01-01T00:00:00.000, @2027-01-02T00:00:00.000]
# MAGIC     during day of Interval[@2026-01-01, @2026-12-31]

# COMMAND ----------
# MAGIC %md
# MAGIC ## 6. Quantities and UCUM units
# MAGIC A clinical value is a number **and a unit**, and the unit is not decoration: `500 mg` and `1 g` are
# MAGIC comparable, and getting that wrong is a dosing error. CQL quantities carry their UCUM unit, and
# MAGIC comparison and arithmetic convert. Spark has no quantity type, so a quantity is
# MAGIC carried as a `(value, unit)` struct and the conversion is the engine's.
# MAGIC
# MAGIC | define | `__val` |
# MAGIC |---|---|
# MAGIC | `Weight` | `{value: 70, unit: "kg"}` |
# MAGIC | `EqualAcrossUnits` | `true` |
# MAGIC | `LessAcrossUnits` | `true` |
# MAGIC | `GreaterAcrossUnits` | `true` |
# MAGIC | `SumAcrossUnits` | `{value: 1.5, unit: "kg"}` — converted into the **left** operand's unit |
# MAGIC
# MAGIC (Decimals are carried at a fixed scale, so the displayed values are `70.00000000` and `1.50000000`.)

# COMMAND ----------
# MAGIC %%cql
# MAGIC define "Weight":             70 'kg'
# MAGIC define "EqualAcrossUnits":   1 'kg' = 1000 'g'
# MAGIC define "LessAcrossUnits":    500 'mg' < 1 'g'
# MAGIC define "GreaterAcrossUnits": 2 'm' > 150 'cm'
# MAGIC define "SumAcrossUnits":     1 'kg' + 500 'g'

# COMMAND ----------
# MAGIC %md
# MAGIC ## 7. Lists, conditionals and parameters
# MAGIC CQL lists are **ordered and allow duplicates** — they are sequences, not sets, so `First` and indexed
# MAGIC access mean something and the order is carried as data. A parameter with a `default`
# MAGIC is a constant for the run; it is the same mechanism a measure uses for its measurement period, and
# MAGIC notebook 1 showed how to override one from Python.
# MAGIC
# MAGIC | define | `__val` |
# MAGIC |---|---|
# MAGIC | `Numbers` | `[1, 2, 3, 4, 5]` — one row, the whole list as an array |
# MAGIC | `ListCount` | `4` |
# MAGIC | `FirstElement` | `10` |
# MAGIC | `Membership` | `true` |
# MAGIC | `Grade` | `"B"` |
# MAGIC | `OverThreshold` | `true` |

# COMMAND ----------
# MAGIC %%cql
# MAGIC parameter "Threshold" default 3
# MAGIC
# MAGIC define "Numbers":       { 1, 2, 3, 4, 5 }
# MAGIC define "ListCount":     Count({ 1, 2, 3, 4 })
# MAGIC define "FirstElement":  First({ 10, 20, 30 })
# MAGIC define "Membership":    3 in { 1, 2, 3 }
# MAGIC define "Grade":         case when 1 = 2 then 'A' else 'B' end
# MAGIC define "OverThreshold": 4 > "Threshold"

# COMMAND ----------
# MAGIC %md
# MAGIC ## 8. Polymorphic choice types — the discriminated struct
# MAGIC A `Choice<T1, T2, …>` is a value that is **one of several statically-known types**. It shows up
# MAGIC whenever the branches of a conditional disagree about type, whenever a
# MAGIC `union` mixes list element types — and, most importantly, at the source, because FHIR is full of
# MAGIC `value[x]` elements (section 11).
# MAGIC
# MAGIC The physical encoding is a **discriminated struct** that mirrors FHIR's own `value[x]` convention:
# MAGIC one `__choice` tag naming the active member, plus one typed slot per member of which exactly one is
# MAGIC populated. It is deliberately *not* a `VARIANT` — the member types are known from the compiled ELM,
# MAGIC so a closed tagged struct keeps the dispatch static and the schema honest.
# MAGIC
# MAGIC `as` projects the named member (null if that is not the active one) and `is` tests the discriminator.
# MAGIC
# MAGIC | define | `__val` |
# MAGIC |---|---|
# MAGIC | `HetIfInt` | `{__choice: "Integer", asInteger: 1, asString: null}` |
# MAGIC | `HetIfStr` | `{__choice: "String", asInteger: null, asString: "x"}` |
# MAGIC | `AsMatch` | `1` |
# MAGIC | `AsMismatch` | `null` — asked for the wrong member |
# MAGIC | `IsMatch` | `true` |
# MAGIC | `IsMismatch` | `false` — a test, so never null |

# COMMAND ----------
# MAGIC %%cql
# MAGIC define "HetIfInt":   if true  then 1 else 'x'
# MAGIC define "HetIfStr":   if false then 1 else 'x'
# MAGIC define "AsMatch":    (if true then 1 else 'x') as Integer
# MAGIC define "AsMismatch": (if true then 1 else 'x') as String
# MAGIC define "IsMatch":    (if true then 1 else 'x') is Integer
# MAGIC define "IsMismatch": (if true then 1 else 'x') is String

# COMMAND ----------
# MAGIC %md
# MAGIC ## 9. Bring a handful of FHIR resources
# MAGIC The last two sections need data. The engine reads every `[Resource]` retrieve from a relation that returns
# MAGIC the resource as a `resource` struct, and optionally `patient_id`, the key of the patient the row belongs to.
# MAGIC In a real deployment that relation is a view in your environment's `data` schema, over your own governed
# MAGIC tables.
# MAGIC Here it is eleven rows in DataFrames, handed to this notebook's cells with `use_resolvers`: every later
# MAGIC `%%cql` cell reads its data and value sets through the resolvers it names, and nothing else changes.
# MAGIC
# MAGIC The resources are plain **FHIR JSON**, one per row, of mixed types: `FhirJson` reads each row's
# MAGIC `resourceType` and parses it against the compiler's FHIR model. That is not cosmetic: an absent
# MAGIC `value[x]` slot has to be genuinely `null` for section 11 to mean anything, and nothing here declares
# MAGIC a schema by hand. The subject key comes from each resource's own `subject` reference.
# MAGIC
# MAGIC The cast:
# MAGIC
# MAGIC | patient | conditions | observations | encounters |
# MAGIC |---|---|---|---|
# MAGIC | `p1` | `c1` — SNOMED 44054006 | `o1` — a **Quantity**, 120 mm[Hg] · `o2` — a **string**, `"positive"` | `e1` |
# MAGIC | `p2` | none | none | none |
# MAGIC | `p3` | `c2` — SNOMED 195967001 | `o3` — a **Quantity**, 98 mg/dL | `e2` |
# MAGIC
# MAGIC …plus a one-row value-set expansion table: the Diabetes value set contains exactly SNOMED
# MAGIC 44054006.
# MAGIC
# MAGIC **Expected output:** `types: ['Condition', 'Encounter', 'Observation', 'Patient']`.

# COMMAND ----------
from cql_databricks.compiler import FhirJson, use_resolvers
from cql_databricks.resolvers import DataFrameDataResolver, DataFrameTerminologyResolver

RESOURCES = [
    '{"resourceType":"Patient","id":"p1","birthDate":"1990-05-01"}',
    '{"resourceType":"Patient","id":"p2","birthDate":"2010-03-15"}',
    '{"resourceType":"Patient","id":"p3","birthDate":"1975-11-20"}',
    '{"resourceType":"Condition","id":"c1","subject":{"reference":"Patient/p1"},'
    '"code":{"coding":[{"system":"http://snomed.info/sct","code":"44054006"}]},"recordedDate":"2026-02-11"}',
    '{"resourceType":"Condition","id":"c2","subject":{"reference":"Patient/p3"},'
    '"code":{"coding":[{"system":"http://snomed.info/sct","code":"195967001"}]},"recordedDate":"2026-04-02"}',
    '{"resourceType":"Observation","id":"o1","subject":{"reference":"Patient/p1"},'
    '"valueQuantity":{"value":120,"unit":"mm[Hg]"}}',
    '{"resourceType":"Observation","id":"o2","subject":{"reference":"Patient/p1"},"valueString":"positive"}',
    '{"resourceType":"Observation","id":"o3","subject":{"reference":"Patient/p3"},'
    '"valueQuantity":{"value":98,"unit":"mg/dL"}}',
    '{"resourceType":"Encounter","id":"e1","subject":{"reference":"Patient/p1"},"status":"finished",'
    '"period":{"start":"2026-03-01T08:00:00.000","end":"2026-03-05T12:00:00.000"}}',
    '{"resourceType":"Encounter","id":"e2","subject":{"reference":"Patient/p3"},"status":"finished",'
    '"period":{"start":"2026-07-19T09:00:00.000","end":"2026-07-19T10:30:00.000"}}',
]

# The value-set expansion: one row per (valueset, system, code).
VALUESETS = spark.createDataFrame([("urn:cql_databricks:vs:diabetes", "http://snomed.info/sct", "44054006")],
                                  "valueset_id STRING, system STRING, code STRING")

# Each type's rows, parsed against the compiler's FHIR model.
fhir = FhirJson(spark.createDataFrame([(r,) for r in RESOURCES], "resource STRING"), column="resource")
types = fhir.found_types(spark)
use_resolvers(data=DataFrameDataResolver({t: fhir.to_df(spark, t) for t in types}, name="tour"),
              terminology=DataFrameTerminologyResolver(VALUESETS, name="tour"))
print("types:", types)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 10. Terminology filtering
# MAGIC Clinical logic is code-dominated: a measure is mostly a set of questions about whether a resource's
# MAGIC code is in some published value set. CQL writes that as `[Condition: "Diabetes"]` — a retrieve
# MAGIC constrained by value-set membership.
# MAGIC
# MAGIC Two things make it work at scale:
# MAGIC
# MAGIC - **Value sets are pre-expanded, never called per row.** Expansion belongs to whoever authored
# MAGIC   the set; CQL on Databricks consumes the expansion as an ordinary table and the membership
# MAGIC   test compiles to a broadcast semi-join. There is no terminology service in the hot path, and the
# MAGIC   expansion version is pinned so a rate can be recomputed.
# MAGIC - **Where you write it does not change what you get.** `[Condition: "Diabetes"]` and
# MAGIC   `[Condition] C where C.code in "Diabetes"` are the same question; the compiler pushes the second
# MAGIC   into the retrieve's code filter so both produce the same plan and the same rows.
# MAGIC
# MAGIC These are `context Patient` defines, so now there is a real subject — one row per patient, not one
# MAGIC `'*'` row.
# MAGIC
# MAGIC **Expected output:** three tables, three rows each.
# MAGIC
# MAGIC | `__subject_id` | `HasAnyCondition` | `HasDiabetes` | `HasDiabetesViaWhere` |
# MAGIC |---|---|---|---|
# MAGIC | `p1` | `true` | `true` | `true` |
# MAGIC | `p2` | `false` | `false` | `false` |
# MAGIC | `p3` | `true` | `false` | `false` |
# MAGIC
# MAGIC `p3` is the row that proves the filter is doing work: they have a Condition, coded, just not one in
# MAGIC this value set.
# MAGIC
# MAGIC `C.code in "Diabetes"` converts a FHIR `CodeableConcept` to a CQL `Concept`, and that conversion lives in
# MAGIC FHIRHelpers, so the library includes it, as every published measure does. CQL never includes it for you.

# COMMAND ----------
# MAGIC %%cql
# MAGIC library TerminologyTour version '1.0.0'
# MAGIC using FHIR version '4.0.1'
# MAGIC include FHIRHelpers version '4.0.1' called FHIRHelpers
# MAGIC
# MAGIC valueset "Diabetes": 'urn:cql_databricks:vs:diabetes'
# MAGIC
# MAGIC context Patient
# MAGIC
# MAGIC define "HasAnyCondition":     exists ([Condition])
# MAGIC define "HasDiabetes":         exists ([Condition: "Diabetes"])
# MAGIC define "HasDiabetesViaWhere": exists ([Condition] C where C.code in "Diabetes")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 11. Polymorphic choice types over FHIR
# MAGIC Section 8's discriminated struct was a language curiosity. Here is why it exists.
# MAGIC
# MAGIC ### `value[x]` — the choice at the source
# MAGIC An `Observation.value` may be a Quantity, a string, a CodeableConcept, a boolean… and FHIR stores it
# MAGIC in a *differently named element per type*: `valueQuantity`, `valueString`, `valueCodeableConcept`.
# MAGIC CQL papers over that with one name — you write `O.value` — and `is` / `as` dispatch to the right
# MAGIC physical slot.
# MAGIC
# MAGIC The dispatch is gated on the **model**, not on the element's name: only a
# MAGIC genuine FHIR `[x]` choice element is projected to a `<path><Type>` slot. Over a monomorphic element
# MAGIC like `Encounter.status`, `as` is an ordinary downcast. A name-only heuristic here reads a slot that
# MAGIC does not exist, gets null, and silently drops rows.
# MAGIC
# MAGIC ### `Choice<Encounter, Condition>` — whole resources as members
# MAGIC A choice member does not have to be a scalar. `[Encounter] union [Condition]` unions two retrieves
# MAGIC with *different schemas*, and CQL types the result `List<Choice<Encounter, Condition>>`. Each arm
# MAGIC stays a relation and tags its rows with the active member, so the
# MAGIC two resource schemas align into one Choice-tagged relation — no boxing, no exploding, one scan each.
# MAGIC
# MAGIC That define is **list-shaped**, so it is the other result shape notebook 1 mentioned: one row per
# MAGIC element, with an `__ordinal` column carrying the order.
# MAGIC
# MAGIC **Expected output — `HasQuantityValue`** (one row per patient):
# MAGIC
# MAGIC | `__subject_id` | `__val` |
# MAGIC |---|---|
# MAGIC | `p1` | `true` |
# MAGIC | `p2` | `false` |
# MAGIC | `p3` | `true` |
# MAGIC
# MAGIC **`StringValues`** — one row, `p1` / `"positive"`. `p2` and `p3` have no string-valued observation,
# MAGIC so they contribute no rows at all (an empty list is no rows, not a null row).
# MAGIC
# MAGIC **`EncountersOrConditions`** — four rows, each `__val` a discriminated struct, and an `__ordinal`
# MAGIC that puts the Encounter arm before the Condition arm within each patient (display order is whatever
# MAGIC Spark hands back; `__ordinal` is the authority):
# MAGIC
# MAGIC | `__subject_id` | `__val` |
# MAGIC |---|---|
# MAGIC | `p1` | `{__choice: "FHIR.Encounter", __resource_type: "Encounter", __resource_id: "e1"}` |
# MAGIC | `p1` | `{__choice: "FHIR.Condition", __resource_type: "Condition", __resource_id: "c1"}` |
# MAGIC | `p3` | `{__choice: "FHIR.Encounter", __resource_type: "Encounter", __resource_id: "e2"}` |
# MAGIC | `p3` | `{__choice: "FHIR.Condition", __resource_type: "Condition", __resource_id: "c2"}` |
# MAGIC
# MAGIC Note what the struct kept: the **identity** of each resource and nothing else. Nothing downstream
# MAGIC navigated a field, so nothing else was read — the choice struct is narrowed to what the query
# MAGIC actually demanded.

# COMMAND ----------
# MAGIC %%cql
# MAGIC library ChoiceTour version '1.0.0'
# MAGIC using FHIR version '4.0.1'
# MAGIC
# MAGIC context Patient
# MAGIC
# MAGIC // value[x]: `is` tests the discriminator, `as` projects the matching member.
# MAGIC define "HasQuantityValue":
# MAGIC   exists ([Observation] O where O.value is FHIR.Quantity)
# MAGIC
# MAGIC define "StringValues":
# MAGIC   [Observation] O where O.value is FHIR.string return all O.value as FHIR.string
# MAGIC
# MAGIC // A Choice whose members are whole resource bodies with different schemas.
# MAGIC define "EncountersOrConditions":
# MAGIC   [Encounter] union [Condition]

# COMMAND ----------
# MAGIC %md
# MAGIC ---
# MAGIC ### Where next
# MAGIC Every rule in this tour exists because a measure depends on it. **03 · A clinical quality measure**
# MAGIC puts them together into an eCQM-shaped care-gap report over FHIR data, and **04 · Compilation and
# MAGIC inspection** opens up what the compiler produced along the way.

# COMMAND ----------
# Every cell above ran, so the notebook is green. There are no value assertions here by design: the
# narrative states each expected result, and a compile or runtime failure fails its own cell loudly
# rather than being collected into a summary at the end.
import json
dbutils.notebook.exit(json.dumps({"ok": True, "notebook": "02_cql_language_tour"}))
