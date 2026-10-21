package com.databricks.cql.harness

/** The SINGLE normalization from a runtime value to stable, deterministic JSON
  * (TECH-DETERMINISM, CONF-GROUND-TRUTH). Both sides of every differential — a Spark `Row`
  * value collected from the engine, and a value from the Java CQF reference — pass through
  * here, so the comparison can never diverge through two hand-synced copies of the rules
  * (PROC-DRY). It therefore handles BOTH worlds' value shapes: JVM box types, Scala/Java
  * collections (Spark array columns), and HAPI FHIR primitives (a bare `return E.status`).
  *
  * The rules for everything that is not a Spark `Row` live in `com.databricks.cql.cqf.CqfJson`, shared with the
  * evaluator's point App (`DEC-SAME-ANSWER`). */
object JsonNormalize {

  /** The engine's Spark `Row` shapes are handled here; every other value, including the CQF reference's runtime
    * types, renders through `CqfJson` with this function for nested values, so both sides share one rule set. */
  def value(v: Any): ujson.Value = v match {
    // A raw FHIR resource result (DATA-EXPORT): the engine carries identity lineage inside the resource struct
    // (`__resource_type`/`__resource_id`); render it to the canonical `Type/id` reference `CqfJson` renders the
    // Java reference's resource to, so a `return E` or bare-`[Encounter]` define diffs clean by identity.
    case r: org.apache.spark.sql.Row if isResourceStruct(r) => ujson.Str(resourceRef(r))
    // A Choice is a discriminated `struct(__choice, as<Type>…)`; the reference returns the concrete
    // value, so unwrap to the populated slot (LANG-CHOICE).
    case r: org.apache.spark.sql.Row if isChoiceStruct(r) => value(choiceValue(r))
    // A struct (a quantity `{value,unit}`, an interval `{low,high}`, a tuple): an object by field name, the
    // shape `CqfJson` renders the reference's runtime Quantity/Interval/Tuple to.
    case r: org.apache.spark.sql.Row if r.schema != null =>
      ujson.Obj.from(r.schema.fieldNames.toSeq.zip(r.toSeq).map { case (n, fv) => n -> value(fv) })
    case other => com.databricks.cql.cqf.CqfJson.render(other, value)
  }

  /** The engine's raw-resource export carries identity lineage inside the struct
    * (`__resource_type`/`__resource_id`, DATA-EXPORT). */
  private def isResourceStruct(r: org.apache.spark.sql.Row): Boolean =
    r.schema != null && {
      val fs = r.schema.fieldNames.toSeq
      fs.contains("__resource_type") && fs.contains("__resource_id")
    }

  /** Render an engine resource struct to its `Type/id` reference (the lineage identity). */
  private def resourceRef(r: org.apache.spark.sql.Row): String =
    s"${r.get(r.schema.fieldIndex("__resource_type"))}/${r.get(r.schema.fieldIndex("__resource_id"))}"

  /** The engine's Choice encoding is a `struct(__choice, as<Type>…)`. */
  private def isChoiceStruct(r: org.apache.spark.sql.Row): Boolean =
    r.schema != null && r.schema.fieldNames.headOption.contains("__choice")

  /** The populated member of a Choice struct: the slot named by the `__choice` discriminator. The slot
    * name sanitizes the tag to an identifier (`ColumnLowering.choiceSlot`), so a resource/composite tag
    * (`FHIR.Encounter`, `Interval<DateTime>`) maps to `asFHIR_Encounter` / `asInterval_DateTime_` — apply
    * the SAME sanitization here, not a bare `"as" + tag`, or such a slot is never found (LANG-CHOICE-RESOURCE). */
  private def choiceValue(r: org.apache.spark.sql.Row): Any = {
    val slot = "as" + String.valueOf(r.get(0)).replaceAll("[^A-Za-z0-9]", "_")
    val idx  = r.schema.fieldNames.indexOf(slot)
    if (idx >= 0) r.get(idx) else null
  }

}
