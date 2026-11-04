package com.databricks.cql.passes

import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.ir._
import com.databricks.cql.sema.{FhirModel, ResourceShape}
import com.databricks.cql.sema.FhirShape.{Leaf, Shape, Struct}

import scala.collection.mutable

/** `DATA-PATH-IN-SCHEMA`, `DATA-MODEL-SUBSET`: a FHIR element the bound data leaves out fails the compile,
  * naming the path, instead of reading as a missing field when the SQL runs (CORE-NO-SILENT-MISLOWER).
  *
  * The superset is FHIR R4 for the modeled resources ([[FhirModel]]). The bound data's shape of each resource
  * type comes from the request ([[ResourceShape]]). The check follows each navigation from what it starts at,
  * a retrieve, a query alias, a `let` or a define, through that shape, so it sees what the data holds where
  * the navigation lands. It fails on:
  *  - an element FHIR R4 defines there that the shape leaves out: not in this environment;
  *  - a `value[x]` choice none of whose typed slots the shape holds: not in this environment;
  *  - `contained`, or an extension on a primitive value: FHIR defines them, the compiler does not support them.
  * A retrieve's own filters count as navigations: its code, its date, a period pre-filter and a profile's
  * `meta.profile`.
  * An element FHIR does not define (a profile element, a primitive's `.value`) is left to lowering. Where the
  * start is not traceable to a retrieve, and when the lowering binds no data (`describe`), the navigation is
  * checked against FHIR R4 alone.
  *
  * Pure and Spark-free; runs over the same linked, normalized library lowering lowers. */
object SchemaPaths {

  /** An element's FHIR type, the bound data's shape at that point (`None`: checked against FHIR R4 alone), and
    * its path from the resource, for the message. */
  private final case class At(fhirType: String, shape: Option[Shape], path: String)

  /** Check every navigation in `lib`. `shapes` is the bound data's shape per resource type, or `None` when the
    * lowering binds no data. */
  def check(lib: Library, typeOf: Expr => CqlType, shapes: Option[Map[String, ResourceShape.Checked]]): Unit = {
    val defines = lib.statements.collect { case e: ExpressionDef => e.name -> e.expression }.toMap
    val defineShapes = mutable.Map.empty[String, Option[At]]

    def ofType(name: String): Option[At] = {
      val t = FhirModel.localName(name)
      if (FhirModel.isModeled(t)) Some(At(t, None, t)) else None
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
        if (FhirModel.isModeled(t)) Some(At(t, shapes.flatMap(_.get(t)).map(_.struct), t)) else None
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
      case r: Retrieve =>
        // The fields a retrieve's own filters read: its code, its date, a period pre-filter and, for a profile,
        // `meta.profile` (LANG-RETRIEVE-PROFILE). No CQL navigation names them, and the scan reads them.
        shape(r, env).foreach { at =>
          (r.codeProperty.toSeq ++ r.dateProperty ++ r.periodScanFilter.map(_._1) ++
            r.profileConstraint.map(_ => "meta.profile")).foreach(navigate(at, _))
        }
        r.children.foreach(walk(_, env))
      case other => other.children.foreach(walk(_, env))
    }

    defines.values.foreach(walk(_, Map.empty))
  }

  private def navigate(start: At, path: String): Option[At] =
    path.split('.').foldLeft(Option(start))((at, seg) => at.flatMap(step(_, seg)))

  private def step(at: At, seg: String): Option[At] = {
    val here = s"${at.path}.$seg"
    def next(shape: Option[Shape]): Option[At] =
      FhirModel.elementType(at.fhirType, seg).map(t => At(FhirModel.localName(t), shape, here))
    val definedHere = FhirModel.elementType(at.fhirType, seg).isDefined
    if (seg == "contained" && definedHere) unsupported(here, "contained resources are")
    else if (FhirModel.primitiveBase(at.fhirType).isDefined) {
      if (definedHere) unsupported(here, "extensions on primitive values are")
      None
    } else at.shape match {
      case None => next(None)
      case Some(s: Struct) =>
        s.field(seg) match {
          case Some(f) => next(Some(f.shape))
          case None =>
            val slots = FhirModel.choiceSlots(at.fhirType, seg)
            if (slots.nonEmpty) {
              // A value[x] base: lowering dispatches it to the typed slots the shape holds, so it must hold one.
              if (!slots.exists(sl => s.field(sl).isDefined))
                missing(here, s"the bound data holds none of its typed slots (${slots.mkString(", ")})")
              None
            } else {
              if (definedHere) missing(here, "the bound data leaves it out")
              None
            }
        }
      case Some(Leaf(_)) => None
    }
  }

  private def missing(path: String, why: String): Nothing =
    throw UnsupportedNode("Property",
      s"`$path` is not in this environment: $why. Add it to the data view, as nulls where there is no data " +
        "(DATA-MODEL-SUBSET, DATA-PATH-IN-SCHEMA)")

  private def unsupported(path: String, what: String): Nothing =
    throw UnsupportedNode("Property", s"`$path`: $what not supported by the compiler (DATA-MODEL-SUBSET)")
}
