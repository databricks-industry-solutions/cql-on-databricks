package com.databricks.cql.service

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.nio.file.{Files, Paths}
import scala.jdk.CollectionConverters._

/** END-TO-END for the caller-resolved CQL closure (`BUILD-COMPILE-LINK` + `BUILD-LIBRARY-PROVIDER`):
  * a multi-library measure, shipped exactly as the Python `FileLibraryProvider` ships it — root CQL plus a
  * `deps` array of `{cql}` objects, no ELM anywhere — compiles to a SQL script.
  *
  * This is the gate the whole library-source seam exists to pass. Before it, a multi-library measure was
  * unreachable from the Python client at all: `CompileRequest.to_json` sent no `deps`, so the only way to
  * compile one was to hand-list its closure in Scala.
  *
  * It runs every measure in the conformance corpus (`CONF-MEASURE-DISCOVERY`: each `data/measures/<library>/`
  * with an `answer-key.json`), reading the CQL from disk rather than a copy, so the test moves with the
  * corpus. Each measure's closure is the one the translator resolves, FHIRHelpers included. */
@RunWith(classOf[JUnitRunner])
class ClientClosureE2ESpec extends AnyFunSuite {
  import ClientClosureE2ESpec.Measure


  /** Anchored on a marker rather than a fixed number of `..` hops. The hard-coded `../conformance`
    * silently resolved to `service/conformance` the moment this project moved from `service/` to
    * `service/compiler/`, and every test here then skipped rather than failed — a green run that proved
    * nothing. Walk up to the repository root and descend from there. */
  private val repoRoot: java.nio.file.Path = {
    var dir = Paths.get("").toAbsolutePath
    while (dir != null && !Files.isRegularFile(dir.resolve("settings.gradle"))) dir = dir.getParent
    require(dir != null, "repository root not found (no settings.gradle above the working directory)")
    dir
  }

  private val resources = repoRoot.resolve("compiler/conformance/src/test/resources")
  private val measureDir = resources.resolve("cql/measures")
  private val measuresDataDir = resources.resolve("data/measures")

  private def read(name: String): String =
    new String(Files.readAllBytes(measureDir.resolve(name + ".cql")), "UTF-8")

  // The corpus is part of this repository, so its absence is a broken checkout, not a reason to skip.
  private val measures: Seq[Measure] = {
    assert(Files.isDirectory(measuresDataDir), s"conformance corpus not present at $measuresDataDir")
    Files.list(measuresDataDir).iterator().asScala.toSeq.sortBy(_.getFileName.toString)
      .map(_.resolve("answer-key.json")).filter(Files.isRegularFile(_)).map { key =>
        val json = ujson.read(new String(Files.readAllBytes(key), "UTF-8"))
        Measure(json("measure").str, json("measurementPeriod").str, json("populations").obj.keys.toSeq)
      }
  }
  assert(measures.nonEmpty, s"no measure under $measuresDataDir")

  /** The library names the translator resolves for `library`, FHIRHelpers among them: it is shipped like any
    * included library (LANG-FHIRHELPERS-FROM-CONTENT). */
  private def closure(library: String): Seq[String] = {
    val candidates = Files.list(measureDir).iterator().asScala.map(_.getFileName.toString)
      .filter(_.endsWith(".cql")).map(_.stripSuffix(".cql")).filterNot(_ == library).toSeq.sorted
    val result = com.databricks.cql.translate.CqlToElmCli.translateClosure(read(library), candidates.map(read))
    assert(result.root.errors.isEmpty, result.root.errors.mkString("; "))
    result.dependencies.map(e => ujson.read(e)("library")("identifier")("id").str)
  }

  measures.foreach { m =>
    val target = m.populations.last

    test(s"${m.library} compiles from a CQL-only closure, exactly as the Python provider ships it") {
      val deps = closure(m.library)
      assert(deps.nonEmpty, s"${m.library} includes no library, so it proves nothing about a closure")
      val res = CompileService.compileSql(
        CompileRequest(cql = Some(read(m.library)), depCql = deps.map(read), target = Some(target),
          parameters = Map("Measurement Period" -> m.period)),
        hasTerminology = true)

      assert(res.statements.nonEmpty, "a measure must lower to statements")
      // `target` is the SQL object the script ends on: the requested define's statement.
      assert(res.defines.contains(target), s"$target is not among the lowered defines ${res.defines}")
      assert(res.statements.exists(_.name == res.target), s"no statement named the target '${res.target}'")
      // The target's OWN dependency defines lowered, so the measure's real logic came through rather than a
      // target that resolved to a constant. (The compile prunes to the target's subgraph.)
      assert(res.statements.size > 1, s"only the target lowered: ${res.statements.map(_.name)}")
    }

    test(s"${m.library} without its closure fails LOUDLY rather than compiling something wrong") {
      val err = intercept[IllegalArgumentException] {
        CompileService.compileSql(CompileRequest(cql = Some(read(m.library)), target = Some(target),
          parameters = Map("Measurement Period" -> m.period)), hasTerminology = true)
      }
      assert(err.getMessage.contains("translation produced errors"),
        s"expected a loud translation failure, got: ${err.getMessage}")
    }
  }
}

object ClientClosureE2ESpec {
  /** One measure as its answer key states it: library, measurement period and population defines. */
  final case class Measure(library: String, period: String, populations: Seq[String])
}
