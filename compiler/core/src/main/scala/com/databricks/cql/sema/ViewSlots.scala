package com.databricks.cql.sema

import scala.util.DynamicVariable

/** `DATA-MODEL-SUBSET`: the `value[x]` slots lowering reads are the ones the bound data holds. A slot the
  * request's shapes leave out holds no value on any row, so a read that dispatches over a choice's slots
  * (`value is null`, a whole `value`, a type test or cast, a choice carrier) leaves it out instead of naming
  * a field the relation does not return. A choice with none of its slots in the shape fails the compile
  * before lowering ([[com.databricks.cql.passes.SchemaPaths]]).
  *
  * Lowering learns a value's FHIR type, not its path, where it dispatches, so a type's slots are the ones its
  * values carry anywhere in the request's shapes ([[ResourceShape.Checked.fieldsByType]]); on a resource they
  * are exact. With no shapes in scope (a lowering that binds no data, `describe`), every slot FHIR declares
  * is read.
  *
  * The shapes are scoped to one lowering, on its thread, by [[withShapes]]: lowering reaches the dispatch
  * through column lowering, which carries no environment. */
object ViewSlots {

  private val current = new DynamicVariable[Option[Map[String, Set[String]]]](None)

  /** Run `body` with `shapes` as the data the slots are read from; `None` reads every declared slot. */
  def withShapes[A](shapes: Option[Iterable[ResourceShape.Checked]])(body: => A): A =
    current.withValue(shapes.map(merge))(body)

  /** The typed slots of choice element `base` on FHIR type `typeName` that the bound data holds, in model
    * order ([[FhirModel.choiceSlots]] when no shapes are in scope). */
  def choiceSlots(typeName: String, base: String): Seq[String] = {
    val declared = FhirModel.choiceSlots(typeName, base)
    current.value match {
      case None         => declared
      case Some(byType) =>
        val held = byType.getOrElse(FhirModel.localName(typeName), Set.empty)
        declared.filter(held)
    }
  }

  private def merge(shapes: Iterable[ResourceShape.Checked]): Map[String, Set[String]] =
    shapes.flatMap(_.fieldsByType).groupMapReduce(_._1)(_._2)(_ ++ _)
}
