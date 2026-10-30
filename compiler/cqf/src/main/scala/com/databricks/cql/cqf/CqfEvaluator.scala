package com.databricks.cql.cqf

import ca.uhn.fhir.context.FhirContext
import ca.uhn.fhir.parser.DataFormatException
import com.databricks.cql.translate.SuppliedLibrarySourceProvider
import org.cqframework.cql.cql2elm.{CqlCompilerException, CqlCompilerOptions, LibraryManager, ModelManager}
import org.cqframework.cql.elm.serializing.DefaultElmLibraryWriterProvider
import org.hl7.elm.r1.{FunctionDef, IntervalTypeSpecifier, NamedTypeSpecifier, VersionedIdentifier}
import org.hl7.fhir.instance.model.api.IBaseResource
import org.opencds.cqf.cql.engine.data.{CompositeDataProvider, DataProvider}
import org.opencds.cqf.cql.engine.execution.{CqlEngine, Environment, EvaluationExpressionRef, EvaluationParams}
import org.opencds.cqf.cql.engine.fhir.model.R4FhirModelResolver
import org.opencds.cqf.cql.engine.runtime.{Date, DateTime, Interval}

import java.security.MessageDigest
import java.time.{LocalDateTime, OffsetDateTime, ZoneOffset}
import scala.jdk.CollectionConverters._

/** What a closure retrieves: the FHIR resource types its retrieves name and the value sets it declares. A caller
  * reads exactly these for its patients (`RUN-POINT-CQF`). */
final case class CqfRequirements(resourceTypes: Seq[String], valueSets: Seq[String])

/** One patient: its id and every resource of the types the closure retrieves, as FHIR R4 JSON. */
final case class CqfPatient(id: String, resources: Seq[String])

/** RUN-POINT-CQF, `DEC-POINT-UC-FUNCTION` — evaluate a library's Patient-context defines for a few patients with the
  * CQF CQL engine, in memory, over resources the caller extracted.
  *
  * **The closure arrives as CQL and is translated here**, once per closure, with the compiler's own translator
  * options (`CqlToElmCli.translateText`: annotations and result types on, and only the supplied library sources,
  * FHIRHelpers among them when the closure includes it, LANG-FHIRHELPERS-FROM-CONTENT). It cannot arrive as ELM: cqframework 4.8's ELM JSON reader restores no result types, and the engine
  * needs them (POINT-CQF-UC-FUNCTION, 2026-10-08). Translated closures are kept, least recently used first out,
  * up to `maxClosures`.
  *
  * **The answers are the conformance oracle's:** the same in-memory providers (`InMemoryFhirRetrieveProvider`,
  * `InMemoryTerminologyProvider`) and the same value normalization (`CqfJson`) as `GroundTruthRunner`
  * (`DEC-SAME-ANSWER`).
  *
  * The measurement period is the only parameter, given as ISO bounds and built in the type the root declares
  * (`Interval<DateTime>` or `Interval<Date>`, both ends inclusive, the canonical bounds of `RUN-EVAL-ID-INPUTS`). It
  * is given to the root and, by qualified name, to every library in the closure. Thread-safe. */
final class CqfEvaluator(maxClosures: Int = 32) {
  import CqfEvaluator.Closure

  private val fhir = FhirContext.forR4()
  private val closures = new java.util.LinkedHashMap[String, Closure](16, 0.75f, true)

  /** The resource types and value sets `root`'s closure retrieves. */
  def requirements(root: String, sources: Seq[String]): CqfRequirements = closure(root, sources).requirements

  /** Every Patient-context define of `root` for each patient, by define name, as `CqfJson`. Fails on the first
    * patient whose resources hold no Patient with its id, or whose evaluation raises: a partial answer would read
    * as a complete one. */
  def evaluate(root: String, sources: Seq[String], patients: Seq[CqfPatient],
               expansions: Map[String, Set[(String, String)]],
               period: Option[(String, String)]): Seq[(String, Map[String, ujson.Value])] = {
    val c = closure(root, sources)
    val parameters = periodParameters(c, period)
    val terminology = new InMemoryTerminologyProvider(expansions)
    patients.map { p =>
      val parser = fhir.newJsonParser()
      val resources: Seq[IBaseResource] = p.resources.map { r =>
        try parser.parseResource(r): IBaseResource
        catch { case e: DataFormatException =>
          throw new IllegalArgumentException(s"patient ${p.id}: a resource is not FHIR R4 JSON: ${e.getMessage}") }
      }
      if (!resources.exists(r => r.fhirType == "Patient" && r.getIdElement.getIdPart == p.id))
        throw new IllegalArgumentException(s"patient ${p.id}: no Patient resource with that id among its resources")
      val data = new CompositeDataProvider(new R4FhirModelResolver(), new InMemoryFhirRetrieveProvider(resources, expansions))
      val engine = new CqlEngine(new Environment(c.manager, Map("http://hl7.org/fhir" -> (data: DataProvider)).asJava,
        terminology))
      val b = new EvaluationParams.Builder()
      b.library(c.root, new EvaluationParams.LibraryParams(c.defines.map(new EvaluationExpressionRef(_)).asJava))
      b.setContextParameter(new kotlin.Pair[String, Object]("Patient", p.id))
      b.setParameters(parameters.asJava)
      val result = engine.evaluate(b.build())
      if (result.hasExceptions)
        throw new IllegalStateException(s"patient ${p.id}: ${result.getExceptions.asScala.values.map(_.getMessage).mkString("; ")}")
      val values = result.getResults.asScala.collect { case (vid, r) if vid.getId == c.root.getId =>
        r.getExpressionResults.asScala.map { case (name, er) => name -> CqfJson.value(er.getValue) }.toMap
      }.flatten.toMap
      p.id -> values
    }
  }

  private def closure(root: String, sources: Seq[String]): Closure = {
    val key = sha256(root + "\u0000" + sources.mkString("\u0000"))
    closures.synchronized {
      Option(closures.get(key)).getOrElse {
        val c = translate(root, sources)
        closures.put(key, c)
        while (closures.size > maxClosures) closures.remove(closures.keySet.iterator.next())
        c
      }
    }
  }

  private val Header = """(?m)^\s*library\s+"?([^\s"']+)"?\s+version\s+'([^']*)'""".r

  private def translate(root: String, sources: Seq[String]): Closure = {
    val options = CqlCompilerOptions.defaultOptions()
    options.getOptions.add(CqlCompilerOptions.Options.EnableAnnotations)
    options.getOptions.add(CqlCompilerOptions.Options.EnableResultTypes)
    val manager = new LibraryManager(new ModelManager(), options)
    manager.getLibrarySourceLoader.registerProvider(new SuppliedLibrarySourceProvider(sources))
    val headers = sources.flatMap(s => Header.findFirstMatchIn(s).map(m => m.group(1) -> m.group(2)))
    val version = headers.collectFirst { case (`root`, v) => v }
      .getOrElse(throw new IllegalArgumentException(s"no CQL among the sources declares library $root"))
    val rootId = new VersionedIdentifier().withId(root).withVersion(version)
    val errors = new java.util.ArrayList[CqlCompilerException]()
    val compiled = manager.resolveLibrary(rootId, errors)
    val fatal = errors.asScala.filter(_.getSeverity == CqlCompilerException.ErrorSeverity.Error)
    if (fatal.nonEmpty)
      throw new IllegalArgumentException(s"$root did not translate: ${fatal.map(_.getMessage).mkString("; ")}")
    val library = compiled.getLibrary
    val defines = Option(library.getStatements).map(_.getDef.asScala.toSeq).getOrElse(Seq.empty)
      .filter(d => !d.isInstanceOf[FunctionDef] && Option(d.getContext).getOrElse("Patient") == "Patient" &&
        d.getName != "Patient")
      .map(_.getName)
    val periodPoint = Option(library.getParameters).map(_.getDef.asScala.toSeq).getOrElse(Seq.empty)
      .find(_.getName == "Measurement Period").map { p =>
        p.getParameterTypeSpecifier match {
          case i: IntervalTypeSpecifier => i.getPointType match {
            case n: NamedTypeSpecifier if Set("DateTime", "Date")(n.getName.getLocalPart) => n.getName.getLocalPart
            case other => throw new IllegalArgumentException(s"$root: Measurement Period over $other is not supported")
          }
          case other => throw new IllegalArgumentException(s"$root: Measurement Period declared as $other is not supported")
        }
      }
    Closure(manager, rootId, defines, headers.map(_._1).distinct, periodPoint, requirementsOf(manager))
  }

  /** The retrieved resource types and declared value sets of every library the translation compiled. */
  private def requirementsOf(manager: LibraryManager): CqfRequirements = {
    val writer = DefaultElmLibraryWriterProvider.INSTANCE.create("application/elm+json")
    val types = scala.collection.mutable.SortedSet[String]("Patient")
    val valueSets = scala.collection.mutable.SortedSet[String]()
    manager.getCompiledLibraries.values.asScala.foreach { compiled =>
      def walk(v: ujson.Value): Unit = v match {
        case o: ujson.Obj =>
          if (o.value.get("type").contains(ujson.Str("Retrieve")))
            o.value.get("dataType").collect { case ujson.Str(t) => types += t.substring(t.lastIndexOf('}') + 1) }
          o.value.values.foreach(walk)
        case a: ujson.Arr => a.value.foreach(walk)
        case _ =>
      }
      walk(ujson.read(writer.writeAsString(compiled.getLibrary)))
      Option(compiled.getLibrary.getValueSets).foreach(_.getDef.asScala.foreach(d => valueSets += d.getId))
    }
    CqfRequirements(types.toSeq, valueSets.toSeq)
  }

  private def periodParameters(c: Closure, period: Option[(String, String)]): Map[String, Object] =
    (c.periodPoint, period) match {
      case (None, None) => Map.empty
      case (None, Some(_)) => throw new IllegalArgumentException(s"${c.root.getId} declares no Measurement Period")
      case (Some(_), None) =>
        throw new IllegalArgumentException(s"${c.root.getId} declares a Measurement Period; give its bounds")
      case (Some(point), Some((low, high))) =>
        val interval: Object = new Interval(bound(point, low), true, bound(point, high), true)
        (("Measurement Period" -> interval) +: c.libraries.map(l => s"$l.Measurement Period" -> interval)).toMap
    }

  /** An ISO bound in the declared point type. A DateTime without an offset is UTC, as conformance runs. */
  private def bound(point: String, iso: String): Object =
    try point match {
      case "Date" => java.time.LocalDate.parse(iso.take(10)); new Date(iso.take(10))
      case _ =>
        val odt = scala.util.Try(OffsetDateTime.parse(iso)).getOrElse(LocalDateTime.parse(iso).atOffset(ZoneOffset.UTC))
        new DateTime(odt)
    } catch { case e: java.time.format.DateTimeParseException =>
      throw new IllegalArgumentException(s"period bound '$iso' is not an ISO ${point.toLowerCase.replace("datetime", "date-time")}: ${e.getMessage}")
    }

  private def sha256(s: String): String =
    MessageDigest.getInstance("SHA-256").digest(s.getBytes("UTF-8")).map("%02x".format(_)).mkString
}

object CqfEvaluator {
  /** One translated closure: its library manager, root, Patient-context defines, library names, the root's
    * Measurement Period point type, and what it retrieves. */
  private final case class Closure(manager: LibraryManager, root: VersionedIdentifier, defines: Seq[String],
                                   libraries: Seq[String], periodPoint: Option[String],
                                   requirements: CqfRequirements)
}
