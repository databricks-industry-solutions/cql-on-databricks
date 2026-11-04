package com.databricks.cql.sema

import com.databricks.cql.sema.FhirShape.{Field, Leaf, Shape, Struct}
import com.databricks.cql.spark.SystemColumns

/** `DATA-MODEL-SUBSET`, `BIND-CONTRACT`: one environment's shape of one resource type, the columns its relation
  * returns, checked against the contract: a `resource` struct that is a subset of the superset, the FHIR R4 type
  * graph for the modeled resources ([[FhirModel]]), and optionally three root columns beside it.
  *
  * A shape is a subset when every field it holds is one FHIR defines at that point, with the same name, as a
  * list exactly when FHIR's element is `0..*`, and of the type FHIR's JSON gives it ([[FhirSchema]]: a
  * primitive is its JSON scalar, a complex type a struct). Fields may come in any order and any may be left
  * out, at any depth, except `id`. The type graph loops (`Reference → Identifier → Reference`, `Extension`
  * inside `Extension`), but a shape is a finite struct, so checking it needs no cuts. An Extension holds `url`
  * and any of FHIR's `value[x]` slots and `extension`, on any element. Two things FHIR defines are outside
  * the superset, and a shape holding them is refused: `contained` and extensions on primitive values
  * (`_birthDate`). Every problem is named at once.
  *
  * The `resource` struct holds FHIR's fields only: a `__*` field in it, such as the engine's `__resource_type`, is
  * refused, since the engine's lineage never reaches an implementer. Beside `resource` the relation may return
  * any of three root columns ([[AccessColumns]]): `patient_id`, a string or an integer, the subject key
  * (`DATA-SUBJECT-KEY`); `min_date` and `max_date`, dates, for the coarse date filter (`BIND-PREFILTER`). Any
  * other root column is refused, naming it. Spark-free: it reads the row type as text, the way Spark prints it
  * (`struct<resource:struct<id:string,…>,patient_id:string>`, `STRUCT<resource: STRUCT<id: STRING NOT NULL, …>>`). */
object ResourceShape {

  /** A checked shape. `struct` is the `resource` struct's FHIR fields as a [[FhirShape]] tree, so the path check ([[com.databricks.cql.passes.SchemaPaths]]) walks it like any shape. `fieldsByType`
    * maps each FHIR type to the fields its values carry anywhere in this shape, for the choice-slot lookup
    * lowering makes where it knows a value's type but not its path ([[ViewSlots]]). `access` is the
    * [[AccessColumns]] the relation returns. */
  final case class Checked(resourceType: String, struct: Struct, fieldsByType: Map[String, Set[String]],
                           access: Set[String] = Set.empty) {
    /** `DATA-SUBJECT-KEY`: the relation returns `patient_id`. */
    def hasPatientId: Boolean = access(PatientId)
    /** `BIND-PREFILTER`: the relation returns both coarse dates. */
    def hasDates: Boolean = access(MinDate) && access(MaxDate)


    /** The shape as text that does not depend on field order, for an identity (`CLIENT-RESOLVER-IDENTITY`):
      * `name:type` with fields sorted, a list as `[type]`, a primitive as its FHIR base. */
    lazy val canonical: String = {
      def text(shape: Shape): String = shape match {
        case Leaf(base)     => base
        case Struct(fields) =>
          fields.sortBy(_.name).map(f => s"${f.name}:${if (f.isList) s"[${text(f.shape)}]" else text(f.shape)}")
            .mkString("{", ",", "}")
      }
      text(struct)
    }
  }

  /** A shape that is not a subset of the superset, with every problem found. */
  final class ShapeError(val resourceType: String, val problems: Seq[String])
    extends IllegalArgumentException(
      s"the data shape for $resourceType is not a subset of FHIR R4 (DATA-MODEL-SUBSET): ${problems.mkString("; ")}")

  /** `BIND-CONTRACT`: the root column holding the resource, a struct. */
  val ResourceColumn = "resource"

  /** `DATA-SUBJECT-KEY`: the root column a relation may return as its subject key. */
  val PatientId = "patient_id"
  /** `BIND-PREFILTER`: the root columns that bound every date in a record, for the coarse date filter. */
  val MinDate = "min_date"
  val MaxDate = "max_date"

  /** `BIND-CONTRACT`: the root columns a relation may return beside `resource`, each with its allowed types. */
  val AccessColumns: Map[String, Set[String]] = Map(
    PatientId -> Set("string", "bigint", "int", "smallint", "tinyint"),
    MinDate   -> Set("date"),
    MaxDate   -> Set("date"))

  private val accessList = AccessColumns.keys.toSeq.sorted.map(c => s"`$c`").mkString(", ")
  private val accessRequirement = Map(PatientId -> "DATA-SUBJECT-KEY", MinDate -> "BIND-PREFILTER", MaxDate -> "BIND-PREFILTER")

  /** The parsed type text: a struct, an array, or any other type by its lower-cased name (`string`,
    * `bigint`, `decimal(10,2)`, `map<string,string>`). */
  private sealed trait TypeText
  private final case class TStruct(fields: Vector[(String, TypeText)]) extends TypeText
  private final case class TArray(elem: TypeText) extends TypeText
  private final case class TOther(name: String) extends TypeText

  private def show(t: TypeText): String = t match {
    case TStruct(_) => "a struct"
    case TArray(_)  => "a list"
    case TOther(n)  => n
  }

  /** Parse a shape from the Spark type text of a relation's row and check it. `resourceType` is matched to a
    * modeled resource in any case, so a view's name (`condition`, `DATA-ENVIRONMENT-LAYOUT`) names its type. */
  def parse(resourceType: String, typeText: String): Checked = {
    val local = FhirModel.localName(resourceType)
    val rt = FhirModel.resourceTypes.find(_.equalsIgnoreCase(local)).getOrElse(local)
    val parsed = try new TypeParser(typeText).whole() catch {
      case e: IllegalArgumentException => throw new ShapeError(rt, Seq(s"it is not a Spark type: ${e.getMessage}"))
    }
    parsed match {
      case s: TStruct => check(rt, s)
      case other      => throw new ShapeError(rt, Seq(s"it is ${show(other)}, not a struct"))
    }
  }

  private def check(rt: String, row: TStruct): Checked = {
    if (!FhirModel.resourceTypes.contains(rt))
      throw new ShapeError(rt, Seq(s"$rt is not a modeled FHIR resource"))
    val problems = Vector.newBuilder[String]
    val byType = scala.collection.mutable.Map.empty[String, Set[String]]

    val access = Set.newBuilder[String]
    def accessOf(name: String, t: TypeText): Unit = {
      val ok = AccessColumns(name)
      t match {
        case TOther(n) if ok(n) => access += name
        case other =>
          problems += s"`$rt.$name` is ${show(other)}; it is ${ok.toSeq.sorted.mkString(" or ")} (${accessRequirement(name)})"
      }
    }

    def structOf(fhirType: String, path: String, s: TStruct, top: Boolean): Struct = {
      byType(fhirType) = byType.getOrElse(fhirType, Set.empty) ++ s.fields.map(_._1)
      val allowed: Map[String, (String, Boolean)] =
        FhirModel.elements(fhirType).map(e => e.name -> (e.fhirType, e.isList)).toMap ++
          (if (fhirType == "Extension") Map("url" -> ("uri", false)) else Map.empty) ++
          (if (top) Map("resourceType" -> ("string", false)) else Map.empty)
      Struct(s.fields.flatMap { case (name, t) =>
        val at = s"$path.$name"
        if (top && SystemColumns.isReserved(name)) {
          problems += s"`$at` is the engine's own field; `$ResourceColumn` holds FHIR's fields only, and the engine " +
            "derives its lineage when it reads the view (BIND-CONTRACT)"
          None
        } else if (name == "contained") { problems += s"`$at`: contained resources are not supported by the compiler"; None }
        else if (name.startsWith("_")) {
          problems += s"`$at`: extensions on primitive values are not supported by the compiler"; None
        } else allowed.get(name) match {
          case None => problems += s"`$at` is not a FHIR R4 element of $fhirType"; None
          case Some((elemType, isList)) =>
            val elem = t match {
              case TArray(e) if isList => Some(e)
              case TArray(_)           => problems += s"`$at` is a list; FHIR's $fhirType.$name is single"; None
              case _ if isList         => problems += s"`$at` is single; FHIR's $fhirType.$name is a list"; None
              case other               => Some(other)
            }
            elem.flatMap(e => valueOf(elemType, at, e)).map(Field(name, _, isList))
        }
      })
    }

    def valueOf(fhirType: String, path: String, t: TypeText): Option[Shape] =
      FhirModel.primitiveBase(fhirType) match {
        case Some(base) =>
          val want = FhirShape.jsonScalar(base)
          if (t == TOther(want)) Some(Leaf(base))
          else { problems += s"`$path` is ${show(t)}; FHIR $fhirType is $want"; None }
        case None => t match {
          case s: TStruct => Some(structOf(fhirType, path, s, top = false))
          case other      => problems += s"`$path` is ${show(other)}; FHIR $fhirType is a struct"; None
        }
      }

    // The row: `resource`, and any of the access columns. Anything else is named.
    row.fields.foreach { case (name, _) =>
      if (name == ResourceColumn || AccessColumns.contains(name)) ()
      else if (SystemColumns.isReserved(name))
        problems += s"`$name` is the engine's own column; a view returns `$ResourceColumn`, and optionally " +
          s"$accessList, and the engine adds its columns when it reads the view (BIND-CONTRACT)"
      else problems += s"`$name` is not a column of the contract; a view returns `$ResourceColumn`, and optionally " +
        s"$accessList (BIND-CONTRACT)"
    }
    row.fields.foreach { case (name, t) => if (AccessColumns.contains(name)) accessOf(name, t) }
    val struct = row.fields.find(_._1 == ResourceColumn) match {
      case None =>
        problems += s"`$ResourceColumn` is missing; a view returns the resource as a struct column `$ResourceColumn` " +
          "(BIND-CONTRACT)"
        Struct(Vector.empty)
      case Some((_, s: TStruct)) =>
        val built = structOf(rt, rt, s, top = true)
        if (built.field("id").isEmpty && !s.fields.exists(_._1 == "id"))
          problems += s"`$rt.id` is missing; every resource needs its id"
        built
      case Some((_, other)) =>
        problems += s"`$ResourceColumn` is ${show(other)}, not a struct (BIND-CONTRACT)"
        Struct(Vector.empty)
    }
    val found = problems.result()
    if (found.nonEmpty) throw new ShapeError(rt, found)
    Checked(rt, struct, byType.toMap, access.result())
  }

  /** A reader of Spark's type text: `struct<…>` and `array<…>` with fields as `name:type` or `name: type`,
    * names bare or back-quoted, keywords in any case, `NOT NULL` and `COMMENT '…'` after a field's type.
    * Any other type is kept as its lower-cased text, `long` read as `bigint` and `integer` as `int`. */
  private final class TypeParser(text: String) {
    private var i = 0

    def whole(): TypeText = {
      val t = typ()
      ws()
      if (i != text.length) fail("unexpected text")
      t
    }

    private def fail(what: String): Nothing =
      throw new IllegalArgumentException(s"$what at position $i of `${text.take(80)}${if (text.length > 80) "…" else ""}`")
    private def ws(): Unit = while (i < text.length && text(i).isWhitespace) i += 1
    private def peek: Char = { ws(); if (i < text.length) text(i) else '\u0000' }
    private def keyword(k: String): Boolean = {
      ws()
      val ok = text.regionMatches(true, i, k, 0, k.length) &&
        (i + k.length == text.length || !text(i + k.length).isLetterOrDigit)
      if (ok) i += k.length
      ok
    }
    private def expect(c: Char): Unit = if (peek == c) i += 1 else fail(s"expected '$c'")

    private def name(): String = {
      ws()
      if (peek == '`') {
        val end = text.indexOf('`', i + 1)
        if (end < 0) fail("unclosed `")
        val n = text.substring(i + 1, end); i = end + 1; n
      } else {
        val start = i
        while (i < text.length && (text(i).isLetterOrDigit || text(i) == '_')) i += 1
        if (i == start) fail("expected a name")
        text.substring(start, i)
      }
    }

    private def typ(): TypeText =
      if (keyword("struct")) {
        expect('<')
        val fields = Vector.newBuilder[(String, TypeText)]
        if (peek != '>') {
          var more = true
          while (more) {
            val n = name()
            if (peek == ':') i += 1
            val t = typ()
            if (keyword("not")) { if (!keyword("null")) fail("expected NULL") }
            if (keyword("comment")) { ws(); expect('\''); val end = text.indexOf('\'', i); if (end < 0) fail("unclosed comment"); i = end + 1 }
            fields += n -> t
            more = peek == ','
            if (more) i += 1
          }
        }
        expect('>')
        TStruct(fields.result())
      } else if (keyword("array")) {
        expect('<'); val t = typ(); expect('>'); TArray(t)
      } else {
        ws()
        val start = i
        var depth = 0
        while (i < text.length && (depth > 0 || (text(i) != ',' && text(i) != '>' && !text(i).isWhitespace))) {
          if (text(i) == '<' || text(i) == '(') depth += 1
          if (text(i) == '>' || text(i) == ')') depth -= 1
          i += 1
        }
        if (i == start) fail("expected a type")
        text.substring(start, i).toLowerCase match {
          case "long"    => TOther("bigint")
          case "integer" => TOther("int")
          case other     => TOther(other)
        }
      }
  }
}
