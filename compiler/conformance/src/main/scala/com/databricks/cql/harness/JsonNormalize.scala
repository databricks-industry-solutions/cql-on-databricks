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
  import com.databricks.cql.ir.CqlType
  import com.databricks.cql.ir.CqlType.{AnyType, ListType, NamedType, TupleType}
  import com.databricks.cql.sema.FhirModel

  /** DATA-EXPORT: the FHIR resource type a CQL type names, if it names one. */
  private def resourceType(t: CqlType): Option[String] = t match {
    case NamedType(n) => Some(FhirModel.localName(n.stripPrefix("FHIR."))).filter(FhirModel.resourceTypes.contains)
    case _            => None
  }

  /** The engine's Spark `Row` shapes are handled here; every other value, including the CQF reference's runtime
    * types, renders through `CqfJson` with this function for nested values, so both sides share one rule set.
    * `t` is the value's CQL type when the caller knows it (the engine side), so a resource renders by its type. */
  def value(v: Any, t: CqlType = AnyType): ujson.Value = (v, t) match {
    // A raw FHIR resource result (DATA-EXPORT): the resource struct carries no lineage; its type is the value's
    // CQL type and its identity its own `id`. Render it to the canonical `Type/id` reference `CqfJson` renders
    // the Java reference's resource to, so a `return E` or bare-`[Encounter]` define diffs clean by identity.
    case (r: org.apache.spark.sql.Row, _) if resourceType(t).isDefined && r.schema != null &&
        r.schema.fieldNames.contains("id") =>
      ujson.Str(s"${resourceType(t).get}/${r.get(r.schema.fieldIndex("id"))}")
    // The engine's demand-pruned handle for a resource in a mixed list carries its identity (ARCH-RESOURCE-HANDLE).
    case (r: org.apache.spark.sql.Row, _) if isResourceHandle(r) => ujson.Str(handleRef(r))
    // A Choice is a discriminated `struct(__choice, as<Type>…)`; the reference returns the concrete
    // value, so unwrap to the populated slot (LANG-CHOICE), typed by its tag.
    case (r: org.apache.spark.sql.Row, _) if isChoiceStruct(r) =>
      value(choiceValue(r), NamedType(String.valueOf(r.get(0))))
    // A tuple: an object by field name, each field typed when the tuple's type is known.
    case (r: org.apache.spark.sql.Row, TupleType(elements)) if r.schema != null =>
      val types = elements.toMap
      ujson.Obj.from(r.schema.fieldNames.toSeq.zip(r.toSeq).map { case (n, fv) => n -> value(fv, types.getOrElse(n, AnyType)) })
    // A struct (a quantity `{value,unit}`, an interval `{low,high}`): an object by field name, the
    // shape `CqfJson` renders the reference's runtime Quantity/Interval/Tuple to.
    case (r: org.apache.spark.sql.Row, _) if r.schema != null =>
      ujson.Obj.from(r.schema.fieldNames.toSeq.zip(r.toSeq).map { case (n, fv) => n -> value(fv) })
    case (xs: scala.collection.Seq[_], ListType(element)) => ujson.Arr.from(xs.map(value(_, element)))
    case (other, _) => com.databricks.cql.cqf.CqfJson.render(other, x => value(x))
  }

  /** The engine's own handle for a resource in a mixed list carries its identity (`__resource_type`/`__resource_id`,
    * ARCH-RESOURCE-HANDLE); a resource itself carries none (DATA-EXPORT). */
  private def isResourceHandle(r: org.apache.spark.sql.Row): Boolean =
    r.schema != null && {
      val fs = r.schema.fieldNames.toSeq
      fs.contains("__resource_type") && fs.contains("__resource_id")
    }

  /** Render an engine resource handle to its `Type/id` reference. */
  private def handleRef(r: org.apache.spark.sql.Row): String =
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
