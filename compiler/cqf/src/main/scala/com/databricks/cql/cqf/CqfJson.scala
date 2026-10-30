package com.databricks.cql.cqf

import scala.jdk.CollectionConverters._

/** The normalization of a CQF engine value to stable, deterministic JSON (TECH-DETERMINISM, CONF-GROUND-TRUTH).
  *
  * The conformance differential's `JsonNormalize` handles the engine's Spark `Row` values and delegates every
  * other value here, so the oracle's side of the comparison and the evaluator's point answers render by ONE set
  * of rules (PROC-DRY, `DEC-SAME-ANSWER`). `recurse` is the caller's own normalization, used for nested values:
  * a list or tuple from the Spark side can hold `Row`s, which only the caller can render.
  *
  * Richer runtime types (Quantity, Interval, Code, Concept) render as below; anything not named renders by `toString`. */
object CqfJson {

  def value(v: Any): ujson.Value = render(v, value)

  def render(v: Any, recurse: Any => ujson.Value): ujson.Value = v match {
    case null                      => ujson.Null
    // Structured values (LANG-QUANTITY/LANG-INTERVAL): the same JSON object the engine's struct renders to,
    // keyed by the engine's struct field names, so the differential compares structurally.
    case q: org.opencds.cqf.cql.engine.runtime.Quantity =>
      ujson.Obj("value" -> recurse(q.getValue), "unit" -> recurse(Option(q.getUnit).getOrElse("1")))
    case iv: org.opencds.cqf.cql.engine.runtime.Interval =>
      ujson.Obj("low" -> recurse(iv.getStart), "high" -> recurse(iv.getEnd))
    // A Code or Concept (LANG-TERMINOLOGY) renders as the FHIR Coding / CodeableConcept struct the engine
    // carries it in (`FHIRHelpers.ToConcept` is an identity on that struct), with the concept's display as
    // the CodeableConcept's `text`.
    case c: org.opencds.cqf.cql.engine.runtime.Code => coding(c, recurse)
    case c: org.opencds.cqf.cql.engine.runtime.Concept =>
      ujson.Obj(
        "coding" -> ujson.Arr.from(Option(c.getCodes).map(_.asScala.toSeq).getOrElse(Nil).map(coding(_, recurse))),
        "text"   -> recurse(c.getDisplay))
    // Date/DateTime render precision-aware (`2012`, `2012-06`, `2012-01-15T10:30:00`) by their `toString`.
    case d: org.opencds.cqf.cql.engine.runtime.Date     => ujson.Str(d.toString)
    case dt: org.opencds.cqf.cql.engine.runtime.DateTime => ujson.Str(dt.toString)
    // A Tuple (LANG-QUERY-MULTISOURCE): an object keyed by the slot names (ujson objects compare as maps).
    case t: org.opencds.cqf.cql.engine.runtime.Tuple =>
      ujson.Obj.from(t.getElements.asScala.toSeq.map { case (k, fv) => k -> recurse(fv) })
    case b: java.lang.Boolean      => ujson.Bool(b)
    case i: java.lang.Integer      => ujson.Num(i.doubleValue)
    case l: java.lang.Long         => ujson.Num(l.doubleValue)
    case d: java.lang.Double       => ujson.Num(d)
    case f: java.lang.Float        => ujson.Num(f.doubleValue)
    case bd: java.math.BigDecimal  => ujson.Num(bd.doubleValue)
    case bd: scala.math.BigDecimal => ujson.Num(bd.doubleValue)
    // A Time lowers onto the SENTINEL date 1970-01-01 on the engine side (ColumnLowering.timeStruct); strip the
    // sentinel to render it time-only. Other strings, including canonical ISO dates, pass through.
    case s: String if s.startsWith("1970-01-01T") => ujson.Str(s.substring("1970-01-01T".length))
    case s: String                 => ujson.Str(s)
    // Ordered lists (LANG-LIST), order preserved.
    case a: Array[_]                => ujson.Arr(a.iterator.map(recurse).toSeq: _*)
    case s: scala.collection.Seq[_] => ujson.Arr(s.iterator.map(recurse).toSeq: _*)
    case l: java.util.List[_]       => ujson.Arr(l.asScala.iterator.map(recurse).toSeq: _*)
    // A FHIR primitive returned without an explicit FHIRHelpers conversion (`return E.status`): its value.
    case p: org.hl7.fhir.instance.model.api.IPrimitiveType[_] => ujson.Str(p.getValueAsString)
    // A raw FHIR resource (DATA-EXPORT): its `Type/id` identity, the same reference the engine's resource struct
    // renders to, so a `return E` / bare-`[Encounter]` define compares by identity, not by resource shape.
    case r: org.hl7.fhir.r4.model.Resource =>
      ujson.Str(s"${r.getResourceType.name}/${r.getIdElement.getIdPart}")
    case other                     => ujson.Str(other.toString)
  }

  private def coding(c: org.opencds.cqf.cql.engine.runtime.Code, recurse: Any => ujson.Value): ujson.Value =
    ujson.Obj("system" -> recurse(c.getSystem), "version" -> recurse(c.getVersion), "code" -> recurse(c.getCode),
      "display" -> recurse(c.getDisplay), "userSelected" -> ujson.Null)
}
