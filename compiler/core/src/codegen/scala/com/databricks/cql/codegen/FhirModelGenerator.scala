package com.databricks.cql.codegen

import java.io.File
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import javax.xml.parsers.DocumentBuilderFactory
import org.w3c.dom.{Element => DomElement, Node, NodeList}

import scala.collection.mutable

/** Build-time generator of the static FHIR R4 element model (`BUILD-FHIR-CODEGEN`,
  * `compiler/docs/data-model.md`). Parses the vendored FHIR R4 XSDs and emits a deterministic,
  * dependency-free Scala DATA model (`sema.generated.FhirR4Model`) of resources/datatypes/
  * primitives with their elements, cardinality, and `value[x]` choice membership.
  *
  * Lives in the `codegen` source set so it compiles BEFORE the `generateFhirModel` task runs (the
  * task is `JavaExec` over this source set's runtime classpath), avoiding the cycle that would
  * arise if the generator lived in `main` (which `dependsOn generateFhirModel`). The generated
  * Scala is a build artifact wired onto `main`'s scala srcDir — never committed (mirrors ELM via
  * `translateCorpus`); the XSDs ARE committed as a root-level model asset (`model-schemas/fhir-r4`,
  * provenance in its README), referenced via `rootProject.file` like the `cql/` corpus.
  *
  * Determinism (`TECH-DETERMINISM`): types are emitted sorted by name, elements in XSD document
  * order — byte-identical run to run.
  *
  * Args: `<xsdDir> <outDir>`. Parses `<xsdDir>/fhir-single.xsd` (self-contained: every
  * `complexType` in one file, no `xs:include` resolution needed). */
object FhirModelGenerator {

  /** The XSD namespace (every schema node is in it). */
  private val Xsd = "http://www.w3.org/2001/XMLSchema"

  /** Resources to emit, seeded from the resource types the conformance corpus retrieves
    * (`Corpus.scala`). Widening the model to another resource is a ONE-LINE
    * change here (add the name) + a re-run of `generateFhirModel` — never a code change
    * (`BUILD-FHIR-CODEGEN` scope). The referenced datatype/backbone closure is pulled in
    * automatically by [[closure]]. */
  private val AllowedResources: Set[String] = Set(
    "Patient", "Encounter", "Observation", "Condition", "Procedure", "Coverage",
    "ServiceRequest", "MedicationRequest", "MedicationStatement", "MedicationAdministration",
    // `Medication`: a value-set-filtered `[MedicationRequest: "vs"]` whose `medication[x]` is a
    // `Reference(Medication)` expands (in the translator) to a relationship-follow retrieve of the
    // referenced `Medication` resource so its `.code` can be matched against the value set.
    "MedicationDispense", "Medication", "Immunization", "AllergyIntolerance", "DiagnosticReport",
    // `DeviceRequest`: a device criterion retrieves `[DeviceRequest: "<VS>"]`.
    "Specimen", "Device", "DeviceRequest", "Location", "Practitioner", "Organization")

  def main(args: Array[String]): Unit = {
    require(args.length == 2, "usage: FhirModelGenerator <xsdDir> <outDir>")
    val xsdDir = new File(args(0))
    val outDir = new File(args(1))
    val single = new File(xsdDir, "fhir-single.xsd")
    require(single.isFile, s"vendored FHIR XSD not found: $single — see ${new File(xsdDir, "README.md")}")

    val all = parse(single)
    val emitted = closure(all, AllowedResources.filter(all.contains))
    val scala = render(all, emitted)

    val pkgDir = new File(outDir, "com/databricks/cql/sema/generated")
    pkgDir.mkdirs()
    val target = new File(pkgDir, "FhirR4Model.scala")
    Files.write(target.toPath, scala.getBytes(StandardCharsets.UTF_8))
    println(s"[generateFhirModel] wrote ${emitted.size} FHIR types -> $target")
  }

  // -- Parsed XSD model ------------------------------------------------------

  /** One element of a complexType. `choiceBase` is set when the element is a `value[x]` typed slot
    * (so `name` = `valueQuantity`, `choiceBase` = `value`, `fhirType` = `Quantity`). */
  private final case class Elem(name: String, fhirType: String, isList: Boolean, choiceBase: Option[String])

  /** A parsed FHIR complexType. `primitiveBase` is the underlying primitive kind (`dateTime`,
    * `boolean`, `code` for code-enums, …) when this type is a FHIR primitive. */
  private final case class Ty(name: String, base: Option[String], isPrimitive: Boolean,
                              primitiveBase: Option[String], elems: Vector[Elem])

  private def parse(xsd: File): Map[String, Ty] = {
    val dbf = DocumentBuilderFactory.newInstance()
    dbf.setNamespaceAware(true)
    // Hermetic parse: no DTD/entity fetches (the build host has no network).
    dbf.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false)
    val doc = dbf.newDocumentBuilder().parse(xsd)
    val complexTypes = childElems(doc.getDocumentElement, "complexType")
    complexTypes.flatMap(parseType).map(t => t.name -> t).toMap
  }

  private def parseType(ct: DomElement): Option[Ty] = {
    val name = ct.getAttribute("name")
    if (name.isEmpty) return None
    val ext = firstDescendant(ct, "extension")
    val base = ext.map(_.getAttribute("base")).filter(_.nonEmpty)
    // Primitive: a `value` attribute typed `<x>-primitive` (real primitives) or `<x>-list`
    // (code-bound enumerations, e.g. EncounterStatus → code-backed).
    val valueAttr = (ext.toSeq.flatMap(e => childElems(e, "attribute")) ++ childElems(ct, "attribute"))
      .find(_.getAttribute("name") == "value")
    val primBase = valueAttr.map(_.getAttribute("type")).collect {
      case t if t.endsWith("-primitive") => t.stripSuffix("-primitive")
      case t if t.endsWith("-list")      => "code" // a code-bound enumeration is string/code-backed
    }
    val elems = ext.orElse(Some(ct)).toVector.flatMap(parseElements)
    Some(Ty(name, base, isPrimitive = primBase.isDefined, primitiveBase = primBase, elems = elems))
  }

  /** All `xs:element`s under a node's `xs:sequence`/`xs:choice` (document order). A `xs:choice`
    * block's children are `value[x]` typed slots — tagged with the computed choice base. */
  private def parseElements(container: DomElement): Vector[Elem] = {
    val out = mutable.ArrayBuffer.empty[Elem]
    def walk(n: DomElement): Unit = childElems(n).foreach { c =>
      localName(c) match {
        case "sequence" => walk(c)
        case "choice"   =>
          val slots = childElems(c, "element").map(e => (e.getAttribute("name"), e.getAttribute("type")))
            .filter { case (nm, ty) => nm.nonEmpty && ty.nonEmpty }
          val base = choiceBaseOf(slots.map(_._1))
          slots.foreach { case (nm, ty) => out += Elem(nm, ty, isList = false, choiceBase = Some(base)) }
        case "element"  =>
          val nm = c.getAttribute("name"); val ty = c.getAttribute("type")
          if (nm.nonEmpty && ty.nonEmpty)
            out += Elem(nm, ty, isList = c.getAttribute("maxOccurs") == "unbounded", choiceBase = None)
        case _ => // annotation, attribute, etc.
      }
    }
    walk(container)
    out.toVector
  }

  /** The `value[x]` choice base for a set of typed slot names: their longest common prefix,
    * trimmed so the remainder of every slot starts with an uppercase type suffix
    * (`valueQuantity`/`valueString` → `value`; `effectiveDateTime`/`effectivePeriod` →
    * `effective`). */
  private def choiceBaseOf(slots: Seq[String]): String = {
    if (slots.isEmpty) return ""
    var p = slots.reduce(commonPrefix)
    while (p.nonEmpty && !slots.forall(s => s.length > p.length && s.charAt(p.length).isUpper))
      p = p.dropRight(1)
    p
  }
  private def commonPrefix(a: String, b: String): String = {
    val n = a.zip(b).takeWhile { case (x, y) => x == y }.length
    a.take(n)
  }

  // -- Allow-list closure ----------------------------------------------------

  /** The transitive closure of types to emit: the allow-listed resources plus every datatype /
    * backbone / primitive reachable through their elements (cycle-safe via the visited set). */
  private def closure(all: Map[String, Ty], roots: Set[String]): Set[String] = {
    val seen = mutable.Set.empty[String]
    val stack = mutable.Stack.empty[String]
    roots.foreach(stack.push)
    // Resources extend DomainResource → Resource; pull those in so inherited fields (id, extension)
    // are modeled too.
    while (stack.nonEmpty) {
      val n = stack.pop()
      if (!seen(n)) all.get(n).foreach { ty =>
        seen += n
        ty.base.filter(all.contains).foreach(stack.push)
        ty.elems.foreach(e => if (all.contains(e.fhirType)) stack.push(e.fhirType))
      }
    }
    seen.toSet
  }

  // -- Render ----------------------------------------------------------------

  private def isResource(all: Map[String, Ty], name: String): Boolean = {
    var cur = Option(name)
    val guard = mutable.Set.empty[String]
    while (cur.isDefined) {
      val n = cur.get
      if (n == "Resource") return true
      if (!guard.add(n)) return false
      cur = all.get(n).flatMap(_.base)
    }
    false
  }

  private def render(all: Map[String, Ty], emitted: Set[String]): String = {
    val sb = new StringBuilder
    sb.append(
      """// GENERATED by com.databricks.cql.codegen.FhirModelGenerator — DO NOT EDIT.
        |// Source: vendored FHIR R4 v4.0.1 XSD (model-schemas/fhir-r4/fhir-single.xsd).
        |// Regenerate: ./gradlew :compiler:generateFhirModel  (BUILD-FHIR-CODEGEN).
        |package com.databricks.cql.sema.generated
        |
        |/** Static FHIR R4 element model (pure data; Spark-free). The engine mapping layer
        |  * (`sema.FhirModel`) interprets it into `CqlType`/`Encoding` (`TYPE-FHIR-MODEL`). */
        |object FhirR4Model {
        |
        |  /** One element of a FHIR type. For a `value[x]` choice slot, `choiceBase` is the bare
        |    * element name (`value` for `valueQuantity`); otherwise `None`. */
        |  final case class Element(name: String, fhirType: String, isList: Boolean,
        |                           choiceBase: Option[String] = None)
        |
        |  /** A FHIR complexType: a resource, a datatype/backbone, or a primitive. `base` is the
        |    * XSD extension base (`DomainResource`, `Element`, `Quantity`, …); `primitiveBase` is the
        |    * underlying primitive kind (`dateTime`/`boolean`/`code`/…) when `isPrimitive`. `elements`
        |    * are this type's OWN elements (XSD document order) — inherited base elements are resolved
        |    * by the engine layer via `base`. */
        |  final case class FhirType(name: String, base: Option[String], isResource: Boolean,
        |                            isPrimitive: Boolean, primitiveBase: Option[String],
        |                            elements: Vector[Element])
        |
        |  val types: Map[String, FhirType] = Map(
        |""".stripMargin)

    emitted.toSeq.sorted.foreach { name =>
      val ty = all(name)
      sb.append("    ").append(quote(name)).append(" -> FhirType(")
      sb.append(quote(name)).append(", ")
      sb.append(opt(ty.base)).append(", ")
      sb.append(isResource(all, name)).append(", ")
      sb.append(ty.isPrimitive).append(", ")
      sb.append(opt(ty.primitiveBase)).append(", Vector(")
      sb.append(ty.elems.map(renderElem).mkString(", "))
      sb.append(")),\n")
    }

    sb.append("  )\n}\n")
    sb.toString
  }

  private def renderElem(e: Elem): String =
    s"Element(${quote(e.name)}, ${quote(e.fhirType)}, ${e.isList}, ${opt(e.choiceBase)})"

  private def quote(s: String): String = "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
  private def opt(o: Option[String]): String = o.fold("None")(s => s"Some(${quote(s)})")

  // -- DOM helpers -----------------------------------------------------------

  private def localName(n: Node): String = {
    val ln = n.getLocalName
    if (ln != null) ln else { val nn = n.getNodeName; nn.substring(nn.indexOf(':') + 1) }
  }

  private def childElems(n: Node): Vector[DomElement] = {
    val kids: NodeList = n.getChildNodes
    (0 until kids.getLength).iterator.map(kids.item)
      .collect { case e: DomElement if e.getNamespaceURI == Xsd || e.getNamespaceURI == null => e }
      .toVector
  }
  private def childElems(n: Node, local: String): Vector[DomElement] =
    childElems(n).filter(localName(_) == local)

  /** The first `extension`/named descendant anywhere under `n` (FHIR wraps it in
    * `complexContent`). */
  private def firstDescendant(n: DomElement, local: String): Option[DomElement] = {
    val direct = childElems(n)
    direct.find(localName(_) == local)
      .orElse(direct.iterator.flatMap(c => firstDescendant(c, local)).nextOption())
  }
}
