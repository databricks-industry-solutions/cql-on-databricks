package com.databricks.cql.passes

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._
import com.databricks.cql.sema.{FhirModel, FhirShape}
import com.databricks.cql.sema.FhirShape.{Leaf, Shape, Struct}

import scala.collection.mutable

/** `DATA-PATH-IN-SCHEMA`: a FHIR element the data model leaves out fails the compile, naming the rule,
  * instead of reading as a missing field when the SQL runs (CORE-NO-SILENT-MISLOWER). The data model is
  * [[FhirShape]]: FHIR R4 with loop cuts and bounded extensions (`DATA-MODEL-SCHEMA`, `DATA-MODEL-CUTS`).
  *
  * It follows each navigation from what it starts at, a retrieve, a query alias, a `let` or a define, through
  * the shape, so a cut is seen where it lands: inside `Identifier.assigner` a Reference has no `identifier`,
  * and a nested extension has no `extension`. Where the start is not traceable it uses the type's shape with
  * no cut applied. It fails only on an element FHIR R4 defines on that type and the shape leaves out; an
  * element FHIR does not define (a profile element, a primitive's `.value`) is left to lowering.
  *
  * Pure and Spark-free; runs over the same linked, normalized library lowering lowers. */
object SchemaPaths {

  /** An element's FHIR type and its shape at that point. */
  private final case class At(fhirType: String, shape: Shape)

  def check(lib: Library, typeOf: Expr => CqlType): Unit = {
    val defines = lib.statements.collect { case e: ExpressionDef => e.name -> e.expression }.toMap
    val defineShapes = mutable.Map.empty[String, Option[At]]

    def ofType(name: String): Option[At] = {
      val t = FhirModel.localName(name)
      if (FhirModel.isModeled(t)) FhirShape.ofType(t).map(At(t, _)) else None
    }

    def fromType(e: Expr): Option[At] = typeOf(e) match {
      case CqlType.NamedType(n)                   => ofType(n)
      case CqlType.ListType(CqlType.NamedType(n)) => ofType(n)
      case _                                      => None
    }

    def ofDefine(name: String): Option[At] = defineShapes.get(name) match {
      case Some(at) => at
      case None =>
        val at = shape(defines(name), Map.empty)
        defineShapes(name) = at
        at
    }

    def bind(q: Query, env: Map[String, Option[At]]): Map[String, Option[At]] = {
      val sources = env ++ q.sources.map(s => s.alias -> shape(s.expr, env)) ++
        q.relationships.map(r => r.alias -> shape(r.expr, env))
      q.lets.foldLeft(sources)((acc, l) => acc + (l.identifier -> shape(l.expr, acc)))
    }

    // The shape of one ELEMENT of `e` (a list's element: CQL navigation flattens lists).
    def shape(e: Expr, env: Map[String, Option[At]]): Option[At] = e match {
      case r: Retrieve =>
        val t = FhirModel.localName(r.dataType)
        if (FhirModel.isModeled(t)) Some(At(t, FhirShape.resource(t))) else None
      case AliasRef(a)    => env.getOrElse(a, None)
      case QueryLetRef(n) => env.getOrElse(n, None)
      case p: Property    => root(p, env).flatMap(navigate(_, p.path))
      case q: Query =>
        q.returnExpr match {
          case Some(r) => shape(r, bind(q, env))
          case None if q.sources.size == 1 && q.aggregate.isEmpty => shape(q.sources.head.expr, env)
          case None => None
        }
      case SingletonFrom(o) => shape(o, env)
      case First(o)         => shape(o, env)
      case Last(o)          => shape(o, env)
      case Distinct(o)      => shape(o, env)
      case Union(l, r) =>
        val (a, b) = (shape(l, env), shape(r, env))
        if (a == b) a else None
      case ExpressionRef(n, None) if defines.contains(n) => ofDefine(n)
      case other => fromType(other)
    }

    def root(p: Property, env: Map[String, Option[At]]): Option[At] = p.source match {
      case Some(s) => shape(s, env)
      case None    => p.scope.flatMap(env.get).flatten
    }

    def walk(node: IrNode, env: Map[String, Option[At]]): Unit = node match {
      case q: Query =>
        val inner = bind(q, env)
        q.sources.foreach(s => walk(s.expr, env))
        q.relationships.foreach { r => walk(r.expr, env); walk(r.suchThat, inner) }
        q.lets.foreach(l => walk(l.expr, inner))
        q.where.foreach(walk(_, inner))
        q.returnExpr.foreach(walk(_, inner))
        q.sort.foreach(_.expr.foreach(walk(_, inner)))
        q.aggregate.foreach { a => walk(a.starting, inner); walk(a.body, inner) }
      case p: Property =>
        shape(p, env)
        p.source.foreach(walk(_, env))
      case other => other.children.foreach(walk(_, env))
    }

    defines.values.foreach(walk(_, Map.empty))
  }

  private def navigate(start: At, path: String): Option[At] =
    path.split('.').foldLeft(Option(start))((at, seg) => at.flatMap(step(_, seg)))

  private def step(at: At, seg: String): Option[At] = at.shape match {
    case s: Struct =>
      s.field(seg) match {
        case Some(f) => FhirModel.elementType(at.fhirType, seg).map(t => At(FhirModel.localName(t), f.shape))
        case None =>
          val slots = FhirModel.choiceSlots(at.fhirType, seg)
          if (slots.nonEmpty) {
            // A value[x] base: lowering dispatches it to a typed slot, so the shape must keep one.
            if (!slots.exists(sl => s.field(sl).isDefined))
              outside(at.fhirType, seg, s"none of its typed slots are kept here")
            None
          } else {
            if (FhirModel.navType(at.fhirType, seg).isDefined) outside(at.fhirType, seg, reason(at.fhirType, seg))
            None
          }
      }
    case Leaf(_) =>
      if (FhirModel.navType(at.fhirType, seg).isDefined)
        outside(at.fhirType, seg, "a primitive value keeps no extensions")
      None
  }

  private def reason(fhirType: String, seg: String): String = (fhirType, seg) match {
    case ("Extension", "extension") => "an extension nests one level"
    case (_, "extension")           => "extensions are kept on resources only"
    case (_, "contained")           => "contained resources are not kept"
    case ("Reference", "identifier") =>
      "the Reference → Identifier → Reference loop is cut at Identifier.assigner"
    case _ => "it is not kept"
  }

  private def outside(fhirType: String, seg: String, why: String): Nothing =
    throw UnsupportedNode("Property",
      s"`$fhirType.$seg` is outside the data model: $why (DATA-PATH-IN-SCHEMA, compiler/docs/data-model.md)")
}
