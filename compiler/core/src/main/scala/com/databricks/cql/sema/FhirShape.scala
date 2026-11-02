package com.databricks.cql.sema

import com.databricks.cql.sema.generated.FhirR4Model
import com.databricks.cql.sema.generated.FhirR4Model.{Element, FhirType}

import java.util.concurrent.ConcurrentHashMap

/** The physical shape of a FHIR resource: FHIR R4 JSON with some fields left out, never moved or renamed
  * (`DATA-MODEL-SCHEMA`, `DATA-MODEL-CUTS`, `compiler/docs/data-model.md`). Any FHIR JSON reads into it;
  * a left-out field is dropped. Spark-free, so the compile-time path check ([[com.databricks.cql.passes.SchemaPaths]])
  * and the Spark schema ([[FhirSchema]]) read the same rules.
  *
  * There is no depth cap. Every element of every modeled type is present at any depth, except:
  *  - at a loop cut ([[Cuts]]), the target type loses the field that closes the loop;
  *  - `extension` is kept on resources only, with one nested level, and its `value[x]` keeps one slot per
  *    [[OpenValueTypes|open type]];
  *  - `modifierExtension` is the nested Extension shape on a resource and its `url` only on a backbone element;
  *  - `contained` is left out (FHIR forbids nesting it, so it is a width question, not a loop).
  *
  * An unlisted loop in the type graph fails the build of a shape, naming the edge, so a new resource type
  * whose model brings a loop needs an entry in [[Cuts]]. */
object FhirShape {

  sealed trait Shape
  /** A FHIR primitive, by its primitive base (`boolean`, `integer`, `decimal`, `dateTime`, `code`, …). */
  final case class Leaf(primitive: String) extends Shape
  /** A complex type: its kept fields, in model order. */
  final case class Struct(fields: Vector[Field]) extends Shape {
    def field(name: String): Option[Field] = fields.find(_.name == name)
  }
  final case class Field(name: String, shape: Shape, isList: Boolean)

  /** The loop cuts (`DATA-MODEL-CUTS`): at edge `(type, field)`, the field's type loses the listed field,
    * the one that closes the loop. With extensions and `contained` set aside, FHIR R4 has one datatype loop,
    * `Reference.identifier → Identifier.assigner → Reference`; it is cut where it is rarely read, so a
    * Reference keeps its identifier everywhere except inside an assigner. An Extension nests one level. */
  val Cuts: Map[(String, String), Set[String]] = Map(
    ("Identifier", "assigner") -> Set("identifier"),
    ("Extension", "extension") -> Set("extension"))

  /** The open types an extension's `value[x]` keeps a slot for (`DATA-MODEL-SCHEMA`), in slot order. Each
    * is a `value<Type>` slot (`code` → `valueCode`). Not the full 50-type `Extension.value[x]`: every slot is
    * a full datatype, and all of them would make one extension wider than a resource. */
  val OpenValueTypes: Seq[String] = Seq(
    "boolean", "integer", "decimal", "string", "code", "uri", "url", "canonical",
    "dateTime", "date", "time", "instant",
    "Coding", "CodeableConcept", "Quantity", "Period", "Reference", "Identifier")

  private def types: Map[String, FhirType] = FhirR4Model.types

  private val resources = new ConcurrentHashMap[String, Struct]()
  private val datatypes = new ConcurrentHashMap[String, Option[Shape]]()

  /** The shape of resource `resourceType` (localized), with the `resourceType` discriminator first. */
  def resource(resourceType: String): Struct =
    resources.computeIfAbsent(FhirModel.localName(resourceType), { rt =>
      val base = struct(rt, Nil, Set.empty)
      if (base.field("resourceType").isDefined) base
      else Struct(Field("resourceType", Leaf("string"), isList = false) +: base.fields)
    })

  /** The shape of FHIR type `typeName` (localized) where no cut applies: a resource's shape, a datatype's or
    * backbone's struct, or a primitive's leaf. `None` for an unmodeled type or one that keeps no fields. */
  def ofType(typeName: String): Option[Shape] = {
    val t = FhirModel.localName(typeName)
    types.get(t) match {
      case Some(ft) if ft.isResource => Some(resource(t))
      case Some(_) => datatypes.computeIfAbsent(t, { n =>
        if (isPrimitive(n)) Some(Leaf(primBase(n)))
        else if (n == "Extension") Some(extension(Set.empty, Nil))
        else Some(struct(n, Nil, Set.empty)).filter(_.fields.nonEmpty)
      })
      case None => None
    }
  }

  private def elementsOf(typeName: String): Vector[Element] = computeElements(typeName, Set.empty)
  private def computeElements(typeName: String, seen: Set[String]): Vector[Element] =
    types.get(typeName) match {
      case Some(t) if !seen(typeName) =>
        t.base.toVector.flatMap(b => computeElements(b, seen + typeName)) ++ t.elements
      case _ => Vector.empty
    }

  private def isPrimitive(fhirType: String): Boolean = types.get(fhirType).exists(_.isPrimitive)
  private def primBase(fhirType: String): String = types.get(fhirType).flatMap(_.primitiveBase).getOrElse("string")
  private def isResource(typeName: String): Boolean = types.get(typeName).exists(_.isResource)

  /** The path of types being built, each with the fields it lost at a cut: re-entering the same pair
    * would never end. */
  private type Stack = List[(String, Set[String])]

  /** The Extension shape (`DATA-MODEL-SCHEMA`): `url`, one slot per open type, then `extension` unless the
    * cut removed it. The XSD carries `url` as an attribute, so it is added here. */
  private def extension(drop: Set[String], stack: Stack): Struct = {
    val values = OpenValueTypes.flatMap { t =>
      shapeOf(t, ("Extension", drop) :: stack, Set.empty).map(s => Field("value" + t.capitalize, s, isList = false))
    }
    val nested =
      if (drop("extension")) None
      else Some(Field("extension", extension(Cuts(("Extension", "extension")), ("Extension", drop) :: stack), isList = true))
    Struct((Field("url", Leaf("uri"), isList = false) +: values.toVector) ++ nested)
  }

  /** The shape of a value of FHIR type `fhirType` at this point: a leaf for a primitive, else its struct, or
    * `None` when the struct keeps no fields (`DATA-NO-EMPTY-STRUCT`). */
  private def shapeOf(fhirType: String, stack: Stack, drop: Set[String]): Option[Shape] =
    if (isPrimitive(fhirType)) Some(Leaf(primBase(fhirType)))
    else if (fhirType == "Extension") Some(extension(drop, stack))
    else if (types.contains(fhirType)) Some(struct(fhirType, stack, drop)).filter(_.fields.nonEmpty)
    else Some(Leaf("string")) // an unmodeled complex type; none occurs in the generated closure

  private def struct(typeName: String, stack: Stack, drop: Set[String]): Struct = {
    if (stack.contains((typeName, drop)))
      throw new IllegalStateException(
        s"DATA-MODEL-CUTS: the FHIR type loop ${(stack.reverse.map(_._1) :+ typeName).mkString(" → ")} " +
          s"is not cut; add an entry to FhirShape.Cuts")
    val here = (typeName, drop) :: stack
    Struct(elementsOf(typeName).filterNot(e => drop(e.name)).flatMap(e => field(typeName, e, here)))
  }

  private def field(owner: String, e: Element, stack: Stack): Option[Field] = {
    val shape: Option[Shape] = e.name match {
      case "contained"                              => None
      case "extension" if isResource(owner)         => Some(extension(Set.empty, stack))
      case "extension"                              => None
      case "modifierExtension" if isResource(owner) => Some(extension(Cuts(("Extension", "extension")), stack))
      case "modifierExtension"                      => Some(Struct(Vector(Field("url", Leaf("uri"), isList = false))))
      case _ => shapeOf(e.fhirType, stack, Cuts.getOrElse((owner, e.name), Set.empty))
    }
    shape.map(Field(e.name, _, e.isList))
  }
}
